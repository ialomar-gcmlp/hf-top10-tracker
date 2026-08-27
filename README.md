# hf-top10-tracker

A [Claude Code](https://claude.com/claude-code) skill that produces a weekly,
paste-into-Excel snapshot of the top 10 holdings and % of portfolio for a fixed
list of hedge fund managers.

Two sources, reconciled by freshness:

- **SEC EDGAR 13F filings** — the authoritative quarterly source, free, no API key.
- **GCM DealCloud ARS Research Notes** — used when a note is genuinely newer
  *and* actually contains a parseable weights table.

> **Internal tooling.** This repository is private. It names GCM's tracked
> manager list and DealCloud record IDs. It contains **no** research note text,
> no parsed holdings and no generated reports — see [Confidentiality](#confidentiality).

## Design principle

The skill runs unattended on a schedule, so **a plausible fabricated number is
worse than a blank cell**. Every rule below follows from that:

- If a number isn't in a source, the cell reads `Not found` and the block says why.
- Every row is traceable to a source and an as-of date, printed above the table.
- A transient fetch failure is never recorded as "this manager has no 13F".
- The note parser is deliberately conservative and returns nothing rather than
  guessing.

## Why EDGAR, not a 13F aggregator

EDGAR is the origin that aggregator sites repackage. It is free, needs no
credentials, and the SEC explicitly permits automated access with a declared
User-Agent under 10 requests/second.

Aggregator terms generally prohibit automated collection and redistribution, so
this project does not crawl them. `scripts/whalewisdom_client.py` is a complete
client for WhaleWisdom's **official API** (HMAC-SHA1 signing, 20 req/min
throttle) and refuses to run without keys rather than falling back to scraping.
It is unused by default.

## Setup

```bash
export EDGAR_USER_AGENT="Your Org your.name@yourdomain.com"   # SEC fair-access policy
```

Python 3.9+. Standard library only; `openpyxl` is needed just for the `.xlsx`
output (CSV and terminal output work without it). The DealCloud MCP server must
be authorised in the Claude Code session for the research-note half.

## Usage

```bash
python scripts/run_edgar.py                 # 13F half, all funds, cache-aware
python scripts/harvest_notes.py --help      # parse DealCloud notes from MCP output
python scripts/merge_manifest.py            # apply the freshness rule
python scripts/build_report.py --manifest cache/manifest.json --outdir output
```

`SKILL.md` documents the full weekly procedure, including the DealCloud MCP calls.

Output is a tab-separated block per fund (pasting tab-separated text into Excel
auto-splits into columns) plus `output/hf_top10_<date>.csv` and `.xlsx` — one
sheet per fund plus an "All Funds" sheet, with true numeric percentages so Excel
can compute on them.

## 13F facts the code encodes

- Long US-listed equity only — no shorts, no non-US listings, no cash. The
  reported "% of portfolio" is a share of the 13F-reportable long book, **not of
  total AUM**, and the report header says so.
- Private positions *can* appear when held through a reportable structure, so a
  13F is not purely public equities.
- Filed quarterly, ~45 days after quarter end. **An unchanged 13F on most weekly
  runs is correct, not a bug** — real movement only appears just after each
  mid-Feb/May/Aug/Nov deadline. `run_edgar.py` does a cheap metadata check first
  and skips the full download when the quarter hasn't changed.
- A filing older than the last passed deadline is flagged **stale** and must not
  be presented as current. This matters: one tracked manager's name search
  resolves to an entity whose last 13F is from 2016.
- PUT/CALL rows are excluded by default.
- Percentages are value ÷ total value, so the 2023 thousands-to-dollars
  reporting change doesn't affect them.
- 13F carries CUSIP, never a ticker. `ticker_map.py` fills one only on an exact
  normalised name match against SEC `company_tickers.json`, and leaves it blank
  otherwise — including for genuinely ambiguous names.

## The note parser

`scripts/dealcloud_note_parser.py` accepts three shapes: an HTML table with a
weight-ish header, a "Top Positions"-style heading followed by `Name 9.7%` list
items, or repeated `Name (TICKER) 9.7%` prose. Anything else returns
`found: false` with a specific reason.

The traps it is tested against:

| Trap | Why it matters |
|---|---|
| Image-only note | Slide screenshots aren't retrievable via the API — no text to parse |
| Performance prose | "returned 19.9% net" is not a position size |
| Attribution list | A note may hold a real "Top Positions" list *and* "Top Contributors"/"Top Detractors" lists of identical shape. Reading the wrong one reports P&L as portfolio weight |
| Basis points | Same trap, different unit |

```bash
python tests/test_note_parser.py
```

## Confidentiality

Research notes carry short positions, named GCM staff, named manager contacts and
manager-confidential commentary; some are explicitly marked not for external
disclosure.

- `cache/` and `output/` are gitignored — note bodies, parsed holdings and
  finished reports never enter version control. The skill regenerates them.
- Test fixtures are **synthetic**, generated by `tests/make_fixtures.py` with
  invented managers and securities. They reproduce the *structure* of each trap
  without any real note text.
- The test suite adds an optional smoke pass over a local `cache/notes/` when one
  exists; it is skipped in a clean clone and never asserts on note contents.

## Scheduling

Claude Code has no built-in weekly trigger. Drive it from an external scheduler
(Windows Task Scheduler, cron, or a scheduled GitHub Actions workflow) invoking
`claude -p`. Once `references/fund_mapping.json` is populated, a run needs no
interactive input.

## Layout

```
SKILL.md                        skill definition and weekly procedure
README.md                       this file
references/fund_mapping.json    fund -> CIK + DealCloud EntryId, cached quarter
references/fund_mapping.md      resolution gotchas and per-fund caveats
scripts/edgar_13f_client.py     SEC EDGAR client (retries, staleness guard)
scripts/run_edgar.py            13F half for every fund, cache-aware
scripts/dealcloud_note_parser.py  conservative weights extraction
scripts/harvest_notes.py        pull note bodies out of persisted MCP results
scripts/merge_manifest.py       freshness rule
scripts/build_report.py         TSV + CSV + XLSX
scripts/ticker_map.py           strict CUSIP-gap ticker fill
scripts/whalewisdom_client.py   official WhaleWisdom API client (unused)
tests/                          synthetic fixtures + regression suite
```
