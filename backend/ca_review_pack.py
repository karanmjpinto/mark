#!/usr/bin/env python3
"""
ca_review_pack.py — print every tax assumption in the product, for an accountant.

    python3 backend/ca_review_pack.py > docs/ca-review-pack.md

`compliance.py` computes GST and TDS on every budget line. The arithmetic is
tested; the *rates, sections and thresholds* are defaults read off public rules
and have never been confirmed by anyone qualified. Until they are, nothing from
`/compliance/*` can go to a client.

This generates the review document from `compliance.RULES`, `THRESHOLDS` and
`SECTION_DEFAULTS` rather than from a hand-written copy, so the pack and the code
cannot drift apart. Regenerate it whenever those tables change, and re-date the
sign-off block.

The document is written to be answerable in half an hour: every row is a
statement the reviewer either ticks or corrects, and the open questions are
listed separately at the end because they need a judgement rather than a rate.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import compliance as c  # noqa: E402

SECTION_NAMES = {
    "10000": "Development", "10300": "Director", "10600": "Director team (ADs)",
    "10700": "Extras / junior artists", "10800": "Production staff",
    "11000": "Art department", "11200": "Stunts", "11300": "Camera",
    "11400": "Sound", "11500": "Lighting & grip", "11800": "Wardrobe",
    "11900": "Choreography", "12000": "Makeup & hair", "12300": "Transport",
    "12400": "Location hire", "12600": "Catering", "12800": "Travel",
    "12900": "Editorial", "13100": "Post sound", "13300": "VFX",
    "13700": "Insurance", "14000": "Contingency",
}

KEYWORD_RULES = [
    ("Reads as rent of premises", "194I_PREMISES",
     "studio floor, stage hire, floor hire, location fee, bungalow, apartment, premises, office, godown, warehouse"),
    ("Reads as equipment or kit hire", "194I_EQUIPMENT",
     "rental, rent, hire, kit, equipment, generator, crane, jimmy, gimbal, drone unit, lens, monitor, "
     "or '<camera|lighting|grip|light|sound|lens|art> … package'"),
    ("Reads as a technical / facility service", "194JB",
     "online, conform, grade, DI, mix, foley, dubbing, VFX, render, studio time, facility, telecine, mastering"),
    ("Names a professional engagement", "194J",
     "director, DOP, cinematographer, editor, colourist, composer, music, writer, designer, stylist, "
     "makeup, hair, sound design, line producer, producer, assistant director, choreographer, "
     "consultant, artist, actor, talent, model"),
    ("Reads as a purchase of goods", "194Q",
     "purchase, buy, consumable, stock, material, raw (and not a transport line)"),
    ("No decisive signal", "194C", "falls back to the budget section default below, then to 194C"),
]

ASSUMPTIONS = [
    ("TDS is computed on the value **excluding** GST, where GST is shown separately on the invoice.",
     "CBDT Circular 23/2017"),
    ("Without a valid PAN, the rate becomes 20% regardless of section.", "s.206AA"),
    ("A transporter is flagged, not exempted: no deduction under s.194C(6) requires the vendor to "
     "declare ≤10 goods carriages and furnish a PAN, so the product deducts and raises a flag.",
     "s.194C(6)"),
    ("Input tax credit is treated as blocked on food, beverages and outdoor catering, and on "
     "passenger transport hire. Blocked credit is reported separately and never netted off the "
     "amount payable.", "s.17(5)(b), CGST Act"),
    ("An insurance premium paid to an insurer and a contingency provision carry no TDS.",
     "s.194D covers commission to agents, not premium paid to an insurer"),
    ("Thresholds are applied per line. Where a line falls below the threshold the product deducts "
     "nothing and flags that the payee's annual aggregate may still cross it.", "—"),
    ("Payee status defaults to a company/firm (the higher 194C rate). The producer can mark a line "
     "as individual/HUF per payee.", "s.194C(1) vs (2)"),
]

OPEN_QUESTIONS = [
    ("Are the rates and thresholds in tables 2 and 3 correct for FY 2026–27?",
     "They were read off public sources and every Finance Act moves some of them. This is the "
     "whole reason the product cannot quote them yet."),
    ("Is 194J at 10% the right default for a director, DOP or editor engaged per-project, or "
     "should some of those be 194JB at 2% as fees for technical services?",
     "The product splits on the description — a named craft person goes to 194J, a facility or "
     "process (online, grade, mix) goes to 194JB. We need to know if that split is defensible."),
    ("Equipment hire is routed to 194-I at 2% rather than 194C. Correct for a camera or lighting "
     "package that comes with an operator?",
     "The line is often a composite of kit and crew. If the invoice is not split, which section "
     "governs?"),
    ("A UK company with no place of business in India invoices an Indian production house for a "
     "diagnostic engagement. Is that an import of service with GST accounted for by the recipient "
     "under reverse charge, and is it correct to issue the invoice with no GST line?",
     "This is the invoicing question for `docs/pricing-basis.md` §3b, not a product question."),
    ("What does the Indian client withhold from that invoice under s.195, and what is needed to "
     "claim it under the UK–India DTAA?",
     "Affects what actually lands in the bank against a ₹1,50,000 invoice."),
]

WORKED_EXAMPLE = {
    "desc": "Director of photography", "amount": 225000, "gst_rate": 0.18, "code": "11302",
}


def money(n: float) -> str:
    """Indian digit grouping. ₹1,00,000 and ₹100,000 are the same number and only
    one of them reads as written by someone who works here."""
    n = int(round(n))
    if n < 1000:
        return f"₹{n}"
    head, tail = str(n)[:-3], str(n)[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return "₹" + ",".join(groups + [tail])


def main() -> int:
    today = date.today().isoformat()
    out: list[str] = []
    w = out.append

    w("# Tax review pack — for a chartered accountant")
    w("")
    w(f"**Generated {today} from `backend/compliance.py`.** Do not edit this file by hand — "
      "correct the tables in the code and regenerate with "
      "`python3 backend/ca_review_pack.py > docs/ca-review-pack.md`.")
    w("")
    w("## What this is, and what we need")
    w("")
    w("Mark builds production budgets for Indian film, TVC and OTT work, and computes GST and TDS "
      "on every line: gross, GST, the section and rate withheld, and the net payable to each "
      "vendor. The arithmetic is tested. **The rates, sections and thresholds below are defaults "
      "we read off public sources and no qualified person has confirmed them.**")
    w("")
    w("Until they are confirmed, every response the product returns carries a disclaimer and none "
      "of it goes to a client. We are not asking for an opinion or a filing — we are asking you to "
      "read six short tables and tell us which rows are wrong.")
    w("")
    w("Each row is a statement. Tick it, or write the correction next to it. The open questions at "
      "the end need a judgement rather than a number and are the only part likely to take real time.")
    w("")

    # ── 1 ──────────────────────────────────────────────────────────────
    w("## Table 1 — which TDS section each budget section defaults to")
    w("")
    w("Applied when the line's description carries no decisive signal (see table 4).")
    w("")
    w("| Budget section | What sits there | Defaults to | Correct? |")
    w("|---|---|---|---|")
    for code, section in sorted(c.SECTION_DEFAULTS.items()):
        name = SECTION_NAMES.get(code, "—")
        w(f"| {code} | {name} | `{section}` | ☐ |")
    w("")

    # ── 2 ──────────────────────────────────────────────────────────────
    w("## Table 2 — the rate applied for each section")
    w("")
    w("| Section | Description | Company / firm | Individual / HUF | Correct? |")
    w("|---|---|---|---|---|")
    for key, (entity, individual, label) in c.RULES.items():
        w(f"| `{key}` | {label} | {entity:.1%} | {individual:.1%} | ☐ |")
    w("")
    w("`194JB` is the product's internal name for fees for technical services under s.194J, kept "
      "separate from professional fees because the rate differs.")
    w("")

    # ── 3 ──────────────────────────────────────────────────────────────
    w("## Table 3 — thresholds below which nothing is deducted")
    w("")
    w("| Section | Threshold used | Basis assumed | Correct? |")
    w("|---|---|---|---|")
    basis = {
        "194C": "aggregate in a financial year, per payee",
        "194C_SINGLE": "single payment",
        "194J": "per financial year, per payee",
        "194JB": "per financial year, per payee",
        "194I_EQUIPMENT": "per financial year, per payee",
        "194I_PREMISES": "per financial year, per payee",
        "194Q": "buyer's turnover in the preceding year",
        "NONE": "not applicable",
    }
    for key, value in c.THRESHOLDS.items():
        w(f"| `{key}` | {money(value)} | {basis.get(key, '—')} | ☐ |")
    w("")

    # ── 4 ──────────────────────────────────────────────────────────────
    w("## Table 4 — how a line's wording picks a section")
    w("")
    w("Read top to bottom; the first match wins. This is the part most likely to be wrong in a way "
      "the tables above would not catch.")
    w("")
    w("| Order | If the description… | Routes to | Trigger words | Correct? |")
    w("|---|---|---|---|---|")
    for i, (test, section, words) in enumerate(KEYWORD_RULES, start=1):
        w(f"| {i} | {test} | `{section}` | {words} | ☐ |")
    w("")
    w("A rate-card row may also carry an explicit `tds_section`, which overrides all of the above.")
    w("")

    # ── 5 ──────────────────────────────────────────────────────────────
    w("## Table 5 — standing assumptions")
    w("")
    w("| # | Assumption | Basis we relied on | Correct? |")
    w("|---|---|---|---|")
    for i, (text, source) in enumerate(ASSUMPTIONS, start=1):
        w(f"| {i} | {text} | {source} | ☐ |")
    w("")

    # ── 6 ──────────────────────────────────────────────────────────────
    w("## Table 6 — one line, end to end")
    w("")
    w("If the arithmetic below is right and tables 1–5 are right, the product is right.")
    w("")
    line = c.compute_line(WORKED_EXAMPLE, section_code="11300")
    w("| Step | Value |")
    w("|---|---|")
    w(f"| Line | {WORKED_EXAMPLE['desc']} (budget section 11300, Camera) |")
    w(f"| Gross | {money(line['gross'])} |")
    w(f"| GST @ {line['gst_rate']:.0%} | {money(line['gst_amount'])} |")
    w(f"| Invoice total | {money(line['invoice_total'])} |")
    w(f"| TDS section chosen | `{line['tds_section']}` — {line['tds_label']} |")
    w(f"| Why that section | {line['basis']} |")
    w(f"| Rate applied | {line['tds_rate']:.1%} |")
    w(f"| TDS withheld (on the pre-GST value) | {money(line['tds_amount'])} |")
    w(f"| **Net payable to the vendor** | **{money(line['net_payable'])}** |")
    w("")
    if line["flags"]:
        w("Flags raised on this line:")
        w("")
        for f in line["flags"]:
            w(f"- {f}")
        w("")

    # ── open questions ─────────────────────────────────────────────────
    w("## Open questions")
    w("")
    for i, (q, why) in enumerate(OPEN_QUESTIONS, start=1):
        w(f"**{i}. {q}**")
        w("")
        w(f"{why}")
        w("")

    w("## Sign-off")
    w("")
    w("Nothing from Mark's tax computation is shown to a client until this is signed.")
    w("")
    w("| | |")
    w("|---|---|")
    w("| Reviewed by | «name, firm, membership no.» |")
    w("| Date | «date» |")
    w("| Scope | Tables 1–6 above, as generated on " + today + " |")
    w("| Corrections attached | ☐ none · ☐ marked on this document |")
    w("")
    w("<sub>This document describes how a software product computes indicative figures. It is not "
      "a filing, an opinion or a return, and the reviewer is not being asked to certify any "
      "taxpayer's position.</sub>")

    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
