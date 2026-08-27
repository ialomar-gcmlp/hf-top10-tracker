#!/usr/bin/env python3
"""
WhaleWisdom API client (official API only -- never scrapes the website).

WhaleWisdom Terms of Use prohibit crawling the site with automated scripting
tools and prohibit bypassing the API. This client therefore talks ONLY to the
documented endpoint https://whalewisdom.com/shell/command.json using digital
signature authentication.

Docs: https://whalewisdom.com/help/api  and  https://whalewisdom.com/shell/api_help

Auth (per the docs): api_sig = base64( HMAC-SHA1( secret_key,
args + "\\n" + timestamp ) ), where args and timestamp are the *pre-URL-encoded*
values. Timestamp is ISO-8601 UTC, e.g. 2011-06-01T13:00:01Z.

Rate limit: 20 requests/minute. We self-throttle to one request per 3.1s.
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API_URL = "https://whalewisdom.com/shell/command.json"
MIN_SECONDS_BETWEEN_CALLS = 3.1  # 20 req/min ceiling, with headroom
_last_call_at = [0.0]

# Column IDs for the `holdings` command (see references/fund_mapping.md).
HOLDINGS_COLUMNS = [0, 1, 2, 3, 4, 5, 8, 10, 16, 17, 27]


class CredentialsMissing(RuntimeError):
    pass


class WhaleWisdomError(RuntimeError):
    pass


def get_credentials():
    """Return (shared_key, secret_key) or raise CredentialsMissing.

    Hard constraint: we never fall back to scraping when creds are absent.
    """
    shared = os.environ.get("WHALEWISDOM_API_KEY", "").strip()
    secret = os.environ.get("WHALEWISDOM_SECRET_KEY", "").strip()
    if not shared or not secret:
        missing = [
            n
            for n, v in (
                ("WHALEWISDOM_API_KEY", shared),
                ("WHALEWISDOM_SECRET_KEY", secret),
            )
            if not v
        ]
        raise CredentialsMissing(
            "Missing WhaleWisdom API credentials: "
            + ", ".join(missing)
            + ".\nThese are created under Profile -> API on a WhaleWisdom account that has "
            "API access (a paid subscription -- a plain browser login is NOT enough).\n"
            "This skill will NOT scrape whalewisdom.com as a fallback: their Terms of Use "
            "prohibit crawling the site with automated tools and bypassing the API."
        )
    return shared, secret


def sign(args_json, secret_key, timestamp):
    digest = hmac.new(
        secret_key.encode("utf-8"),
        (args_json + "\n" + timestamp).encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode("ascii").strip()


def call(command, **params):
    """Issue one signed API call and return the decoded JSON payload."""
    shared, secret = get_credentials()

    args = {"command": command}
    args.update({k: v for k, v in params.items() if v is not None})
    # Compact separators keep the signed string identical to what we send.
    args_json = json.dumps(args, separators=(",", ":"), sort_keys=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    api_sig = sign(args_json, secret, timestamp)

    query = urllib.parse.urlencode(
        {
            "args": args_json,
            "api_shared_key": shared,
            "api_sig": api_sig,
            "timestamp": timestamp,
        }
    )
    url = API_URL + "?" + query

    # Self-throttle to stay inside 20 req/min.
    wait = MIN_SECONDS_BETWEEN_CALLS - (time.time() - _last_call_at[0])
    if wait > 0:
        time.sleep(wait)
    _last_call_at[0] = time.time()

    req = urllib.request.Request(url, headers={"User-Agent": "gcm-hf-top10-tracker/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        if e.code in (401, 403):
            raise WhaleWisdomError(
                f"WhaleWisdom rejected the request ({e.code}). Usual causes: the account "
                f"lacks API access, or the keys are wrong. Response: {detail}"
            ) from e
        raise WhaleWisdomError(f"HTTP {e.code} from WhaleWisdom: {detail}") from e

    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise WhaleWisdomError(f"Non-JSON response from WhaleWisdom: {body[:500]}") from e


# --------------------------------------------------------------------------
# Response normalisation
#
# The docs specify request params precisely but not the exact response shape,
# and it differs a little between commands. Rather than assume one layout we
# search the payload for the first list of dicts that looks like the records we
# asked for. If nothing matches we raise -- we never return a half-parsed row,
# because a plausible-looking wrong number is worse than an error here.
# --------------------------------------------------------------------------


def _iter_record_lists(node):
    if isinstance(node, list):
        if node and all(isinstance(x, dict) for x in node):
            yield node
        for item in node:
            yield from _iter_record_lists(item)
    elif isinstance(node, dict):
        for value in node.values():
            yield from _iter_record_lists(value)


def _find_records(payload, required_keys):
    """First list-of-dicts whose rows carry any of `required_keys`."""
    for candidate in _iter_record_lists(payload):
        keys = {k.lower() for k in candidate[0].keys()}
        if any(rk in keys for rk in required_keys):
            return candidate
    return None


def _get(row, *names, default=None):
    lowered = {k.lower(): v for k, v in row.items()}
    for n in names:
        if n in lowered and lowered[n] not in (None, ""):
            return lowered[n]
    return default


def quarters():
    payload = call("quarters")
    rows = _find_records(payload, {"quarter", "filing_period", "id"})
    if rows is None:
        raise WhaleWisdomError(
            f"Could not locate quarter records in response: {str(payload)[:400]}"
        )
    out = []
    for r in rows:
        qid = _get(r, "id", "quarter_id")
        qdate = _get(r, "quarter", "filing_period", "quarter_date", "date")
        if qid is None or qdate is None:
            continue
        out.append({"quarter_id": int(qid), "quarter_date": str(qdate)[:10]})
    out.sort(key=lambda x: x["quarter_date"])
    if not out:
        raise WhaleWisdomError("WhaleWisdom returned no quarters.")
    return out


def latest_quarter():
    return quarters()[-1]


def filer_lookup(name):
    payload = call("filer_lookup", name=name)
    rows = _find_records(payload, {"name", "filer_name"})
    if rows is None:
        return []
    out = []
    for r in rows:
        fid = _get(r, "id", "filer_id")
        fname = _get(r, "name", "filer_name")
        if fid is None or not fname:
            continue
        out.append(
            {
                "filer_id": int(fid),
                "filer_name": str(fname),
                "cik": _get(r, "cik", default=""),
                "city": _get(r, "city", default=""),
                "state": _get(r, "state", default=""),
            }
        )
    return out


def _to_pct(value):
    """Coerce a percent-of-portfolio value to float, or None. Never guesses."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("%", "").replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def top_holdings(filer_id, quarter_id=None, limit=10):
    """Top `limit` 13F long positions by current % of portfolio."""
    payload = call(
        "holdings",
        filer_ids=[int(filer_id)],
        quarter_ids=[int(quarter_id)] if quarter_id else None,
        columns=HOLDINGS_COLUMNS,
        include_13d=0,
    )
    rows = _find_records(
        payload,
        {"current_percent_of_portfolio", "percent_of_portfolio", "stock_ticker"},
    )
    if rows is None:
        raise WhaleWisdomError(
            "Could not locate holdings records in the WhaleWisdom response. "
            f"Top-level keys: {list(payload)[:10] if isinstance(payload, dict) else type(payload)}"
        )

    holdings, filer_name, source_date = [], None, None
    for r in rows:
        pct = _to_pct(_get(r, "current_percent_of_portfolio", "percent_of_portfolio"))
        name = _get(r, "stock_name", "name", "security_name")
        if name is None:
            continue
        filer_name = filer_name or _get(r, "filer_name")
        source_date = source_date or _get(r, "source_date")
        rank = _get(r, "current_ranking", "ranking", "current_rank")
        holdings.append(
            {
                "stock_name": str(name),
                "ticker": (_get(r, "stock_ticker", "ticker", default="") or "").strip(),
                "security_type": _get(r, "security_type", default=""),
                "pct_of_portfolio": pct,
                "rank": int(rank) if str(rank).strip().isdigit() else None,
            }
        )

    # Sort by % of portfolio descending; rows with no parseable % sink to the
    # bottom rather than being silently dropped or invented.
    holdings.sort(
        key=lambda h: (h["pct_of_portfolio"] is None, -(h["pct_of_portfolio"] or 0.0))
    )
    return {
        "filer_id": int(filer_id),
        "filer_name": filer_name,
        "quarter_id": quarter_id,
        "source_date": str(source_date)[:10] if source_date else None,
        "total_positions": len(holdings),
        "holdings": holdings[:limit],
    }


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check-credentials")
    q = sub.add_parser("quarters")
    q.add_argument("--latest", action="store_true")
    f = sub.add_parser("filer-lookup")
    f.add_argument("--name", required=True)
    t = sub.add_parser("top10")
    t.add_argument("--filer-id", required=True, type=int)
    t.add_argument("--quarter-id", type=int)
    t.add_argument("--limit", type=int, default=10)
    a = p.parse_args()

    try:
        if a.cmd == "check-credentials":
            get_credentials()
            print(json.dumps({"credentials": "present"}))
        elif a.cmd == "quarters":
            print(json.dumps(latest_quarter() if a.latest else quarters(), indent=2))
        elif a.cmd == "filer-lookup":
            print(json.dumps(filer_lookup(a.name), indent=2))
        elif a.cmd == "top10":
            print(json.dumps(top_holdings(a.filer_id, a.quarter_id, a.limit), indent=2))
    except CredentialsMissing as e:
        print(json.dumps({"error": "credentials_missing", "message": str(e)}, indent=2))
        sys.exit(2)
    except WhaleWisdomError as e:
        print(json.dumps({"error": "whalewisdom_error", "message": str(e)}, indent=2))
        sys.exit(3)


if __name__ == "__main__":
    main()
