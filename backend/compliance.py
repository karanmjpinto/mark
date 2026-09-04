"""
compliance.py — the Indian tax layer a production budget actually needs.

The US production-finance products (Saturation, Wrapbook, EP) all sell an
automatic fringe engine: union rates, pension and health, vacation, payroll tax,
computed per line. It is the thing producers pay for and it is worthless outside
North America. India's equivalent — GST treatment, TDS deduction under the right
section, and the net figure a vendor is actually paid — exists in no product at
any price. This module is that engine.

What it produces, per line: gross, GST, invoice total, the TDS section and rate,
the deduction, and the net payable. Rolled up: a payment schedule and a GST
input-credit view that separates what the production can reclaim from what it
cannot.

**Read this before it goes anywhere near a client.** Every rate and threshold
here is a default, held in `RULES` and `THRESHOLDS` so it can be corrected in one
place. Tax law changes every Finance Act and a wrong deduction is the client's
liability, not a rendering bug. So:

  * every response carries `disclaimer` and it is not optional;
  * every computed line carries a `basis` naming the rule applied;
  * anything the module is unsure of is flagged rather than assumed;
  * **a chartered accountant signs off `RULES` before this is quoted to anyone.**

Until that sign-off exists, treat the output as a structured question to ask an
accountant, not an answer.
"""

from __future__ import annotations

import os
import re
from typing import Any, Optional

# Flipped to "1" only after a chartered accountant has signed off the tables
# below — see `docs/ca-review-pack.md`, which is generated from them. Until then
# every response says `reviewed: false`, and any UI that shows a tax figure is
# expected to refuse to present it as final. A disclaimer nobody reads is not a
# control; a boolean the code can check is.
TAX_RULES_REVIEWED = os.getenv("TAX_RULES_REVIEWED", "").strip() in ("1", "true", "yes")
TAX_RULES_REVIEWED_BY = os.getenv("TAX_RULES_REVIEWED_BY", "").strip()

DISCLAIMER = (
    "Indicative computation only. Rates, sections and thresholds are defaults that must be "
    "confirmed against the current Finance Act and the payee's status by a qualified chartered "
    "accountant. This is not tax advice and must not be represented as an audit or an opinion."
)

# Payee status changes the 194C rate, and nothing else here.
PAYEE_INDIVIDUAL = "individual"   # individual / HUF
PAYEE_ENTITY = "entity"           # company, firm, LLP — the default for vendors

# section → (rate for entity, rate for individual, human label)
RULES: dict[str, tuple[float, float, str]] = {
    "194C": (0.02, 0.01, "Payments to contractors"),
    "194J": (0.10, 0.10, "Fees for professional services"),
    "194JB": (0.02, 0.02, "Fees for technical services"),
    "194I_EQUIPMENT": (0.02, 0.02, "Rent — plant, machinery or equipment"),
    "194I_PREMISES": (0.10, 0.10, "Rent — land, building or furniture"),
    "194Q": (0.001, 0.001, "Purchase of goods (buyer turnover threshold applies)"),
    "NONE": (0.0, 0.0, "No TDS applicable on this payment type"),
}

# Per-payee annual thresholds below which no deduction is required. Defaults
# only — these move with almost every Finance Act.
THRESHOLDS: dict[str, float] = {
    "194C": 100000.0,          # aggregate in a financial year (single payment limit also applies)
    "194C_SINGLE": 30000.0,
    "194J": 50000.0,
    "194JB": 50000.0,
    "194I_EQUIPMENT": 240000.0,
    "194I_PREMISES": 240000.0,
    "194Q": 5000000.0,
    "NONE": 0.0,
}

NO_PAN_RATE = 0.20  # s.206AA — the one rule here that has been stable for years

# GST input tax credit is blocked on some heads under s.17(5). For a production
# company these two are the ones that bite, and both are routinely missed.
ITC_BLOCKED_KEYWORDS = {
    "catering": "s.17(5)(b) — food, beverages and outdoor catering: ITC generally blocked",
    "food": "s.17(5)(b) — food and beverages: ITC generally blocked",
    "meal": "s.17(5)(b) — food and beverages: ITC generally blocked",
    "cab": "s.17(5)(b) — passenger transport hire: ITC generally blocked",
    "taxi": "s.17(5)(b) — passenger transport hire: ITC generally blocked",
}

_PROFESSIONAL = re.compile(
    r"\b(director|dop|cinematograph|editor|colour|color|colourist|composer|music|writer|"
    r"designer|stylist|makeup|make-up|hair|sound design|line producer|producer|"
    r"assistant director|choreograph|consultant|artist|actor|talent|model)\b", re.I)
_TECHNICAL = re.compile(
    r"\b(online|conform|grade|di\b|mix|foley|dubbing|vfx|render|studio time|facility|"
    r"post facility|telecine|mastering)\b", re.I)
# "package" on its own is too loose — "production package insurance" is not a kit
# hire. It only counts as equipment when it is a kit package.
_EQUIPMENT_RENT = re.compile(
    r"\b(rental|rent|hire|kit|equipment|generator|crane|jimmy|gimbal|drone unit|"
    r"lens|monitor)\b|\b(?:camera|lighting|grip|light|sound|lens|art)\s*\+?\s*\w*\s*package\b", re.I)
_PREMISES_RENT = re.compile(
    r"\b(studio floor|stage hire|floor hire|location fee|bungalow|apartment|premises|"
    r"office|godown|warehouse)\b", re.I)
_GOODS = re.compile(r"\b(purchase|buy|consumable|stock|material|raw)\b", re.I)
_TRANSPORT = re.compile(r"\b(transport|vehicle|van|bus|car|tempo|logistics|carriage)\b", re.I)

# Section-code defaults, applied when the description is not decisive.
SECTION_DEFAULTS: dict[str, str] = {
    "10000": "194J",   # development
    "10300": "194J",   # director
    "10600": "194J",   # director team
    "10700": "194C",   # extras, usually via a coordinator
    "10800": "194C",   # production staff — overridden to 194J for named professionals
    "11000": "194C",   # art department
    "11200": "194C",   # stunts
    "11300": "194C",   # camera
    "11400": "194J",   # sound
    "11500": "194C",   # lighting
    "11800": "194C",   # wardrobe
    "11900": "194J",   # choreography
    "12000": "194J",   # makeup & hair
    "12300": "194C",   # transport
    "12400": "194I_PREMISES",  # location
    "12600": "194C",   # catering
    "12800": "194C",   # travel
    "12900": "194J",   # editorial
    "13100": "194J",   # post sound
    "13300": "194JB",  # vfx — technical services
    "13700": "NONE",   # insurance premium to an insurer
    "14000": "NONE",   # contingency
}


def tds_rule_for(section_code: str, desc: str, *, sub: str = "",
                 declared_section: str = "") -> tuple[str, str]:
    """Pick the TDS section for one budget line. Returns (section, basis).

    A rate-card row that already carries `tds_section` wins — a producer who
    corrected the rate has usually corrected the treatment too.
    """
    if declared_section:
        key = declared_section.upper().replace("-", "_").replace(" ", "")
        if key == "194I":
            key = "194I_EQUIPMENT"
        if key in RULES:
            return key, "section declared on the rate-card row"

    text = f"{desc} {sub}".strip()
    code = str(section_code or "")

    # Sections that are never a payment for work or rent: an insurance premium
    # paid to an insurer and a contingency provision. These short-circuit the
    # keyword rules below — "production package insurance" is not a kit hire.
    if SECTION_DEFAULTS.get(code) == "NONE":
        return "NONE", f"section {code} is not a payment for work, services or rent"

    if _PREMISES_RENT.search(text):
        return "194I_PREMISES", "description reads as rent of premises"
    if _EQUIPMENT_RENT.search(text) and not _PROFESSIONAL.search(text):
        return "194I_EQUIPMENT", "description reads as equipment or kit hire"
    if _TECHNICAL.search(text):
        return "194JB", "description reads as a technical/facility service"
    if _PROFESSIONAL.search(text):
        return "194J", "description names a professional engagement"
    if _GOODS.search(text) and not _TRANSPORT.search(text):
        return "194Q", "description reads as a purchase of goods"
    if code in SECTION_DEFAULTS:
        return SECTION_DEFAULTS[code], f"default for budget section {code}"
    return "194C", "no decisive signal — defaulted to contractor"


def compute_line(item: dict, *, section_code: str = "", payee_type: str = PAYEE_ENTITY,
                 has_pan: bool = True, apply_thresholds: bool = True) -> dict:
    """Gross → GST → TDS → net payable, for one budget line.

    Two rules worth naming, because both are routinely got wrong by hand:

      * TDS is deducted on the value **excluding** GST where GST is shown
        separately on the invoice (CBDT Circular 23/2017). Deducting on the
        GST-inclusive figure over-deducts by the tax rate.
      * Without a valid PAN, s.206AA forces 20% regardless of section.
    """
    gross = float(item.get("amount") or 0)
    gst_rate = float(item.get("gst_rate") or 0)
    gst_amount = round(gross * gst_rate, 2)
    invoice_total = round(gross + gst_amount, 2)

    tds_section, basis = tds_rule_for(section_code, item.get("desc", ""),
                                      sub=item.get("sub", ""),
                                      declared_section=item.get("tds_section", ""))
    entity_rate, individual_rate, label = RULES[tds_section]
    rate = individual_rate if payee_type == PAYEE_INDIVIDUAL else entity_rate

    flags: list[str] = []
    applied_rate = rate
    if not has_pan and tds_section != "NONE":
        applied_rate = NO_PAN_RATE
        flags.append("no PAN on file — s.206AA forces 20%")
    elif apply_thresholds and rate and gross < THRESHOLDS.get(tds_section, 0):
        applied_rate = 0.0
        flags.append(
            f"below the {THRESHOLDS[tds_section]:,.0f} annual threshold for {tds_section} on this line "
            f"— deduct anyway if the payee's aggregate for the year crosses it")

    if tds_section == "194C" and _TRANSPORT.search(f"{item.get('desc','')} {item.get('sub','')}"):
        flags.append("transporter: no deduction under s.194C(6) if the vendor declares "
                     "≤10 goods carriages and furnishes PAN")

    tds_amount = round(gross * applied_rate, 2)  # on the pre-GST value
    return {
        "code": item.get("code"),
        "desc": item.get("desc"),
        "gross": round(gross, 2),
        "gst_rate": gst_rate,
        "gst_amount": gst_amount,
        "invoice_total": invoice_total,
        "tds_section": tds_section,
        "tds_label": label,
        "tds_rate": applied_rate,
        "tds_amount": tds_amount,
        "net_payable": round(invoice_total - tds_amount, 2),
        "payee_type": payee_type,
        "basis": basis,
        "flags": flags,
    }


def review_status() -> dict:
    """The block every tax response carries.

    `reviewed` is the field a caller should branch on. While it is false the
    figures are a structured question for an accountant, not an answer, and no
    client-facing document may present them as final.
    """
    return {
        "reviewed": TAX_RULES_REVIEWED,
        "reviewed_by": TAX_RULES_REVIEWED_BY or None,
        "disclaimer": DISCLAIMER if not TAX_RULES_REVIEWED else (
            f"Rates and sections reviewed by {TAX_RULES_REVIEWED_BY or 'a qualified reviewer'}. "
            "Still indicative: confirm the payee's status and annual aggregates before deducting."),
    }


def _itc_block_reason(desc: str, sub: str = "") -> Optional[str]:
    text = f"{desc} {sub}".lower()
    for kw, reason in ITC_BLOCKED_KEYWORDS.items():
        if kw in text:
            return reason
    return None


def compute_budget(budget: dict, *, payee_types: Optional[dict] = None,
                   apply_thresholds: bool = True) -> dict:
    """The whole budget, line by line, with GST and TDS resolved.

    `payee_types` maps a line code to `individual` where the producer knows the
    payee is an individual or HUF — the only input that changes a 194C rate.
    """
    payee_types = payee_types or {}
    lines: list[dict] = []
    itc_blocked: list[dict] = []

    for section in budget.get("sections") or []:
        code = str(section.get("code") or "")
        for item in section.get("items") or []:
            payee = payee_types.get(str(item.get("code")), PAYEE_ENTITY)
            row = compute_line(item, section_code=code, payee_type=payee,
                               apply_thresholds=apply_thresholds)
            row["section"] = code
            row["section_name"] = section.get("name") or ""
            reason = _itc_block_reason(item.get("desc", ""), item.get("sub", ""))
            row["itc_blocked"] = bool(reason)
            row["itc_note"] = reason or ""
            if reason:
                itc_blocked.append({"code": row["code"], "desc": row["desc"],
                                    "gst_amount": row["gst_amount"], "reason": reason})
            lines.append(row)

    by_section: dict[str, dict] = {}
    for l in lines:
        agg = by_section.setdefault(l["section"], {
            "section": l["section"], "section_name": l["section_name"],
            "gross": 0.0, "gst_amount": 0.0, "tds_amount": 0.0, "net_payable": 0.0,
        })
        for k in ("gross", "gst_amount", "tds_amount", "net_payable"):
            agg[k] += l[k]
    for agg in by_section.values():
        for k in ("gross", "gst_amount", "tds_amount", "net_payable"):
            agg[k] = round(agg[k], 2)

    by_tds: dict[str, dict] = {}
    for l in lines:
        if not l["tds_amount"]:
            continue
        agg = by_tds.setdefault(l["tds_section"], {
            "tds_section": l["tds_section"], "label": l["tds_label"],
            "lines": 0, "base": 0.0, "tds_amount": 0.0,
        })
        agg["lines"] += 1
        agg["base"] += l["gross"]
        agg["tds_amount"] += l["tds_amount"]
    for agg in by_tds.values():
        agg["base"] = round(agg["base"], 2)
        agg["tds_amount"] = round(agg["tds_amount"], 2)

    gst_total = sum(l["gst_amount"] for l in lines)
    gst_blocked = sum(b["gst_amount"] for b in itc_blocked)
    return {
        "lines": lines,
        "by_section": sorted(by_section.values(), key=lambda s: s["section"]),
        "by_tds_section": sorted(by_tds.values(), key=lambda s: -s["tds_amount"]),
        "totals": {
            "gross": round(sum(l["gross"] for l in lines), 2),
            "gst": round(gst_total, 2),
            "invoice_total": round(sum(l["invoice_total"] for l in lines), 2),
            "tds": round(sum(l["tds_amount"] for l in lines), 2),
            "net_payable": round(sum(l["net_payable"] for l in lines), 2),
        },
        "gst_credit": {
            "total_gst": round(gst_total, 2),
            "creditable": round(gst_total - gst_blocked, 2),
            "blocked": round(gst_blocked, 2),
            "blocked_lines": itc_blocked,
            "note": "Input tax credit availability depends on the production company's own "
                    "registration and outward supplies. Blocked heads are flagged, not netted off.",
        },
        "flags": sorted({f for l in lines for f in l["flags"]}),
        **review_status(),
    }


def payment_schedule(budget: dict, *, advance_pct: float = 0.4,
                     payee_types: Optional[dict] = None) -> dict:
    """Split the computed budget into an advance and a balance.

    Indian productions are cash-timing problems as much as cost problems: the
    advance goes out before the shoot and the balance clears 30–90 days after,
    and TDS is deducted at each payment. A budget that shows only a total tells a
    producer nothing about the week they will be short.
    """
    computed = compute_budget(budget, payee_types=payee_types)
    advance_pct = max(0.0, min(1.0, advance_pct))

    stages = []
    for label, share in (("advance", advance_pct), ("balance", 1 - advance_pct)):
        if share <= 0:
            continue
        stages.append({
            "stage": label,
            "share": round(share, 4),
            "gross": round(computed["totals"]["gross"] * share, 2),
            "gst": round(computed["totals"]["gst"] * share, 2),
            "tds": round(computed["totals"]["tds"] * share, 2),
            "net_payable": round(computed["totals"]["net_payable"] * share, 2),
            "timing": "on award / before first shoot day" if label == "advance"
                      else "on delivery, per agreed credit terms",
        })

    return {
        "stages": stages,
        "totals": computed["totals"],
        "by_tds_section": computed["by_tds_section"],
        "note": "TDS is deducted at each payment, not once at the end. Certificates "
                "(Form 16A) follow the quarterly return for the quarter the deduction falls in.",
        **review_status(),
    }
