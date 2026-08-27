#!/usr/bin/env python3
"""
Generate SYNTHETIC research-note fixtures for the parser test suite.

No real GCM research note text is ever committed to this repository. The live
notes are manager-confidential -- they carry short positions, named GCM staff and
named manager contacts, and some are explicitly marked not for external
disclosure.

These fixtures reproduce the *structure* of each failure mode exactly as it
appears in CKEditor output, using invented managers and invented securities, so
the parser is still tested against every trap that matters:

  image_only            slide-screenshot deck: many <img src=""> , no tables
  images_with_src       images that DO have a src, plus exposure percentages
  perf_prose            investor-letter prose: many percentages, zero weights
  bps_attribution       "Winners/Losers" lists quoted in bps and %
  attribution_weights   BOTH a real "Top Positions" list AND contributor/
                        detractor lists of identical shape, plus a nested
                        sub-bullet revising one weight
  weights_plain         a clean "Top Positions as of ..." list

Run:  python tests/make_fixtures.py
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "fixtures")

P = '<p style="margin: 0"><span style="font-family: Calibri, sans-serif; font-size: 11pt">{}</span></p>'
PB = '<p style="margin: 0"><span style="font-family: Calibri, sans-serif; font-size: 11pt"><strong>{}</strong></span></p>'
LI = '<li class="ck-list-marker-font-size ck-list-marker-font-family">{}</li>'


def li(text, nested=""):
    return LI.format(P.format(text) + (f"<ul>{nested}</ul>" if nested else ""))


def payload(entry_id, subject, as_of, body):
    return {
        "status": "ok",
        "row": {
            "EntryId": entry_id,
            "Subject": {"type": 0, "id": entry_id, "name": subject, "entryListId": 6082},
            "AsOfDate": f"{as_of}T04:00:00Z",
            "Body": body,
        },
    }


def build():
    fixtures = {}

    # ---------------------------------------------------------------- 1
    # Slide-deck webinar note: dozens of pasted screenshots with EMPTY src.
    imgs = "".join(
        f'<p><span><img src="" width="{560 + (i % 7) * 4}" height="{310 + (i % 5) * 3}"></span></p>'
        for i in range(75)
    )
    body = (
        PB.format("EMM: Northwind Capital Update Webinar")
        + P.format("GCM: [analyst initials]")
        + PB.format("Key Takeaways")
        + "<ul>"
        + li("Discussed portfolio construction and the current opportunity set.")
        + li("Selling inventory allows you to book 100% of the profit immediately.")
        + li("Gross exposure was as high as 40% in 2023, which was the right decision.")
        + "</ul>"
        + imgs
    )
    fixtures["image_only.json"] = payload(
        900001, "EMM: Northwind Capital Update Webinar", "2026-05-06", body
    )

    # ---------------------------------------------------------------- 2
    # Images that DO have a src, plus exposure figures that are not weights.
    srcs = "".join(
        f'<p><span><img src="/dcstatic/richtextimages/synthetic-{i}" width="624" height="384"></span></p>'
        for i in range(4)
    )
    body = (
        PB.format("EMM: Meridian Asia Q2 Webinar")
        + PB.format("Positioning")
        + "<ul>"
        + li("209% gross exposure (from 223%), 80% net exposure (from 85%).")
        + li("Technology is roughly 40% of net capital and 45% of gross.")
        + li("Momentum exposure actively cut to the median peer level since May.")
        + "</ul>"
        + srcs
        + PB.format("Outlook")
        + "<ul>" + li("Expect a more rational environment in the second half.") + "</ul>"
    )
    fixtures["images_with_src.json"] = payload(
        900002, "EMM: Meridian Asia Q2 Webinar", "2026-07-24", body
    )

    # ---------------------------------------------------------------- 3
    # Investor-letter prose. Names securities WITH tickers next to percentages,
    # but every one is a return/valuation figure. The word "positions" appears
    # so the holdings-heading gate passes and the PERF filter is what rejects.
    body = (
        PB.format("Key Takeaways")
        + "<ul>"
        + li("The Fund returned 19.9% net in Q2 versus 15.2% for the benchmark, "
             "bringing year-to-date net to 22.6%.")
        + li("In July the Fund declined 15.0% gross and 12.9% net as momentum unwound.")
        + "</ul>"
        + PB.format("Performance Summary")
        + "<ul>"
        + li("Top five winners by net return: Cascade Industrial (CSDI) 12.4%, "
             "Harborline Group (HBRL) 9.1%, Ridgeway Power (RDGW) 7.8%.")
        + li("Top five detractors by net return: Sablefish Energy (SBLF) 6.2%, "
             "Kestrel Aerospace (KSTL) 4.9%.")
        + li("Several positions are now underwritten to three-year IRRs of 30-40%.")
        + "</ul>"
        + PB.format("Cascade Industrial (CSDI)")
        + "<ul>"
        + li("Down over 15% in July, including 7.6% on Q2 earnings, which we "
             "attribute to conservative guidance rather than deterioration.")
        + li("We model earnings compounding more than 20% annually through 2030.")
        + "</ul>"
    )
    fixtures["perf_prose.json"] = payload(
        900003, "Cascade Partners Investor Letter - Q2 2026 Notes", "2026-08-24", body
    )

    # ---------------------------------------------------------------- 4
    # Attribution quoted in basis points and percent, under Winners/Losers.
    body = (
        PB.format("EMM: Foxglove Capital Update Call")
        + PB.format("June Winners")
        + "<ul>"
        + li("Alderpoint Materials +1%")
        + li("Bellwether Systems +96 bps")
        + li("Crestline Media +70 bps")
        + li("Dunmore Robotics +65 bps")
        + "</ul>"
        + PB.format("June Losers")
        + "<ul>"
        + li("Everline Semiconductor -93 bps")
        + li("Fairhaven Retail -78 bps")
        + li("Gladstone Freight -24 bps")
        + "</ul>"
        + PB.format("June Attribution")
        + "<ul>" + li("+5.88% long, +2.81% short") + "</ul>"
    )
    fixtures["bps_attribution.json"] = payload(
        900004, "EMM: Foxglove Capital Update Call", "2026-07-28", body
    )

    # ---------------------------------------------------------------- 5
    # The hardest case: a genuine weights list in the SAME note as contributor
    # and detractor lists of identical shape, plus a nested sub-bullet that
    # revises the top weight after the stated as-of date.
    contributors = (
        li("Vireo Health +6.18%") + li("Wexford Utilities +0.92%")
        + li("Yardley Payments +0.55%") + li("Zenith Data +0.41%")
    )
    detractors = (
        li("Wexford Utilities -0.33%") + li("Ashgrove Aggregates -0.21%")
        + li("Brightwater Aero -0.14%")
    )
    positions = (
        li("Vireo Health 41%", nested=li("Reduced to 34% as of 8/10/2026"))
        + li("Wexford Utilities 8.2%")
        + li("Yardley Payments 6.4%")
        + li("Zenith Data 5.1%")
        + li("Ashgrove Aggregates 3.8%")
        + li("Brightwater Aero 2.9%")
        + li("Colverton Hotels 2.3%")
        + li("Dunbar Ratings 1.8%")
    )
    body = (
        PB.format("EMM: Larkspur Global Monthly Update")
        + PB.format("July Performance")
        + "<ul>"
        + li("Top Contributors", nested=contributors)
        + li("Top Detractors", nested=detractors)
        + "</ul>"
        + PB.format("Top Positions (as of 8/1/2026)")
        + f"<ul>{positions}</ul>"
        + PB.format("Exposure")
        + "<ul>" + li("Ended July with roughly 99% long and 2% short exposure.") + "</ul>"
    )
    fixtures["attribution_weights.json"] = payload(
        900005, "EMM: Larkspur Global Monthly Update", "2026-08-10", body
    )

    # ---------------------------------------------------------------- 6
    # Clean weights list whose heading carries its own as-of date.
    positions = (
        li("Thornbury Alloys 11.4%") + li("Ironwood Storage 6.7%")
        + li("Silverpine Memory 5.2%") + li("Cobalt Analytics 4.8%")
        + li("Marlowe Databases 4.6%") + li("Halden Energy 3.9%")
        + li("Pemberton Foundry 3.3%") + li("Rosslyn Turbines 3.3%")
        + li("Ellsworth Pharma 2.8%") + li("Vireo Health 2.4%")
    )
    body = (
        PB.format("EMM: Quillon Partners Update Call")
        + PB.format("General")
        + "<ul>" + li("Up mid-single digits as of this afternoon.") + "</ul>"
        + PB.format("Top Positions as of 7/31/2026")
        + f"<ul>{positions}</ul>"
        + PB.format("July Top Contributors")
        + "<ul>" + li("Cobalt Analytics +55 bps") + li("Halden Energy +25 bps") + "</ul>"
        + PB.format("July Top Detractors")
        + "<ul>" + li("Silverpine Memory -2.7%") + li("Ironwood Storage -1.1%") + "</ul>"
    )
    fixtures["weights_plain.json"] = payload(
        900006, "EMM: Quillon Partners Update Call", "2026-08-25", body
    )

    return fixtures


def main():
    os.makedirs(OUT, exist_ok=True)
    written = build()
    for name, obj in written.items():
        with open(os.path.join(OUT, name), "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=1)
        print(f"  wrote fixtures/{name}")
    print(f"\n{len(written)} synthetic fixtures written to {OUT}")


if __name__ == "__main__":
    main()
