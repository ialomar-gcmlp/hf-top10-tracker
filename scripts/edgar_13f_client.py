#!/usr/bin/env python3
"""
SEC EDGAR 13F client -- the PRIMARY 13F source for this skill.

EDGAR is the authoritative origin of 13F data (aggregator sites repackage it),
it is free, needs no credentials, and the SEC explicitly permits automated
access provided you (a) declare a User-Agent identifying you with a contact
address and (b) stay under 10 requests/second. We throttle well inside that.

Set EDGAR_USER_AGENT to "Your Org your.name@yourdomain.com" per SEC policy:
https://www.sec.gov/os/webmaster-faq#developers

Endpoints used (all public, all documented):
  browse-edgar atom     -> filer name search, returns CIK + conformed name
  data.sec.gov          -> submissions JSON, gives form/reportDate/accession
  www.sec.gov/Archives  -> the filing INFORMATION TABLE XML

13F reality this client encodes:
  * Filed quarterly, ~45 days after quarter end (mid Feb/May/Aug/Nov).
  * Long US-listed equity only. No shorts, no non-US listings, no privates,
    no cash. Percent-of-portfolio is therefore a share of the 13F-reportable
    long book, NOT of total AUM.
  * PUT/CALL rows are options, not long stock. Excluded by default.
  * Percentages are computed as value / total value, which is scale-invariant,
    so the 2023 whole-dollars-vs-thousands reporting change does not matter.
"""

import argparse
import datetime
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

DEFAULT_UA = "GCM Grosvenor hf-top10-tracker admin@gcmlp.com"
MIN_GAP = 0.15  # SEC allows 10 req/s; this is ~6.7 req/s
_last = [0.0]


class EdgarError(RuntimeError):
    pass


class NoFilerFound(EdgarError):
    """Normal, reportable outcome -- many managers simply do not file a 13F."""


def user_agent():
    return os.environ.get("EDGAR_USER_AGENT", "").strip() or DEFAULT_UA


def _fetch(url, accept="application/json", attempts=4):
    """GET with throttling and backoff.

    EDGAR answers a burst of odd queries with 503. This runs unattended weekly,
    so a transient 503 must not look like "manager has no 13F" -- we retry, then
    surface a clear transient error.
    """
    last_error = None
    for attempt in range(attempts):
        gap = MIN_GAP - (time.time() - _last[0])
        if gap > 0:
            time.sleep(gap)
        _last[0] = time.time()
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": user_agent(),
                "Accept-Encoding": "gzip, deflate",
                "Accept": accept,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise NoFilerFound(f"EDGAR returned 404 for {url}") from e
            if e.code in (403, 429, 500, 502, 503, 504):
                last_error = f"HTTP {e.code}"
                time.sleep(1.5 * (2**attempt))
                continue
            raise EdgarError(f"HTTP {e.code} from EDGAR for {url}") from e
        except urllib.error.URLError as e:
            last_error = str(e.reason)
            time.sleep(1.5 * (2**attempt))
            continue
    raise EdgarError(
        f"EDGAR did not respond after {attempts} attempts ({last_error}) for {url}. "
        "This is a transient network/throttling condition, NOT evidence that the "
        "manager has no 13F -- do not record it as such."
    )


def expected_latest_period(today=None):
    """Most recent quarter-end whose 45-day 13F deadline has already passed."""
    today = today or datetime.date.today()
    ends = []
    for year in (today.year - 1, today.year):
        for month, day in ((3, 31), (6, 30), (9, 30), (12, 31)):
            ends.append(datetime.date(year, month, day))
    due = [e for e in ends if e + datetime.timedelta(days=45) <= today]
    return max(due).isoformat() if due else None


def filer_lookup(name):
    """Search EDGAR for entities that have filed a 13F-HR under `name`."""
    url = (
        "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company="
        + urllib.parse.quote(name)
        + "&type=13F-HR&dateb=&owner=include&count=40&output=atom"
    )
    body = _fetch(url, accept="application/atom+xml")

    # Single exact hit -> EDGAR returns one <company-info>. Multiple hits ->
    # a list of <entry> rows instead. Handle both.
    out = []
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise EdgarError(f"Could not parse EDGAR atom feed: {e}") from e

    def _txt(node, tag):
        for el in node.iter():
            if el.tag.split("}")[-1] == tag and el.text:
                return el.text.strip()
        return None

    ci = None
    for el in root.iter():
        if el.tag.split("}")[-1] == "company-info":
            ci = el
            break
    if ci is not None and _txt(ci, "cik"):
        out.append(
            {
                "cik": _txt(ci, "cik").lstrip("0").zfill(10),
                "name": _txt(ci, "conformed-name") or name,
                "state": _txt(ci, "state-location") or "",
            }
        )

    for el in root.iter():
        if el.tag.split("}")[-1] != "entry":
            continue
        cik = _txt(el, "CIK") or _txt(el, "cik")
        nm = _txt(el, "company-name") or _txt(el, "conformed-name")
        if cik and nm:
            rec = {"cik": cik.lstrip("0").zfill(10), "name": nm, "state": ""}
            if rec["cik"] not in {o["cik"] for o in out}:
                out.append(rec)

    if not out:
        raise NoFilerFound(
            f"No SEC 13F-HR filer matches '{name}'. This is a normal outcome: the "
            "manager may be non-US domiciled, below the $100M 13F threshold, or "
            "filing under a different legal entity name."
        )
    return out


def latest_13f(cik, include_amendments=True):
    """Most recent 13F-HR for a CIK, by period of report."""
    cik10 = str(cik).lstrip("0").zfill(10)
    data = json.loads(_fetch(f"https://data.sec.gov/submissions/CIK{cik10}.json"))
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    wanted = {"13F-HR"} | ({"13F-HR/A"} if include_amendments else set())

    rows = []
    for i, form in enumerate(forms):
        if form not in wanted:
            continue
        rows.append(
            {
                "form": form,
                "accession": recent["accessionNumber"][i],
                "filing_date": recent["filingDate"][i],
                "report_date": recent["reportDate"][i],
            }
        )
    if not rows:
        raise NoFilerFound(
            f"CIK {cik10} ({data.get('name', '?')}) has no 13F-HR filings on EDGAR."
        )
    rows.sort(key=lambda r: (r["report_date"], r["filing_date"]))
    latest = rows[-1]
    latest["cik"] = cik10
    latest["filer_name"] = data.get("name", "")
    return latest


def _information_table_url(cik, accession):
    acc_nodash = accession.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nodash}"
    index = json.loads(_fetch(base + "/index.json"))
    items = index.get("directory", {}).get("item", [])
    candidates = [it["name"] for it in items if it["name"].lower().endswith(".xml")]
    # The information table is the XML that is not the cover page.
    for nm in candidates:
        if "primary_doc" not in nm.lower():
            return base + "/" + nm
    if candidates:
        return base + "/" + candidates[0]
    raise EdgarError(f"No XML information table found in {base}")


def _local(tag):
    return tag.split("}")[-1]


def parse_information_table(xml_text, include_options=False):
    root = ET.fromstring(xml_text)
    rows = []
    for node in root.iter():
        if _local(node.tag) != "infoTable":
            continue
        rec = {}
        for child in node.iter():
            t = _local(child.tag)
            if t in ("nameOfIssuer", "titleOfClass", "cusip", "value", "putCall", "sshPrnamtType"):
                if child.text and child.text.strip():
                    rec[t] = child.text.strip()
            elif t == "sshPrnamt" and child.text:
                rec["shares"] = child.text.strip()
        if "nameOfIssuer" not in rec or "value" not in rec:
            continue
        put_call = rec.get("putCall", "").upper()
        if put_call and not include_options:
            continue
        try:
            value = float(rec["value"].replace(",", ""))
        except ValueError:
            continue
        rows.append(
            {
                "issuer": rec["nameOfIssuer"].strip(),
                "class": rec.get("titleOfClass", "").strip(),
                "cusip": rec.get("cusip", "").strip().upper(),
                "value": value,
                "put_call": put_call,
            }
        )
    return rows


def top_holdings(cik, limit=10, include_options=False, accession=None, report_date=None):
    if accession is None:
        latest = latest_13f(cik)
        accession, report_date = latest["accession"], latest["report_date"]
        filing_date, filer_name = latest["filing_date"], latest["filer_name"]
    else:
        filing_date, filer_name = None, None

    url = _information_table_url(cik, accession)
    rows = parse_information_table(_fetch(url, accept="application/xml"), include_options)
    if not rows:
        raise EdgarError(
            f"13F information table for accession {accession} contained no long "
            "equity rows (it may be a holdings-report-excluded or options-only filing)."
        )

    # Aggregate by security (CUSIP), keeping the issuer label.
    agg = {}
    for r in rows:
        key = r["cusip"] or r["issuer"].upper()
        if key not in agg:
            agg[key] = {"issuer": r["issuer"], "class": r["class"], "cusip": r["cusip"], "value": 0.0}
        agg[key]["value"] += r["value"]

    total = sum(a["value"] for a in agg.values())
    if total <= 0:
        raise EdgarError("13F information table total value was zero; cannot compute weights.")

    ranked = sorted(agg.values(), key=lambda a: -a["value"])
    holdings = []
    for i, a in enumerate(ranked[:limit], start=1):
        label = a["issuer"]
        if a["class"] and a["class"].upper() not in ("COM", "COMMON STOCK", "ORD", "SHS"):
            label = f"{label} {a['class']}"
        holdings.append(
            {
                "position": label,
                "ticker": "",  # 13F reports CUSIP, not ticker -- left blank, never guessed
                "cusip": a["cusip"],
                "pct_of_portfolio": round(100.0 * a["value"] / total, 2),
                "rank": i,
            }
        )

    expected = expected_latest_period()
    stale = bool(expected and report_date and report_date < expected)
    result = {
        "cik": str(cik).lstrip("0").zfill(10),
        "filer_name": filer_name,
        "accession": accession,
        "report_date": report_date,
        "filing_date": filing_date,
        "expected_latest_period": expected,
        "stale": stale,
        "positions_in_filing": len(agg),
        "total_value_reported": total,
        "options_included": include_options,
        "holdings": holdings,
    }
    if stale:
        result["stale_warning"] = (
            f"Latest 13F covers {report_date} but {expected} should already be filed "
            f"(45-day deadline passed). This filer has stopped filing, or the CIK is the "
            f"wrong entity. Do NOT present these holdings as current."
        )
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("filer-lookup"); f.add_argument("--name", required=True)
    l = sub.add_parser("latest"); l.add_argument("--cik", required=True)
    t = sub.add_parser("top10")
    t.add_argument("--cik", required=True)
    t.add_argument("--limit", type=int, default=10)
    t.add_argument("--include-options", action="store_true")
    a = p.parse_args()

    try:
        if a.cmd == "filer-lookup":
            print(json.dumps(filer_lookup(a.name), indent=2))
        elif a.cmd == "latest":
            print(json.dumps(latest_13f(a.cik), indent=2))
        elif a.cmd == "top10":
            print(json.dumps(top_holdings(a.cik, a.limit, a.include_options), indent=2))
    except NoFilerFound as e:
        print(json.dumps({"error": "no_13f_filer", "message": str(e)}, indent=2))
        sys.exit(4)
    except EdgarError as e:
        print(json.dumps({"error": "edgar_error", "message": str(e)}, indent=2))
        sys.exit(3)


if __name__ == "__main__":
    main()
