#!/usr/bin/env python3
"""
Best-effort ticker enrichment for 13F rows.

13F information tables carry CUSIP, never a ticker. This module maps an issuer
NAME to a ticker using the SEC's own company_tickers.json, and does so STRICTLY:
a ticker is filled in only when the normalised 13F issuer name is exactly equal
to a normalised SEC company name AND that normalised name is unique in the file.

Anything else stays blank. A blank ticker is a cosmetic gap; a wrong ticker in a
holdings report is a material error, so this never fuzzy-matches.
"""

import json
import os
import re
import time
import urllib.request

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cache", "company_tickers.json")
SRC = "https://www.sec.gov/files/company_tickers.json"
MAX_AGE_DAYS = 30

# Descriptive tails that appear in 13F issuer strings but not in SEC names.
NOISE = r"""
 sponsored\s+ads?r?|sponsored\s+ads|unsponsored\s+adr|adr|ads
|cap\s+stk|com\s+stk|common\s+stock|com\s+new|com\s+par|com
|class\s+[a-c]|cl\s+[a-c]|ser\s+[a-c]|shs|ord|ordinary\s+shares
|new|inc|incorporated|corp|corporation|company|co|plc|ltd|limited
|lp|llc|holdings?|group|the|sa|nv|ag
"""
NOISE_RE = re.compile(r"\b(?:" + NOISE.replace("\n", "").strip() + r")\b", re.I)


def normalise(name):
    s = (name or "").lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = NOISE_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def _load_source(user_agent):
    path = os.path.abspath(CACHE)
    fresh = os.path.exists(path) and (time.time() - os.path.getmtime(path)) < MAX_AGE_DAYS * 86400
    if not fresh:
        req = urllib.request.Request(SRC, headers={"User-Agent": user_agent})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read().decode("utf-8", errors="replace")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(data)
        except Exception:
            if not os.path.exists(path):
                return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


_index = None


def build_index(user_agent="GCM Grosvenor hf-top10-tracker admin@gcmlp.com"):
    """normalised name -> ticker, dropping any normalised name that collides."""
    global _index
    if _index is not None:
        return _index
    raw = _load_source(user_agent)
    counts, table = {}, {}
    for rec in (raw or {}).values():
        key = normalise(rec.get("title"))
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
        table.setdefault(key, rec.get("ticker", "").strip().upper())
    _index = {k: v for k, v in table.items() if counts[k] == 1 and v}
    return _index


def lookup(issuer_name, user_agent=None):
    idx = build_index(user_agent) if user_agent else build_index()
    return idx.get(normalise(issuer_name), "")


def enrich(holdings, user_agent=None):
    """Fill blank `ticker` fields in place where an exact match exists."""
    for h in holdings:
        if not h.get("ticker"):
            h["ticker"] = lookup(h.get("position", ""), user_agent)
    return holdings


if __name__ == "__main__":
    import sys

    idx = build_index()
    print(f"index entries: {len(idx)}")
    for name in sys.argv[1:]:
        print(f"  {name!r} -> {lookup(name)!r}  (normalised: {normalise(name)!r})")
