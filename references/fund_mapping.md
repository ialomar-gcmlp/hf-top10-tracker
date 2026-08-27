# Fund mapping and known gotchas

Machine-readable companion: `fund_mapping.json`. All resolutions below were made
against **live SEC EDGAR and GCM DealCloud on 2026-08-27** and confirmed by the
user where ambiguous. Do not re-resolve on every run.

## Resolved mapping

| Fund (as tracked) | SEC EDGAR filer | CIK | DealCloud `PubInvestmentManager` | EntryId |
|---|---|---|---|---|
| D1 Capital Partners | D1 Capital Partners L.P. | 0001747057 | D1 Capital Partners L.P. | 332808 |
| BlackRock Strategic | *(none — see below)* | — | BlackRock | 332064 |
| Maplelane Capital | MAPLELANE CAPITAL, LLC | 0001512173 | Maplelane Capital LLC | 334531 |
| Aspex Management | Aspex Management (HK) Ltd | 0001768375 | Aspex Management | 331807 |
| Tiger Global | TIGER GLOBAL MANAGEMENT LLC | 0001167483 | Tiger Global Management, LLC | 336466 |
| Coatue Management | COATUE MANAGEMENT LLC | 0001135730 | Coatue | 332576 |
| Skye Global Management | Skye Global Management LP | 0001675884 | Skye Global | 336040 |
| Avala Global | Avala Global LP | 0001948899 | Avala Global | 331878 |
| Kinetic Partners | Kinetic | 0001911448 | Kinetic Partners Management, LP | 334173 |
| SurgoCap Partners | SurgoCap Partners LP | 0001960830 | SurgoCap Partners | 336275 |
| Voyager Global Management | Voyager Global Management LP | 0001849753 | Voyager Global | 336785 |
| Forest Avenue Capital Management | Forest Avenue Capital Management LP | 0001944889 | Forest Avenue Capital | 333322 |

## Corrections to the original assumptions

The brief this skill was built from made several predictions that turned out to
be wrong when checked against live data. Recorded here so nobody re-introduces them.

- **"Skye Global, Avala Global, Kinetic Partners, SurgoCap, Voyager Global and
  Forest Avenue may not file a 13F at all."** All six file. Every one has a
  13F-HR for period 2026-06-30. Forest Avenue — nominated as the likely
  non-filer test case — files a full 24-position, $2.4bn table.
  The no-filer code path is still implemented and needed (it is the correct
  outcome for a genuinely non-filing manager), it simply is not exercised by
  this fund list today.
- **"Aspex files under 'Aspex Management (HK) Ltd', so exact-string search will
  miss it."** True about the legal name. In practice EDGAR's prefix search on
  `Aspex Management` still resolves it; searching the short token `Aspex` is
  more robust.
- **"DealCloud's most recent note under plain 'BlackRock' is BlackRock STA."**
  True, but incomplete — see below.

## BlackRock Strategic — the sharpest edge

Three DealCloud records match `BlackRock`, and **two of them carry BlackRock STA
notes**:

| EntryId | Name | Latest note | Date |
|---|---|---|---|
| 332064 | BlackRock | PMM: BlackRock STA May 2026 Update | 2026-05-19 |
| 332065 | BlackRock, Inc. | PMM: BlackRock STA July 2026 Update | 2026-08-11 |
| 332979 | DSP Blackrock Asset Management | — | — |

**The user selected 332064 on 2026-08-27.** Note that 332065 held the *more
recent* STA note. If a future run finds 332064 has gone quiet while 332065 keeps
receiving STA notes, raise it rather than switching silently.

On the 13F side there is **no usable filer**:

- EDGAR name search for `BlackRock` returns **BLACKROCK ADVISORS LLC**
  (CIK 0001086364), whose last 13F-HR is **2016-12-31** — nearly ten years stale.
- CIK 0001364742 is **BlackRock Finance, Inc.**, last 13F 2024-06-30.
- BlackRock's firm-wide 13F reflects ~$11tn of index and ETF assets and is
  meaningless as a proxy for a single systematic hedge fund strategy.

So `edgar.use` is `false` for this fund and DealCloud is the only valid source.
This is exactly why `run_edgar.py` carries a staleness guard: without it the
2016 filing would be silently reported as current holdings.

## Other name-resolution decoys

- **Kinetic** — three decoys share the stem: `Horizon Kinetics` (333811) and
  `Horizon Kinetics Asset Management` (333812), both last noted 2019, and
  `Kinetics Advisers LLC` (334174), last noted 2006. Match on `Kinetic Partners`.
  Separately, the EDGAR conformed name is the bare word **"Kinetic"** — verify by
  CIK, not by name string.
- **Skye** — `Skye Global` (336040) chosen over `Skye Investment Advisors LLC`
  (336041, last note 2026-03-09) on recency and name match.
- **Voyager** — `Voyager Global` (336785) chosen over `Voyager Investment
  Advisors` (336786), which has no notes. Voyager's last note is **2024-02-13**,
  so DealCloud will essentially never be the fresher source for this fund.
- **Forest Avenue** — the DealCloud name omits "Management".
- **Coatue** — DealCloud uses the short house name "Coatue", not "Coatue Management".

## Per-fund caveats worth printing

- **D1 Capital** — the 13F is **61.9% SPACE EXPLORATION TECHN CORP** of a
  $34.8bn reported book. A private company legitimately appears in a 13F when
  held through a reportable structure, so "13F = public equities" is not a safe
  assumption. The remaining 54 positions are all under 3.1%.
- **Tiger Global, Coatue** — large private/venture books; 13F weights cover the
  public long sleeve only.
- **Aspex** — Hong Kong based, substantial non-US exposure. Its 13F (25
  positions) is a genuinely partial view: the fund runs well over 100% gross
  across Japan, Korea, Taiwan and China that a 13F cannot show.
- **Voyager Global** — files only **8 positions**, summing to ~100%. A short
  block is correct, not a truncation bug.
- **SurgoCap** — 13 positions; top 10 covers ~92%.
- **Skye Global** — its research notes state top positions with a highly
  concentrated top holding, and sometimes revise that weight in a nested
  sub-bullet dated after the table's own as-of. The parser reports the stated
  weight against the stated date and carries the revision as an annotation.

## Research note reality

Only a minority of the latest notes contain a parseable weights table — as of
2026-08-27, two of twelve. The rest are meeting writeups, investor-letter
summaries or image-based decks with no position sizes. That ratio is normal; it
is not a parser failure.

## Confidentiality

Research notes carry short positions, named GCM staff, named manager contacts and
manager-confidential commentary; some are explicitly marked not for external
disclosure. Note bodies, parsed holdings and finished reports are gitignored
(`cache/`, `output/`) and must not be committed. Test fixtures are synthetic —
see `tests/make_fixtures.py`. Keep the report itself to long top-10 weights.

## Reference data

- EDGAR company search (13F filers):
  `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company=<name>&type=13F-HR&output=atom`
- Submissions JSON: `https://data.sec.gov/submissions/CIK##########.json`
- Filing index: `https://www.sec.gov/Archives/edgar/data/<cik>/<accession-no-dashes>/index.json`
- SEC fair access policy: <https://www.sec.gov/os/webmaster-faq#developers>
- WhaleWisdom API (optional, unused — needs a paid subscription):
  <https://whalewisdom.com/help/api>, signature is
  `base64(HMAC-SHA1(secret, args + "\n" + timestamp))`, limit 20 req/min.
