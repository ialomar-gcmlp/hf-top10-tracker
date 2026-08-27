---
name: hf-top10-tracker
description: Produce the weekly paste-into-Excel snapshot of top 10 holdings and % of portfolio for GCM's tracked hedge fund managers, sourced from SEC EDGAR 13F filings and cross-checked against DealCloud ARS Research Notes for anything more current. Triggers on "run the top 10 tracker", "weekly hedge fund holdings", "update the top 10 snapshot", "what are D1/Coatue/Tiger Global's top holdings", or a scheduled weekly invocation. Handles managers that file no 13F, notes that are image-only, and research notes that contain attribution rather than position weights.
compatibility: Requires the GCM DealCloud MCP server for research notes, and outbound HTTPS to sec.gov (no API key needed; set EDGAR_USER_AGENT per SEC fair-access policy). Python 3.9+; openpyxl only for the .xlsx output. No WhaleWisdom subscription required.
---

# Hedge Fund Top 10 Tracker

Weekly top-10 holdings snapshot for the 12 managers in `references/fund_mapping.json`,
delivered as tab-separated blocks for pasting into Excel plus a `.csv`/`.xlsx`.

## Non-negotiable rules

1. **Never invent a position or a percentage.** If a number is not in a source,
   the cell reads `Not found` and the block states why. This runs unattended: a
   plausible fabricated weight is far worse than a blank cell.
2. **Never scrape a data vendor.** SEC EDGAR is the primary source; it is the
   authoritative origin that aggregator sites repackage, it is free, and the SEC
   permits automated access with a declared User-Agent under 10 req/s. Do not
   crawl WhaleWisdom, 13f.info, aum13f.com or similar — their terms prohibit
   automated collection and redistribution. `scripts/whalewisdom_client.py` is
   included for the official WhaleWisdom API only, and refuses to run without
   keys rather than falling back to scraping.
3. **Every row is traceable** to a source and an as-of date, printed above the table.
4. **A transient fetch failure is not "no 13F."** EDGAR 503s are retried; if it
   still fails, report the error, never record the manager as a non-filer.

## Prerequisites

```bash
export EDGAR_USER_AGENT="GCM Grosvenor <your.name>@gcmlp.com"   # SEC fair-access policy
```
If unset a generic UA is used, which SEC currently accepts but may throttle.
The DealCloud MCP tools must be authorised in the session.

## Weekly run

### 1. 13F half — one command, fully automatic

```bash
cd ~/.claude/skills/hf-top10-tracker
python scripts/run_edgar.py
```

Reads `references/fund_mapping.json`, and for each fund does a cheap
latest-filing metadata check first. If the period of report matches
`last_quarter_seen`, it reuses cached holdings and skips the information-table
download. Writes `cache/manifest_skeleton.json`.

Expect **no change on most weeks** — 13Fs are quarterly, filed ~45 days after
quarter end (mid Feb/May/Aug/Nov). An unchanged 13F is correct, not a bug.
Real movement only appears in the week or so after each deadline.

Flags: `--force` (ignore cache), `--only "Coatue,Tiger"` (subset),
`--no-write` (don't update the mapping file).

### 2. DealCloud half — always re-check, notes change any day

For each fund, read `dealcloud.entry_id` from the mapping and fetch the pointer
field rather than querying the whole `ResearchNote` table. One batched call
covers all 12:

```
query_dealcloud_entities(
  entity_type="PubInvestmentManager",
  query={"$or": [{"EntryId": 332808}, {"EntryId": 332064}, ...]},
  fields=["EntryId","Name","DateofLastResearchNote","DateofLastResearchNote2"])
```

`DateofLastResearchNote` carries the latest note's `EntryId` + `Name`;
`DateofLastResearchNote2` is its date. Write these into `cache/note_index.json`
keyed by fund name (`note_id`, `note_date`, `note_subject`).

Then fetch each note body:

```
get_dealcloud_entity_by_id(entity_type="ResearchNote", entry_id=<note_id>,
                           fields=["Subject","AsOfDate","Body"])
```

Then harvest and parse — this pulls the bodies out of the persisted tool results
so they never need re-transcribing:

```bash
python scripts/harvest_notes.py \
  --transcript "<path to session .jsonl>" \
  --session-dir "<path to session tool-results dir>"
```

Writes `cache/notes_parsed.json`.

### 3. Merge, then render

```bash
python scripts/merge_manifest.py
python scripts/build_report.py --manifest cache/manifest.json --outdir output
```

`build_report.py` prints the tab-separated blocks and writes
`output/hf_top10_<date>.csv` and `.xlsx` (one sheet per fund plus an "All Funds"
sheet; per-fund sheets store true numeric percentages so Excel can compute on them).

## Freshness logic

Compare the 13F **period of report** (the date holdings are true as of — not the
filing date) against the DealCloud as-of date.

- Prefer DealCloud **only when it is genuinely newer AND the note actually
  yielded a parseable weights table.** A fresher source with no usable data never
  displaces an older source that has data.
- When a weights table states its own date ("Top Positions as of 7/31/2026"),
  that beats the note's `AsOfDate` (when it was written).
- Print which source won, and its as-of, above every table.

## What the note parser will and will not accept

`scripts/dealcloud_note_parser.py` is deliberately conservative. It accepts an
HTML table with a weight-ish header, a heading like "Top Positions" followed by
`Name 9.7%` list items, or repeated `Name (TICKER) 9.7%` prose. Everything else
returns `found: false` with a specific reason.

Four traps it is tested against (`python tests/test_note_parser.py`), each seen
in live notes and reproduced as a synthetic fixture:

| Trap | Shape | Why it matters |
|---|---|---|
| Image-only note | Dozens of `<img src="">`, no tables | Slide screenshots aren't retrievable via the API; there is no text to parse |
| Performance prose | Many percentages, none a weight | "returned 19.9% net" is not a position size |
| Attribution list | A real "Top Positions" list AND "Top Contributors"/"Top Detractors" lists of identical shape in the same note | Picking the wrong one reports P&L as portfolio weight. Headings disambiguate; a leading +/- marks attribution |
| Basis points | "<name> +96 bps" under Winners/Losers | Same trap, different unit |

Only a minority of notes yield a usable table in practice — roughly one in six.
That is expected; most are meeting writeups, not holdings reports.

Test fixtures are **synthetic** (`tests/make_fixtures.py`), using invented
managers and securities. Real note text is never committed. If a local
gitignored `cache/notes/` exists, the suite adds a smoke pass over it.

## 13F reality to keep in mind

- Long US-listed equity only: **no shorts, no non-US listings, no cash**, and
  the report header says so. For managers with big private/short/non-US books
  (D1, Tiger Global, Coatue, Aspex) the weights overstate true portfolio weight.
- Private positions **can** appear when held via a reportable structure — D1's
  13F is 61.9% SpaceX. Do not treat a 13F as purely public.
- PUT/CALL rows are excluded by default (options, not long stock).
- Percentages are computed as value ÷ total value, so the 2023 change from
  reporting thousands to whole dollars does not affect them.
- 13F carries CUSIP, never a ticker. `scripts/ticker_map.py` fills a ticker only
  on an exact normalised name match against SEC `company_tickers.json`, and
  leaves it blank otherwise — about half fill. It deliberately returns blank for
  ambiguous names (e.g. "Alphabet" → GOOGL/GOOG collide).
- A manager with no 13F is a **normal, reportable outcome**, not an error to retry.
- A 13F whose period is older than the last passed deadline is flagged stale and
  must not be presented as current.

## Fund-specific quirks

See `references/fund_mapping.md` for the full list. The two that bite hardest:

- **BlackRock Strategic** has no usable 13F and is DealCloud-only by design.
  EDGAR's "BlackRock" match is BLACKROCK ADVISORS LLC, last 13F **2016-12-31**.
  Without the staleness guard this silently emits decade-old data.
- **Aspex** files as "Aspex Management (HK) Ltd"; search the short token `Aspex`.

If a name search is ambiguous, surface the candidates and ask **once**, then
persist the choice in `references/fund_mapping.json`. Never guess between two
plausible manager records.

## Confidentiality

Research notes carry short positions, named GCM staff, named manager contacts,
and manager-confidential commentary; some are explicitly marked not for external
disclosure. This report covers long top-10 weights only.

- Do not copy short positions or verbatim note commentary into the output.
- Do not commit note bodies, parsed holdings, or finished reports. `cache/` and
  `output/` are gitignored for this reason; the skill regenerates them each run.

## Scheduling

Claude Code has no built-in weekly trigger. Run it from an external scheduler —
Windows Task Scheduler, cron, or a GitHub Actions workflow — invoking:

```bash
claude -p "Run the hf-top10-tracker weekly snapshot" --allowedTools ...
```

Once `references/fund_mapping.json` is populated (it is), a run needs no
interactive input. Best timing: **weekly, with the useful run mid-Feb / mid-May /
mid-Aug / mid-Nov**, just after each 13F deadline.
