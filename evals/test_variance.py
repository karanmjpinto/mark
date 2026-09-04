"""
test_variance.py — the teardown, asserted.

Run: python3 evals/test_variance.py

The rules being pinned here are the ones that decide whether a Teardown Report
is defensible in a readout: unbudgeted spend is never absorbed into a total, a
classification is never asserted without a basis, and nothing is called
recurring from a single production.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import variance  # noqa: E402


BUDGET = {
    "title": "TEST TVC",
    "shoot_days": 3,
    "sections": [
        {"code": "11300", "name": "CAMERA", "type": "below_the_line", "items": [
            {"code": "11301", "desc": "Camera package rental", "sub": "3 days × 1 unit",
             "amount": 180000, "gst_rate": 0.18, "conf": "green", "item_key": "camera.package.alexa35"},
            {"code": "11302", "desc": "Focus puller", "sub": "3 days",
             "amount": 36000, "gst_rate": 0.18, "conf": "green", "item_key": "focus_puller"},
        ]},
        {"code": "12400", "name": "LOCATION HIRE", "type": "below_the_line", "items": [
            {"code": "12401", "desc": "Bungalow location fee", "sub": "2 days",
             "amount": 300000, "gst_rate": 0.18, "conf": "amber", "item_key": "location.bungalow"},
        ]},
        {"code": "12600", "name": "CATERING", "type": "below_the_line", "items": [
            {"code": "12601", "desc": "Crew catering", "sub": "", "amount": 90000,
             "gst_rate": 0.05, "conf": "amber", "item_key": "catering.crew_meal"},
        ]},
    ],
}

ACTUALS = [
    # same quantity, higher unit rate → vendor variance
    {"code": "11301", "desc": "Camera package rental", "amount": 225000, "qty": 3, "vendor": "Kit House"},
    # on budget
    {"code": "11302", "desc": "Focus puller", "amount": 36000, "qty": 3},
    # quantity moved 2 → 3 days → scope change
    {"code": "12401", "desc": "Bungalow location fee", "amount": 465000, "qty": 3},
    # spent with no budget line at all
    {"desc": "Police bandobast + society charges", "amount": 85000, "vendor": "Local liaison"},
    # catering never spent
]


def _ledger():
    return variance.build_ledger(BUDGET, ACTUALS, currency="INR", production="TEST TVC")


def test_amount_cleaning_handles_indian_formatting():
    assert variance._clean_number("₹1,80,000") == 180000
    assert variance._clean_number("(4,500)") == -4500
    assert variance._clean_number("2,25,000.50 CR") == 225000.50
    assert variance._clean_number("") is None
    assert variance._clean_number(None) is None


def test_subtotal_rows_are_dropped():
    rows = variance.normalise_actuals([
        {"desc": "Camera package", "amount": "100"},
        {"desc": "TOTAL", "amount": "100"},
        {"desc": "Total camera", "amount": "100"},
        {"desc": "", "amount": "50"},          # no desc and no code
        {"desc": "Grip", "amount": "not a number"},
    ])
    assert [r["desc"] for r in rows] == ["Camera package"], rows


def test_csv_parsing():
    csv_text = "Code,Particulars,Amount\n11301,Camera package rental,\"2,25,000\"\n,TOTAL,225000\n"
    rows = variance.parse_actuals_csv(csv_text)
    assert len(rows) == 1
    assert rows[0]["code"] == "11301" and rows[0]["amount"] == 225000


def _tiny_xlsx() -> bytes:
    """Build a minimal .xlsx in memory — enough to exercise the reader without
    adding a spreadsheet dependency to the eval suite."""
    import io as _io
    import zipfile

    shared = ["Cost report — TEST TVC", "Code", "Particulars", "Amount",
              "Camera package rental", "Focus puller"]
    ss = ("<sst>" + "".join(f"<si><t>{s}</t></si>" for s in shared) + "</sst>")
    sheet = (
        "<worksheet><sheetData>"
        '<row r="1"><c r="A1" t="s"><v>0</v></c></row>'
        '<row r="2"><c r="A2" t="s"><v>1</v></c><c r="B2" t="s"><v>2</v></c>'
        '<c r="C2" t="s"><v>3</v></c></row>'
        '<row r="3"><c r="A3"><v>11301</v></c><c r="B3" t="s"><v>4</v></c>'
        '<c r="C3"><v>225000</v></c></row>'
        '<row r="4"><c r="A4"><v>11302</v></c><c r="B4" t="s"><v>5</v></c>'
        '<c r="C4"><v>36000</v></c></row>'
        "</sheetData></worksheet>"
    )
    buf = _io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/sharedStrings.xml", ss)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


def test_xlsx_reader_finds_the_header_below_a_title_row():
    rows = variance.parse_actuals_xlsx(_tiny_xlsx())
    assert len(rows) == 2, rows
    assert rows[0]["code"] == "11301" and rows[0]["amount"] == 225000
    assert rows[1]["desc"] == "Focus puller"


def test_xlsx_preserves_column_positions():
    raw = variance.xlsx_rows(_tiny_xlsx())
    assert raw[1] == ["Code", "Particulars", "Amount"]
    assert raw[2][0] == "11301" and raw[2][2] == "225000"


def test_unbudgeted_spend_is_its_own_status():
    led = _ledger()
    unbudgeted = [l for l in led["lines"] if l["status"] == variance.STATUS_UNBUDGETED]
    assert len(unbudgeted) == 1
    assert unbudgeted[0]["classification"] == variance.CLASS_UNRECORDED
    assert led["totals"]["unbudgeted_spend"] == 85000
    # and it must not be silently folded into the budget total
    assert led["totals"]["budget"] == 180000 + 36000 + 300000 + 90000


def test_quantity_move_reads_as_scope_change():
    led = _ledger()
    line = next(l for l in led["lines"] if l["code"] == "12401")
    assert line["classification"] == variance.CLASS_SCOPE
    assert "quantity moved" in line["basis"]


def test_rate_move_reads_as_vendor_variance():
    led = _ledger()
    line = next(l for l in led["lines"] if l["code"] == "11301")
    assert line["classification"] == variance.CLASS_VENDOR
    assert "unit rate moved" in line["basis"]


def test_on_budget_lines_are_not_material():
    led = _ledger()
    line = next(l for l in led["lines"] if l["code"] == "11302")
    assert line["status"] == variance.STATUS_ON_BUDGET
    assert line["material"] is False
    assert line["classification"] is None


def test_budgeted_but_unspent_is_surfaced():
    led = _ledger()
    line = next(l for l in led["lines"] if l["code"] == "12601")
    assert line["status"] == variance.STATUS_NOT_SPENT
    assert led["totals"]["unspent_budget"] == 90000


def test_every_material_line_carries_a_basis():
    led = _ledger()
    for line in led["material_lines"]:
        assert line["classification"], line
        assert line["basis"], f"{line['code']} classified with no evidence"


def test_actual_quantity_is_recorded_for_rate_derivation():
    led = _ledger()
    loc = next(l for l in led["lines"] if l["code"] == "12401")
    assert loc["quantity"] == 2 and loc["actual_quantity"] == 3
    catering = next(l for l in led["lines"] if l["code"] == "12601")
    assert catering["actual_quantity"] is None


def test_producer_override_wins():
    led = variance.build_ledger(BUDGET, ACTUALS, overrides={"11301": variance.CLASS_SCOPE})
    line = next(l for l in led["lines"] if l["code"] == "11301")
    assert line["classification"] == variance.CLASS_SCOPE
    assert "producer" in line["basis"]


def test_totals_reconcile():
    led = _ledger()
    assert led["totals"]["actual"] == sum(l["actual"] for l in led["lines"])
    assert led["totals"]["delta"] == round(led["totals"]["actual"] - led["totals"]["budget"], 2)


def test_section_rollup_covers_every_line():
    led = _ledger()
    assert sum(s["lines"] for s in led["sections"]) == len(led["lines"])


def test_description_matching_without_codes():
    actuals = [{"desc": "Rental of camera package", "amount": 200000}]
    led = variance.build_ledger(BUDGET, actuals)
    line = next(l for l in led["lines"] if l["code"] == "11301")
    assert line["actual"] == 200000, "description matching failed to find the camera line"


def test_nothing_is_recurring_from_one_production():
    assert variance.recurring_patterns([_ledger()]) == []


def test_recurring_needs_a_consistent_direction():
    over = _ledger()
    under_actuals = [dict(a) for a in ACTUALS]
    under_actuals[0]["amount"] = 100000  # camera under budget this time
    under = variance.build_ledger(BUDGET, under_actuals, production="SECOND TVC")
    keys = {p["key"] for p in variance.recurring_patterns([over, under])}
    camera = next((l for l in over["lines"] if l["code"] == "11301"), None)
    assert variance._pattern_key(camera) not in keys, "a line that moved both ways is not a pattern"


def test_recurring_pattern_across_two_productions():
    a = _ledger()
    b = variance.build_ledger(BUDGET, ACTUALS, production="SECOND TVC")
    patterns = variance.recurring_patterns([a, b])
    assert patterns, "identical overruns across two productions must register"
    p = patterns[0]
    assert p["productions"] == 2 and p["direction"] == "over"


def test_recurring_keeps_unbudgeted_spend():
    """A cost with no budget line has no percentage to be material against. If it
    is spent on every production it is the most systemic finding there is, so it
    must survive into the pattern list."""
    a = _ledger()
    b = variance.build_ledger(BUDGET, ACTUALS, production="SECOND TVC")
    patterns = variance.recurring_patterns([a, b])
    police = next((p for p in patterns if "bandobast" in p["desc"].lower()), None)
    assert police is not None, [p["desc"] for p in patterns]
    assert police["avg_delta_pct"] is None and police["avg_delta"] == 85000
    assert police["classifications"] == [variance.CLASS_UNRECORDED]


def test_annualise_states_its_method():
    a = _ledger()
    b = variance.build_ledger(BUDGET, ACTUALS, production="SECOND TVC")
    out = variance.annualise([a, b], productions_per_year=20)
    assert out["sample_productions"] == 2
    assert out["recurring_cost_per_year"] == round(out["recurring_cost_per_production"] * 20, 2)
    assert "method" in out and "20" in out["method"]
    assert out["caveats"]


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
    print(f"\n variance: {len(tests) - failed} passed · {failed} failed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run() else 0)
