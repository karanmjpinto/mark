"""
test_compliance.py — the India GST/TDS engine, asserted.

Run: python3 evals/test_compliance.py

These tests pin behaviour, not tax law. The rates and thresholds in
`compliance.RULES` / `compliance.THRESHOLDS` are defaults awaiting a chartered
accountant's sign-off; what is asserted here is that the engine applies whatever
is in those tables correctly, deducts on the pre-GST value, never drops the
disclaimer, and flags rather than assumes.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import compliance  # noqa: E402


BUDGET = {
    "sections": [
        {"code": "11300", "name": "CAMERA", "items": [
            {"code": "11301", "desc": "Camera package rental — Alexa 35", "sub": "3 days",
             "amount": 180000, "gst_rate": 0.18},
            {"code": "11302", "desc": "Director of photography", "sub": "3 days",
             "amount": 225000, "gst_rate": 0.18},
        ]},
        {"code": "12400", "name": "LOCATION", "items": [
            {"code": "12401", "desc": "Studio floor hire", "sub": "2 days",
             "amount": 300000, "gst_rate": 0.18},
        ]},
        {"code": "12600", "name": "CATERING", "items": [
            {"code": "12601", "desc": "Crew catering", "sub": "3 days", "amount": 150000,
             "gst_rate": 0.05},
        ]},
    ]
}


def test_tds_is_deducted_on_the_pre_gst_value():
    line = compliance.compute_line(
        {"code": "x", "desc": "Director of photography", "amount": 100000, "gst_rate": 0.18})
    assert line["gst_amount"] == 18000
    assert line["invoice_total"] == 118000
    # 10% of 100000, not of 118000
    assert line["tds_amount"] == 10000
    assert line["net_payable"] == 108000


def test_professional_service_reads_as_194J():
    section, basis = compliance.tds_rule_for("11300", "Director of photography")
    assert section == "194J" and basis


def test_equipment_hire_reads_as_rent_not_contract():
    section, _ = compliance.tds_rule_for("11300", "Camera package rental — Alexa 35")
    assert section == "194I_EQUIPMENT"
    assert compliance.RULES[section][0] == 0.02


def test_premises_hire_reads_as_the_higher_rent_rate():
    section, _ = compliance.tds_rule_for("12400", "Studio floor hire")
    assert section == "194I_PREMISES"
    assert compliance.RULES[section][0] == 0.10


def test_technical_facility_service_separates_from_professional():
    assert compliance.tds_rule_for("12900", "Online / conform, per finished film")[0] == "194JB"
    assert compliance.tds_rule_for("12900", "Offline editor + suite")[0] == "194J"


def test_declared_section_on_the_rate_row_wins():
    section, basis = compliance.tds_rule_for("11300", "Something ambiguous",
                                             declared_section="194C")
    assert section == "194C" and "rate-card" in basis


def test_individual_payee_gets_the_lower_194C_rate():
    item = {"code": "y", "desc": "Spot boys", "amount": 200000, "gst_rate": 0.18}
    entity = compliance.compute_line(item, section_code="10800")
    individual = compliance.compute_line(item, section_code="10800",
                                         payee_type=compliance.PAYEE_INDIVIDUAL)
    assert entity["tds_section"] == "194C"
    assert entity["tds_rate"] == 0.02 and individual["tds_rate"] == 0.01


def test_no_pan_forces_twenty_percent():
    line = compliance.compute_line(
        {"code": "z", "desc": "Director of photography", "amount": 100000, "gst_rate": 0.18},
        has_pan=False)
    assert line["tds_rate"] == 0.20 and line["tds_amount"] == 20000
    assert any("206AA" in f for f in line["flags"])


def test_below_threshold_suppresses_but_warns():
    line = compliance.compute_line(
        {"code": "a", "desc": "Spot boys", "amount": 5000, "gst_rate": 0.18},
        section_code="10800")
    assert line["tds_amount"] == 0
    assert any("threshold" in f for f in line["flags"]), line["flags"]


def test_thresholds_can_be_turned_off_for_a_repeat_vendor():
    line = compliance.compute_line(
        {"code": "a", "desc": "Spot boys", "amount": 5000, "gst_rate": 0.18},
        section_code="10800", apply_thresholds=False)
    assert line["tds_amount"] == 100  # 2% of 5000


def test_transporter_exemption_is_flagged_not_applied():
    line = compliance.compute_line(
        {"code": "b", "desc": "Crew bus and production van", "amount": 200000, "gst_rate": 0.12},
        section_code="12300")
    assert line["tds_section"] == "194C"
    assert line["tds_amount"] > 0, "the exemption needs a vendor declaration — never assumed"
    assert any("194C(6)" in f for f in line["flags"])


def test_insurance_and_contingency_take_no_deduction():
    for code, desc in (("13700", "Production package insurance"), ("14000", "Contingency")):
        line = compliance.compute_line({"code": code, "desc": desc, "amount": 100000,
                                        "gst_rate": 0.18}, section_code=code)
        assert line["tds_section"] == "NONE" and line["tds_amount"] == 0


def test_catering_itc_is_blocked_and_reported_separately():
    out = compliance.compute_budget(BUDGET)
    blocked = out["gst_credit"]["blocked_lines"]
    assert [b["code"] for b in blocked] == ["12601"]
    assert out["gst_credit"]["blocked"] == 7500  # 5% of 150000
    assert out["gst_credit"]["creditable"] == round(out["gst_credit"]["total_gst"] - 7500, 2)
    # blocked credit is reported, never netted off the payable
    assert out["totals"]["gst"] == out["gst_credit"]["total_gst"]


def test_budget_totals_reconcile():
    out = compliance.compute_budget(BUDGET)
    lines = out["lines"]
    assert out["totals"]["gross"] == round(sum(l["gross"] for l in lines), 2)
    assert out["totals"]["invoice_total"] == round(
        out["totals"]["gross"] + out["totals"]["gst"], 2)
    assert out["totals"]["net_payable"] == round(
        out["totals"]["invoice_total"] - out["totals"]["tds"], 2)


def test_every_line_carries_a_basis_and_the_response_carries_the_disclaimer():
    out = compliance.compute_budget(BUDGET)
    assert all(l["basis"] for l in out["lines"])
    assert "not tax advice" in out["disclaimer"]


def test_tds_rolls_up_by_section():
    out = compliance.compute_budget(BUDGET)
    by_section = {r["tds_section"]: r for r in out["by_tds_section"]}
    assert "194I_PREMISES" in by_section
    assert by_section["194I_PREMISES"]["tds_amount"] == 30000  # 10% of 300000
    assert sum(r["tds_amount"] for r in out["by_tds_section"]) == out["totals"]["tds"]


def test_payment_schedule_splits_and_reconciles():
    ps = compliance.payment_schedule(BUDGET, advance_pct=0.4)
    assert [s["stage"] for s in ps["stages"]] == ["advance", "balance"]
    assert round(sum(s["share"] for s in ps["stages"]), 4) == 1.0
    assert round(sum(s["net_payable"] for s in ps["stages"]), 0) == round(ps["totals"]["net_payable"], 0)
    assert "not tax advice" in ps["disclaimer"]


def test_full_advance_produces_one_stage():
    ps = compliance.payment_schedule(BUDGET, advance_pct=1.0)
    assert len(ps["stages"]) == 1 and ps["stages"][0]["stage"] == "advance"


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ✅ {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  ❌ {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ❌ {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n compliance: {len(tests) - failed} passed · {failed} failed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run() else 0)
