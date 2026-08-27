#!/usr/bin/env python3
"""
Merge the 13F half (cache/manifest_skeleton.json) with the DealCloud half
(cache/notes_parsed.json) into the final report manifest.

Freshness rule, per the spec:
  * Compare the 13F PERIOD OF REPORT (not its filing date -- the period is the
    date the holdings are true as of) against the DealCloud as-of date.
  * Prefer DealCloud ONLY when it is genuinely more recent AND the note actually
    yielded a parseable weights table. A fresher source with no usable data
    never displaces an older source that has data.
  * Whichever source wins is printed above that fund's table, with its as-of.

Note as-of precedence: a weights table that states its own date ("Top Positions
as of 7/31/2026") is truer than the note's AsOfDate (when it was written), so the
parser's table_as_of wins when present.
"""

import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(ROOT, "cache")
SKELETON = os.path.join(CACHE, "manifest_skeleton.json")
PARSED = os.path.join(CACHE, "notes_parsed.json")
NOTE_INDEX = os.path.join(CACHE, "note_index.json")
OUT = os.path.join(CACHE, "manifest.json")


def load(path, default=None):
    if not os.path.exists(path):
        if default is not None:
            return default
        sys.exit(f"Missing required file: {path}")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def merge():
    skeleton = load(SKELETON)
    parsed = load(PARSED, {})
    index = load(NOTE_INDEX, {})

    funds = []
    for block in skeleton["funds"]:
        name = block["fund"]
        meta = index.get(name, {})
        note_id = str(meta.get("note_id") or "")
        note = parsed.get(note_id) or {}

        dc_ok = bool(note.get("found")) and bool(note.get("holdings"))
        dc_as_of = note.get("as_of") or meta.get("note_date")
        edgar_as_of = block.get("as_of")
        edgar_ok = bool(block.get("holdings")) and block.get("_13f_status") == "ok"

        dc_note_bits = []
        if meta.get("note_subject"):
            dc_note_bits.append(f'note {note_id} "{meta["note_subject"]}"')
        if note.get("method"):
            dc_note_bits.append(f"parsed via {note['method']}")
        if note.get("table_as_of") and note.get("note_as_of") \
                and note["table_as_of"] != note["note_as_of"]:
            dc_note_bits.append(
                f"table states as-of {note['table_as_of']}; note written {note['note_as_of']}"
            )

        use_dc = dc_ok and (not edgar_ok or (dc_as_of and edgar_as_of and dc_as_of > edgar_as_of)
                            or not edgar_as_of)

        out = dict(block)
        out.pop("_13f_reused_cache", None)

        if use_dc:
            out["source"] = "DealCloud ARS Research Note"
            out["as_of"] = dc_as_of
            out["detail"] = ", ".join(dc_note_bits) or f"note {note_id}"
            out["holdings"] = note["holdings"]
            out["not_found_reason"] = None
            caveats = ["Manager-reported position weights (share of the fund's own "
                       "portfolio as the manager states it), not a 13F-derived weight."]
            if note.get("annotations"):
                caveats.append("Note adds: " + "; ".join(note["annotations"]) + ".")
            if edgar_ok:
                caveats.append(
                    f"Fresher than the {edgar_as_of} 13F, which is also on file and "
                    f"available if a US-listed-long-only view is wanted."
                )
            out["caveat"] = " ".join(caveats)
            out["_source_choice"] = (
                f"DealCloud ({dc_as_of}) beat 13F ({edgar_as_of})" if edgar_as_of
                else f"DealCloud ({dc_as_of}); no 13F available"
            )

        elif edgar_ok:
            out["_source_choice"] = "SEC EDGAR 13F"
            if dc_as_of and edgar_as_of and dc_as_of > edgar_as_of:
                why = note.get("reason") or "no parseable weights in the note"
                out["_source_choice"] = (
                    f"13F ({edgar_as_of}) used even though the DealCloud note is newer "
                    f"({dc_as_of}): {why}"
                )
                out["caveat"] = (out.get("caveat") or "") + (
                    f" A newer DealCloud note exists ({dc_as_of}) but yielded no weights: {why}"
                )

        else:
            # Neither source produced holdings. Say why for both.
            out["source"] = out.get("source") or None
            reasons = []
            if out.get("not_found_reason"):
                reasons.append(f"13F: {out['not_found_reason']}")
            if note.get("reason"):
                reasons.append(f"DealCloud: {note['reason']}")
            elif not note_id:
                reasons.append("DealCloud: no research note located for this manager.")
            out["not_found_reason"] = " | ".join(reasons) or "No source produced holdings."
            out["holdings"] = []
            if not out.get("as_of") and dc_as_of:
                out["as_of"] = dc_as_of
                out["source"] = "DealCloud ARS Research Note (no weights)"
            out["_source_choice"] = "none"

        funds.append(out)

    return {"generated": datetime.date.today().isoformat(), "funds": funds}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default=OUT)
    a = p.parse_args()

    manifest = merge()
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)

    print("Source chosen per fund:", file=sys.stderr)
    for f in manifest["funds"]:
        n = len(f["holdings"])
        print(f"  {f['fund']:<34} {n:>2} rows   {f.get('_source_choice')}", file=sys.stderr)
    print(f"\nManifest -> {a.out}", file=sys.stderr)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
