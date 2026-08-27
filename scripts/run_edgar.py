#!/usr/bin/env python3
"""
Pull the 13F half of the weekly run for every fund in references/fund_mapping.json.

Cheap-check first: ask EDGAR only for the latest filing metadata (one small JSON
per fund). If the period of report matches what we already cached, reuse the
cached holdings and skip the information-table download entirely. 13Fs change
four times a year, so most weekly runs do no real work here.

Writes:
  cache/holdings_cache.json    per-CIK cached top 10 keyed by period
  cache/manifest_skeleton.json the 13F half of the report manifest
  references/fund_mapping.json last_quarter_seen written back (unless --no-write)
"""

import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from edgar_13f_client import (  # noqa: E402
    EdgarError,
    NoFilerFound,
    expected_latest_period,
    latest_13f,
    top_holdings,
)
from ticker_map import enrich  # noqa: E402

ROOT = os.path.dirname(HERE)
MAPPING = os.path.join(ROOT, "references", "fund_mapping.json")
CACHE_DIR = os.path.join(ROOT, "cache")
HOLDINGS_CACHE = os.path.join(CACHE_DIR, "holdings_cache.json")
SKELETON = os.path.join(CACHE_DIR, "manifest_skeleton.json")

LONG_BOOK_CAVEAT = (
    "13F long US-listed equity book only; excludes shorts, non-US listings, "
    "privates and cash."
)


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2)


def process(entry, cache, force):
    """Return (manifest_block, status_line)."""
    fund = entry["fund"]
    edgar = entry.get("edgar") or {}
    base_caveat = " ".join(x for x in [LONG_BOOK_CAVEAT, entry.get("caveat") or ""] if x).strip()

    if not edgar.get("use") or not edgar.get("cik"):
        return (
            {
                "fund": fund,
                "source": None,
                "as_of": None,
                "detail": None,
                "caveat": entry.get("caveat"),
                "not_found_reason": edgar.get("note")
                or "No 13F source configured for this fund.",
                "holdings": [],
                "_13f_status": "not_applicable",
            },
            f"{fund}: 13F not applicable ({(edgar.get('note') or '')[:60]}...)",
        )

    cik = edgar["cik"]
    try:
        latest = latest_13f(cik)
    except NoFilerFound as e:
        return (
            {
                "fund": fund,
                "source": None,
                "as_of": None,
                "detail": None,
                "caveat": entry.get("caveat"),
                "not_found_reason": f"No 13F on file with the SEC. {e}",
                "holdings": [],
                "_13f_status": "no_filer",
            },
            f"{fund}: NO 13F FILER (normal outcome, not an error)",
        )
    except EdgarError as e:
        return (
            {
                "fund": fund,
                "source": None,
                "as_of": None,
                "detail": None,
                "caveat": entry.get("caveat"),
                "not_found_reason": f"13F fetch failed (transient): {e}",
                "holdings": [],
                "_13f_status": "error",
            },
            f"{fund}: EDGAR ERROR {str(e)[:70]}",
        )

    period = latest["report_date"]
    cached = cache.get(cik)
    reused = False
    if not force and cached and cached.get("report_date") == period:
        data = cached
        reused = True
    else:
        try:
            data = top_holdings(cik, limit=10)
        except EdgarError as e:
            return (
                {
                    "fund": fund,
                    "source": None,
                    "as_of": None,
                    "detail": None,
                    "caveat": entry.get("caveat"),
                    "not_found_reason": f"13F information table unreadable: {e}",
                    "holdings": [],
                    "_13f_status": "error",
                },
                f"{fund}: PARSE ERROR {str(e)[:70]}",
            )
        enrich(data["holdings"])
        cache[cik] = data

    expected = expected_latest_period()
    stale = bool(expected and period < expected)
    detail = (
        f"{latest['filer_name']} (CIK {cik}), {latest['form']} for period {period}, "
        f"filed {latest['filing_date']}, accession {latest['accession']}, "
        f"{data.get('positions_in_filing', '?')} positions"
    )
    caveat = base_caveat
    reason = None
    if stale:
        reason = (
            f"13F is STALE: latest period is {period} but {expected} is already past its "
            f"45-day deadline. Treat these weights as historical, not current."
        )

    return (
        {
            "fund": fund,
            "source": "SEC EDGAR 13F",
            "as_of": period,
            "detail": detail,
            "caveat": caveat,
            "not_found_reason": reason,
            "holdings": data["holdings"],
            "_13f_status": "stale" if stale else "ok",
            "_13f_filing_date": latest["filing_date"],
            "_13f_reused_cache": reused,
        },
        f"{fund}: {period} ({'cached' if reused else 'fetched'})"
        + (f" [{len(data['holdings'])} rows]" if data.get("holdings") else "")
        + (" STALE" if stale else ""),
    )


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--force", action="store_true", help="Re-fetch even if the quarter is unchanged")
    p.add_argument("--no-write", action="store_true", help="Do not update fund_mapping.json")
    p.add_argument("--only", help="Comma-separated fund names to run (substring match)")
    a = p.parse_args()

    mapping = load_json(MAPPING, None)
    if mapping is None:
        print(f"Missing {MAPPING}", file=sys.stderr)
        sys.exit(1)
    cache = load_json(HOLDINGS_CACHE, {})

    entries = mapping["funds"]
    if a.only:
        wanted = [s.strip().lower() for s in a.only.split(",") if s.strip()]
        entries = [e for e in entries if any(w in e["fund"].lower() for w in wanted)]

    blocks = []
    print(f"Expected latest 13F period: {expected_latest_period()}\n", file=sys.stderr)
    for entry in entries:
        block, status = process(entry, cache, a.force)
        blocks.append(block)
        print("  " + status, file=sys.stderr)
        if block.get("as_of"):
            entry["last_quarter_seen"] = block["as_of"]

    save_json(HOLDINGS_CACHE, cache)
    save_json(
        SKELETON,
        {"generated": datetime.date.today().isoformat(), "funds": blocks},
    )
    if not a.no_write:
        save_json(MAPPING, mapping)

    ok = sum(1 for b in blocks if b["_13f_status"] == "ok")
    print(
        f"\n{ok}/{len(blocks)} funds with current 13F data. Skeleton: {SKELETON}",
        file=sys.stderr,
    )
    print(json.dumps({"generated": datetime.date.today().isoformat(), "funds": blocks}, indent=2))


if __name__ == "__main__":
    main()
