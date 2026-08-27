#!/usr/bin/env python3
"""
Parse a DealCloud ARS ResearchNote `Body` (CKEditor HTML) for a top-10 holdings
table with portfolio percentages.

Design stance: CONSERVATIVE. Holdings are returned only when an explicit
position-weight structure is visible. Everything else returns found=false with a
specific reason. A blank cell is recoverable; a fabricated weight in an
unattended weekly report is not.

Three extraction strategies, in priority order:
  1. html_table    -- a <table> with a weight-ish header column.
  2. weights_list  -- a heading such as "Top Positions as of 7/31/2026" followed
                      by list items of the form "Name 9.7%". This is how GCM
                      analysts actually write them up.
  3. inline        -- repeated "Name (TICKER) 9.7%" prose patterns.

Four failure modes this must survive, all observed in live notes and reproduced
as synthetic fixtures in tests/ (no real note text is committed):

  * Image-only notes. A pasted slide deck: dozens of <img> tags with an EMPTY
    src and no tables. The screenshots are not retrievable through the API, so
    there is simply no holdings text to parse.
  * Performance prose. An investor-letter writeup naming securities with tickers
    and carrying dozens of percentages, none of them a position weight
    ("returned 19.9% net", "three-year IRRs of 30-40%").
  * Attribution lists. A note may contain BOTH a real "Top Positions" weights
    list AND "Top Contributors"/"Top Detractors" lists of the same shape.
    Picking the wrong list silently reports P&L attribution as portfolio weight.
    Headings disambiguate, and a leading +/- sign marks attribution, never a
    weight.
  * Basis-point lists. Winners/losers quoted as "<name> +96 bps" -- same trap,
    different unit.

Usage:
    python dealcloud_note_parser.py --json note.json      # MCP tool output
    python dealcloud_note_parser.py --html body.html
    python dealcloud_note_parser.py --json -              # stdin
"""

import argparse
import html
import json
import re
import sys
from html.parser import HTMLParser

# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------

# A heading that introduces genuine position weights.
POSITIVE_HEADINGS = (
    "top position",
    "top holding",
    "largest position",
    "largest holding",
    "top 10",
    "top ten",
    "core position",
    "portfolio holding",
    "top long",
    "current position",
    "position size",
    "portfolio weight",
)

# A heading that introduces P&L attribution. Never a weight, even though the
# rows look identical.
NEGATIVE_HEADINGS = (
    "contributor",
    "detractor",
    "winner",
    "loser",
    "attribution",
    "performance",
    "gainer",
    "return",
    "p&l",
    "exposure",
    "commentary",
    "new position",
    "exited",
    "trim",
)

# Words that mark a percentage as performance/valuation commentary in prose.
PERF_TOKENS = (
    "return", "returned", "net", "gross", "ytd", "annualiz", "annualis",
    "s&p", "index", "benchmark", "irr", "cagr", "ebitda", "margin", "upside",
    "downside", "growth", "grew", "decline", "declined", "fell", "rose",
    "drawdown", "basis point", "bps", "eps", "revenue", "consensus",
    "estimate", "guidance", "alpha", "beta", "volatil", "sharpe",
    "attribution", "contributed", "detract", "since inception", "itd", "qtd",
    "mtd", "fee", "hurdle", "synerg", "multiple", "discount", "premium",
    "ownership",
)

# Header tokens identifying a weight column in a real <table>.
WEIGHT_TOKENS = (
    "% of portfolio", "percent of portfolio", "portfolio weight", "% weight",
    "weight (%)", "weight", "position size", "% of nav", "percent of nav",
    "% of aum", "% of capital", "exposure (%)", "% exposure", "size (%)",
    "allocation",
)

HOLDINGS_HEADINGS = POSITIVE_HEADINGS + ("holdings", "positions")

NAME_TICKER_PCT = re.compile(
    r"(?P<name>[A-Z][A-Za-z0-9&.,'\-/ ]{1,60}?)\s*"
    r"\(\s*(?P<ticker>[A-Z]{1,6}(?:[.\-][A-Z]{1,3})?)\s*\)\s*"
    r"[\s\-–—:|,]{0,6}"
    r"(?P<pct>\d{1,3}(?:\.\d{1,2})?)\s*%"
)

# Anchored: the WHOLE list item must be "Name 9.7%". Trailing prose such as
# "Reduced to 39% as of 8/10/2026" is rejected, as is any leading +/- sign.
LIST_NAME_PCT = re.compile(
    r"^(?P<name>[A-Za-z][A-Za-z0-9&.,'’\-/ ]{1,60}?)"
    r"\s+(?P<pct>\d{1,3}(?:\.\d{1,2})?)\s*%$"
)

PCT_RE = re.compile(r"^\s*(\d{1,3}(?:\.\d{1,2})?)\s*%?\s*$")
AS_OF_RE = re.compile(
    r"as\s+of\s+(\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2})", re.I
)
# Verbs that mean the line is a narrative sub-point, not a position row.
NARRATIVE_STARTS = (
    "reduced", "increased", "added", "trimmed", "exited", "sold", "bought",
    "up ", "down ", "now ", "was ", "approximately", "roughly", "about",
)


class _DocExtractor(HTMLParser):
    """Linearise the note into headings and list items, tracking list depth."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.events = []            # ("heading"|"item", text, depth)
        self.tables = []
        self.img_total = 0
        self.img_empty_src = 0
        self.img_with_src = 0

        self._ul_depth = 0
        self._li_stack = []         # (buffer, order, depth_at_open) per open <li>
        self._order = 0             # document order; </li> fires child-before-parent
        self._in_strong = 0
        self._para = []             # text of current <p>
        self._para_strong = []      # bold-only text of current <p>
        self._tstack, self._row, self._cell = [], None, None
        self.text_parts = []

    # -- tags ------------------------------------------------------------
    def handle_starttag(self, tag, attrs):
        if tag == "img":
            self.img_total += 1
            src = dict(attrs).get("src")
            if src and src.strip():
                self.img_with_src += 1
            else:
                self.img_empty_src += 1
        elif tag == "table":
            self._tstack.append([])
        elif tag == "tr" and self._tstack:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag == "ul" or tag == "ol":
            self._ul_depth += 1
        elif tag == "li":
            self._order += 1
            self._li_stack.append(([], self._order, self._ul_depth))
        elif tag == "strong" or tag == "b":
            self._in_strong += 1
        elif tag == "p":
            self._para, self._para_strong = [], []
        elif tag == "br":
            self.text_parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "table" and self._tstack:
            self.tables.append(self._tstack.pop())
        elif tag == "tr" and self._tstack and self._row is not None:
            if any(c.strip() for c in self._row):
                self._tstack[-1].append(self._row)
            self._row = None
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag in ("ul", "ol"):
            self._ul_depth = max(0, self._ul_depth - 1)
        elif tag == "li":
            if self._li_stack:
                buf, order, depth = self._li_stack.pop()
                text = re.sub(r"\s+", " ", "".join(buf)).strip()
                if text:
                    self.events.append((order, "item", text, depth))
        elif tag in ("strong", "b"):
            self._in_strong = max(0, self._in_strong - 1)
        elif tag == "p":
            whole = re.sub(r"\s+", " ", "".join(self._para)).strip()
            bold = re.sub(r"\s+", " ", "".join(self._para_strong)).strip()
            # A heading is a paragraph that is entirely bold, outside any <li>.
            if whole and bold and len(bold) >= len(whole) * 0.8 and not self._li_stack:
                self._order += 1
                self.events.append((self._order, "heading", whole, self._ul_depth))
            self._para, self._para_strong = [], []
            self.text_parts.append("\n")

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
        if self._li_stack:
            # Text belongs to the innermost open <li> only.
            self._li_stack[-1][0].append(data)
        self._para.append(data)
        if self._in_strong:
            self._para_strong.append(data)
        self.text_parts.append(data)

    @property
    def ordered_events(self):
        """Events in true document order (</li> closes child before parent)."""
        return [(k, t, d) for _o, k, t, d in sorted(self.events, key=lambda e: e[0])]

    @property
    def text(self):
        raw = "".join(self.text_parts).replace("\xa0", " ")
        raw = re.sub(r"[ \t]+", " ", raw)
        return re.sub(r"\n\s*\n+", "\n", raw)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _looks_like_performance(context):
    low = context.lower()
    return any(tok in low for tok in PERF_TOKENS)


def _clean_name(name):
    name = re.sub(r"\s+", " ", name).strip(" .,-–—:|")
    return re.sub(r"^\d{1,2}\s*[.)\-]\s*", "", name)


def _iso_date(raw):
    raw = raw.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return raw
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$", raw)
    if m:
        mth, day, yr = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if yr < 100:
            yr += 2000
        try:
            return f"{yr:04d}-{mth:02d}-{day:02d}"
        except ValueError:
            return None
    return None


def _heading_is_positive(text):
    low = text.lower()
    if any(neg in low for neg in NEGATIVE_HEADINGS):
        return False
    return any(pos in low for pos in POSITIVE_HEADINGS)


# --------------------------------------------------------------------------
# Strategies
# --------------------------------------------------------------------------

def _parse_tables(tables):
    for tbl in tables:
        if len(tbl) < 3:
            continue
        header = [c.lower() for c in tbl[0]]
        weight_col = next(
            (i for i, c in enumerate(header) if any(t in c for t in WEIGHT_TOKENS)), None
        )
        if weight_col is None:
            continue
        name_col = 0 if weight_col != 0 else 1
        rows = []
        for row in tbl[1:]:
            if len(row) <= max(weight_col, name_col):
                continue
            m = PCT_RE.match(row[weight_col])
            if not m:
                continue
            name = _clean_name(row[name_col])
            if not name:
                continue
            ticker = ""
            tm = re.search(r"\(\s*([A-Z]{1,6}(?:[.\-][A-Z]{1,3})?)\s*\)", name)
            if tm:
                ticker = tm.group(1)
                name = _clean_name(name[: tm.start()])
            rows.append({"position": name, "ticker": ticker,
                         "pct_of_portfolio": float(m.group(1))})
        if len(rows) >= 3:
            return rows, "html_table", None, []
    return None, None, None, []


def _parse_weights_list(events):
    """A positive heading followed by anchored `Name N.N%` list items."""
    for idx, (kind, text, _depth) in enumerate(events):
        if kind != "heading" or not _heading_is_positive(text):
            continue

        as_of = None
        m = AS_OF_RE.search(text)
        if m:
            as_of = _iso_date(m.group(1))

        rows, annotations = [], []
        base_depth = None
        for kind2, text2, depth2 in events[idx + 1:]:
            if kind2 == "heading":
                break
            if kind2 != "item":
                continue
            if base_depth is None:
                base_depth = depth2
            if depth2 > base_depth:
                # Nested sub-bullet: context for the row above, never a row.
                if rows:
                    annotations.append(f"{rows[-1]['position']}: {text2}")
                continue
            if depth2 < base_depth:
                break
            if "%" not in text2 and any(n in text2.lower() for n in NEGATIVE_HEADINGS):
                break  # bare "Top Contributors" label ends the weights section
            if text2.lstrip().startswith(("+", "-", "−")):
                continue  # signed value => attribution, not a weight
            hit = LIST_NAME_PCT.match(text2.strip())
            if not hit:
                continue
            name = _clean_name(hit.group("name"))
            if not name or name.lower().startswith(NARRATIVE_STARTS):
                continue
            pct = float(hit.group("pct"))
            if not (0.0 < pct <= 100.0):
                continue
            ticker = ""
            tm = re.search(r"\(\s*([A-Z]{1,6})\s*\)", name)
            if tm:
                ticker = tm.group(1)
                name = _clean_name(name[: tm.start()])
            rows.append({"position": name, "ticker": ticker, "pct_of_portfolio": pct})

        if len(rows) >= 3:
            return rows, "weights_list", as_of, annotations
    return None, None, None, []


def _parse_inline(text):
    hits = []
    for m in NAME_TICKER_PCT.finditer(text):
        start, end = m.span()
        context = text[max(0, start - 140): min(len(text), end + 140)]
        if _looks_like_performance(context):
            continue
        pct = float(m.group("pct"))
        if not (0.0 < pct <= 100.0):
            continue
        name = _clean_name(m.group("name"))
        if not name or len(name) < 2:
            continue
        hits.append({"position": name, "ticker": m.group("ticker"),
                     "pct_of_portfolio": pct})
    if len(hits) < 3:
        return None, None, None, []
    low = text.lower()
    if not any(h in low for h in HOLDINGS_HEADINGS):
        return None, None, None, []
    return hits, "inline_pattern", None, []


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def parse_note(body_html, subject=None, as_of=None):
    if not body_html or not body_html.strip():
        return {"found": False, "subject": subject, "as_of": as_of,
                "reason": "Not found in DealCloud note text (note Body is empty).",
                "holdings": [], "diagnostics": {}}

    ex = _DocExtractor()
    ex.feed(body_html)
    text = html.unescape(ex.text)

    diagnostics = {
        "img_total": ex.img_total,
        "img_empty_src": ex.img_empty_src,
        "img_with_src": ex.img_with_src,
        "tables": len(ex.tables),
        "headings": sum(1 for e in ex.events if e[1] == "heading"),
        "list_items": sum(1 for e in ex.events if e[1] == "item"),
        "text_chars": len(text.strip()),
        "percent_tokens": len(re.findall(r"\d+(?:\.\d+)?\s*%", text)),
    }

    rows, method, table_as_of, annotations = _parse_tables(ex.tables)
    if rows is None:
        rows, method, table_as_of, annotations = _parse_weights_list(ex.ordered_events)
    if rows is None:
        rows, method, table_as_of, annotations = _parse_inline(text)

    if rows:
        best = {}
        for r in rows:
            key = (r["ticker"] or r["position"]).upper()
            if key not in best or r["pct_of_portfolio"] > best[key]["pct_of_portfolio"]:
                best[key] = r
        out = sorted(best.values(), key=lambda r: -r["pct_of_portfolio"])[:10]
        return {
            "found": True,
            "subject": subject,
            "as_of": table_as_of or as_of,
            "note_as_of": as_of,
            "table_as_of": table_as_of,
            "method": method,
            "reason": None,
            "holdings": out,
            "annotations": annotations,
            "diagnostics": diagnostics,
        }

    if ex.img_empty_src >= 3 and diagnostics["tables"] == 0:
        reason = (
            f"Not found in DealCloud note text (note may be image-based: "
            f"{ex.img_empty_src} embedded images with empty src, 0 tables -- "
            f"slide screenshots are not retrievable through the DealCloud API)."
        )
    elif ex.img_with_src >= 3 and diagnostics["tables"] == 0:
        reason = (
            f"Not found in DealCloud note text (note embeds {ex.img_with_src} images "
            f"whose content is not text-extractable; any holdings table is likely "
            f"inside a pasted slide)."
        )
    elif diagnostics["tables"] == 0 and diagnostics["percent_tokens"] == 0:
        reason = "Not found in DealCloud note text (no holdings table and no percentages in the note)."
    else:
        reason = (
            "Not found in DealCloud note text (percentages present but none identifiable "
            "as position weights -- they read as performance/attribution commentary)."
        )
    return {"found": False, "subject": subject, "as_of": as_of, "reason": reason,
            "holdings": [], "annotations": [], "diagnostics": diagnostics}


def _from_mcp_json(payload):
    row = payload.get("row", payload) if isinstance(payload, dict) else {}
    subject = row.get("Subject")
    if isinstance(subject, dict):
        subject = subject.get("name")
    as_of = row.get("AsOfDate")
    if isinstance(as_of, str):
        as_of = as_of[:10]
    return row.get("Body") or "", subject, as_of


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--json", help="JSON file (or - for stdin) from get_dealcloud_entity_by_id")
    g.add_argument("--html", help="File containing the raw Body HTML")
    a = p.parse_args()

    if a.json:
        raw = sys.stdin.read() if a.json == "-" else open(a.json, encoding="utf-8").read()
        body, subject, as_of = _from_mcp_json(json.loads(raw))
    else:
        body, subject, as_of = open(a.html, encoding="utf-8").read(), None, None

    print(json.dumps(parse_note(body, subject, as_of), indent=2))


if __name__ == "__main__":
    main()
