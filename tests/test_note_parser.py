#!/usr/bin/env python3
"""
Regression tests for dealcloud_note_parser.

Fixtures are SYNTHETIC (see make_fixtures.py). No real GCM research note text is
committed to this repository -- the live notes carry manager-confidential
commentary, named contacts and short positions. Each fixture reproduces the
structure of a real failure mode using invented managers and securities.

The negative cases matter most: they are the shapes that would otherwise put
invented or misattributed numbers into an unattended weekly report.

Optionally, if a local gitignored cache/notes/ directory is present, a
supplementary pass runs the parser over the real notes as a smoke test. That
pass is skipped in a clean clone and never asserts on note contents.
"""

import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from dealcloud_note_parser import parse_note, _from_mcp_json  # noqa: E402

FIX = os.path.join(HERE, "fixtures")
failures = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' -- ' + detail) if detail else ''}")
    if not cond:
        failures.append(label)


def run(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        body, subj, as_of = _from_mcp_json(json.load(fh))
    return parse_note(body, subj, as_of)


# ------------------------------------------------------------------ negatives
print("\n[1] Slide-deck note -- many <img src=\"\">, no tables")
r = run("image_only.json")
print(f"      {r['diagnostics']}")
check("found is False", r["found"] is False)
check("holdings empty", r["holdings"] == [])
check("reason names the image cause", "image-based" in (r["reason"] or ""))
check("counted empty-src images", r["diagnostics"]["img_empty_src"] > 50,
      str(r["diagnostics"]["img_empty_src"]))
check("did not read '100% of the profit' as a weight",
      all(h["pct_of_portfolio"] != 100 for h in r["holdings"]))

print("\n[2] Images WITH src + exposure percentages")
r = run("images_with_src.json")
check("found is False", r["found"] is False)
check("counted non-empty-src images", r["diagnostics"]["img_with_src"] >= 3,
      str(r["diagnostics"]["img_with_src"]))
check("reason mentions non-extractable images", "not text-extractable" in (r["reason"] or ""))
check("did not read '209% gross exposure' as a weight",
      all(h["pct_of_portfolio"] != 209 for h in r["holdings"]))

print("\n[3] Investor-letter prose -- percentages everywhere, no weights")
r = run("perf_prose.json")
print(f"      {r['diagnostics']['percent_tokens']} percent tokens present")
check("found is False", r["found"] is False)
check("many percentages rejected", r["diagnostics"]["percent_tokens"] >= 10)
blob = json.dumps(r["holdings"])
for tkr in ("CSDI", "HBRL", "RDGW", "SBLF", "KSTL"):
    check(f"invented no weight for {tkr}", tkr not in blob)

print("\n[4] Basis-point attribution under Winners/Losers")
r = run("bps_attribution.json")
check("found is False", r["found"] is False)
blob = json.dumps(r["holdings"])
check("'Alderpoint +1%' not read as a weight", "Alderpoint" not in blob)
check("'Bellwether +96 bps' not read as 96%", "Bellwether" not in blob)

# ------------------------------------------------------------------ positives
print("\n[5] Weights list sharing a note with contributor/detractor lists")
r = run("attribution_weights.json")
print(f"      method={r.get('method')} table_as_of={r.get('table_as_of')}")
check("found is True", r["found"] is True)
check("method is weights_list", r.get("method") == "weights_list")
check("8 positions", len(r["holdings"]) == 8, str(len(r["holdings"])))
got = {h["position"]: h["pct_of_portfolio"] for h in r["holdings"]}
for name, pct in [("Vireo Health", 41.0), ("Wexford Utilities", 8.2),
                  ("Yardley Payments", 6.4), ("Zenith Data", 5.1),
                  ("Ashgrove Aggregates", 3.8), ("Brightwater Aero", 2.9),
                  ("Colverton Hotels", 2.3), ("Dunbar Ratings", 1.8)]:
    check(f"{name} = {pct}%", got.get(name) == pct, f"got {got.get(name)}")
check("used the table as-of (8/1), not the note date (8/10)",
      r.get("table_as_of") == "2026-08-01", str(r.get("table_as_of")))
check("captured the 'reduced to 34%' sub-bullet",
      any("34%" in a for a in r.get("annotations", [])))
check("sub-bullet did not become its own row", "Reduced" not in json.dumps(r["holdings"]))
vals = set(got.values())
for bad, lbl in [(6.18, "Vireo +6.18% contributor"), (0.92, "Wexford +0.92%"),
                 (0.55, "Yardley +0.55%"), (0.33, "Wexford -0.33% detractor"),
                 (0.41, "Zenith +0.41%"), (0.21, "Ashgrove -0.21%")]:
    check(f"attribution {lbl} excluded", bad not in vals)
check("descending order",
      [h["pct_of_portfolio"] for h in r["holdings"]] ==
      sorted([h["pct_of_portfolio"] for h in r["holdings"]], reverse=True))

print("\n[6] Clean weights list with its own as-of date")
r = run("weights_plain.json")
check("found is True", r["found"] is True)
check("10 positions", len(r["holdings"]) == 10, str(len(r["holdings"])))
got = {h["position"]: h["pct_of_portfolio"] for h in r["holdings"]}
for name, pct in [("Thornbury Alloys", 11.4), ("Ironwood Storage", 6.7),
                  ("Silverpine Memory", 5.2), ("Cobalt Analytics", 4.8),
                  ("Marlowe Databases", 4.6), ("Halden Energy", 3.9),
                  ("Pemberton Foundry", 3.3), ("Rosslyn Turbines", 3.3),
                  ("Ellsworth Pharma", 2.8), ("Vireo Health", 2.4)]:
    check(f"{name} = {pct}%", got.get(name) == pct, f"got {got.get(name)}")
check("used table as-of 7/31, not note date 8/25",
      r.get("table_as_of") == "2026-07-31", str(r.get("table_as_of")))
vals = set(got.values())
check("'-2.7%' detractor excluded", 2.7 not in vals)
check("'+55 bps' not read as 55%", 55.0 not in vals)

print("\n[7] Synthetic HTML weights table")
r = parse_note("""
<p><strong>Top 10 Holdings</strong></p>
<table><tbody>
<tr><th>Position</th><th>% of Portfolio</th></tr>
<tr><td>Vireo Health (VRHL)</td><td>9.4%</td></tr>
<tr><td>Zenith Data (ZNDT)</td><td>8.1%</td></tr>
<tr><td>Halden Energy (HLDN)</td><td>6.2%</td></tr>
<tr><td>Marlowe Databases (MRLW)</td><td>5.9%</td></tr>
</tbody></table>
<p>The fund returned 12.4% net for the quarter.</p>
""", "Synthetic", "2026-08-01")
check("found is True", r["found"] is True)
check("method is html_table", r.get("method") == "html_table")
check("top is VRHL 9.4", r["holdings"][0]["ticker"] == "VRHL"
      and r["holdings"][0]["pct_of_portfolio"] == 9.4)
check("ignored the 12.4% performance figure",
      12.4 not in [h["pct_of_portfolio"] for h in r["holdings"]])

print("\n[8] Empty body")
r = parse_note("", "Empty", "2026-08-01")
check("found is False", r["found"] is False)
check("reason mentions empty", "empty" in (r["reason"] or "").lower())

# ------------------------------- optional local pass over real notes ----------
real = sorted(glob.glob(os.path.join(ROOT, "cache", "notes", "*.json")))
print(f"\n[9] Local real-note smoke test ({len(real)} notes found)")
if not real:
    print("      skipped -- cache/notes/ absent (expected in a clean clone)")
else:
    ok = True
    for path in real:
        try:
            with open(path, encoding="utf-8") as fh:
                body, subj, as_of = _from_mcp_json(json.load(fh))
            res = parse_note(body, subj, as_of)
            assert isinstance(res["found"], bool)
            assert res["found"] or res["reason"], "no verdict reason"
            for h in res["holdings"]:
                assert 0 < h["pct_of_portfolio"] <= 100
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"      ERROR on {os.path.basename(path)}: {exc}")
    check("every real note parses to a definite, bounded verdict", ok)

print("\n" + "=" * 64)
if failures:
    print(f"FAILED ({len(failures)}): " + "; ".join(failures))
    sys.exit(1)
print("ALL CHECKS PASSED")
