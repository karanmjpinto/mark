"""
test_exporters.py — interop, asserted.

Run: python3 evals/test_exporters.py

The xlsx writer is hand-rolled against the OOXML spec, so the tests do two
things: check every part of the package is well-formed XML (a malformed part
produces a file Excel refuses to open with no useful error), and round-trip the
numbers back through the reader in `variance.py`.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import exporters  # noqa: E402
import variance  # noqa: E402


BUDGET = {
    "title": "NIKE — RUN <TEST> & CO",   # deliberately contains XML-hostile characters
    "production_type": "TVC",
    "shoot_days": 3,
    "scale_tier": "mid",
    "sections": [
        {"code": "11300", "name": "CAMERA", "type": "below_the_line", "items": [
            {"code": "11301", "desc": "Camera package rental", "sub": "3 days × 1 unit",
             "amount": 180000, "gst_rate": 0.18, "conf": "green"},
            {"code": "11302", "desc": "Focus puller", "sub": "3 days",
             "amount": 36000, "gst_rate": 0.18, "conf": "green"},
        ]},
        {"code": "12600", "name": "CATERING", "type": "below_the_line", "items": [
            {"code": "12601", "desc": "Crew catering", "sub": "", "amount": 90000,
             "gst_rate": 0.05, "conf": "amber", "note": "Confirm headcount"},
        ]},
    ],
    "excluded": ["Principal cast fees"],
    "flags": ["Crew size not stated"],
}


def _parts(raw: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(BytesIO(raw)) as z:
        return {n: z.read(n) for n in z.namelist()}


def test_xlsx_package_has_every_required_part():
    parts = _parts(exporters.to_xlsx(BUDGET))
    for required in ("[Content_Types].xml", "_rels/.rels", "xl/workbook.xml",
                     "xl/_rels/workbook.xml.rels", "xl/styles.xml", "xl/worksheets/sheet1.xml"):
        assert required in parts, f"missing {required}"


def test_every_part_is_well_formed_xml():
    for name, blob in _parts(exporters.to_xlsx(BUDGET)).items():
        try:
            ET.fromstring(blob)
        except ET.ParseError as e:
            raise AssertionError(f"{name} is not well-formed: {e}")


def test_hostile_characters_are_escaped_not_dropped():
    sheet = _parts(exporters.to_xlsx(BUDGET))["xl/worksheets/sheet1.xml"].decode()
    assert "&lt;TEST&gt;" in sheet and "&amp;" in sheet
    assert "<TEST>" not in sheet.replace("&lt;TEST&gt;", "")


def test_numbers_round_trip_through_the_reader():
    rows = variance.xlsx_rows(exporters.to_xlsx(BUDGET))
    flat = [c for row in rows for c in row]
    assert "180000" in flat and "36000" in flat and "90000" in flat
    # tax carried per line: 18% of 180000
    assert "32400.0" in flat or "32400" in flat


def test_totals_reconcile_in_the_sheet():
    rows = exporters.budget_rows(BUDGET)
    labels = {}
    for row in rows:
        vals = [c[0] if isinstance(c, tuple) else c for c in row]
        for i, v in enumerate(vals):
            if v in ("Subtotal", "Tax", "Total"):
                labels[v] = next((x for x in vals[i + 1:] if isinstance(x, (int, float))), None)
    assert labels["Subtotal"] == 306000
    assert labels["Tax"] == round(180000 * .18 + 36000 * .18 + 90000 * .05, 2)
    assert round(labels["Total"], 2) == round(labels["Subtotal"] + labels["Tax"], 2)


def test_section_subtotals_present():
    rows = exporters.budget_rows(BUDGET)
    text = [str(c[0] if isinstance(c, tuple) else c) for row in rows for c in row]
    assert "CAMERA subtotal" in text and "CATERING subtotal" in text


def test_ledger_export_is_valid_and_carries_the_evidence_column():
    ledger = variance.build_ledger(BUDGET, [
        {"code": "11301", "desc": "Camera package rental", "amount": 225000, "qty": 3},
        {"desc": "Police bandobast", "amount": 40000},
    ], production="TEST")
    raw = exporters.ledger_to_xlsx(ledger)
    for name, blob in _parts(raw).items():
        ET.fromstring(blob)
    flat = [c for row in variance.xlsx_rows(raw) for c in row]
    assert "Evidence" in flat and "unbudgeted" in flat
    assert any("unit rate moved" in c for c in flat)


def test_mm_interchange_splits_units_and_rate():
    text = exporters.to_mm_interchange(BUDGET)
    lines = [l.split(",") for l in text.strip().split("\n")]
    assert lines[0] == exporters.MM_INTERCHANGE_HEADER
    camera = next(l for l in lines if l[0] == "11301")
    assert camera[2] == "3.0", camera          # units pulled from "3 days × 1 unit"
    assert camera[4] == "60000.00"             # rate derived
    assert camera[5] == "180000.00"            # total unchanged


def test_mm_interchange_flat_line_imports_as_one_unit():
    text = exporters.to_mm_interchange(BUDGET)
    catering = next(l.split(",") for l in text.strip().split("\n") if l.startswith("12601"))
    assert catering[2] == "1" and catering[5] == "90000.00"


def test_mm_interchange_marks_section_rows():
    text = exporters.to_mm_interchange(BUDGET)
    section = next(l.split(",") for l in text.strip().split("\n") if l.startswith("11300"))
    assert section[1] == "CAMERA" and section[-1] == "section"


def test_roundtrip_export_then_import():
    raw = exporters.to_xlsx(BUDGET)
    back = exporters.from_xlsx(raw, title="Round trip")
    total = sum(i["amount"] for s in back["sections"] for i in s["items"])
    assert total == 306000, total
    assert back["imported"]["lines"] >= 3
    assert any("tax rates default to 0" in f for f in back["flags"])


def test_import_refuses_a_sheet_with_no_header():
    raw = exporters.write_xlsx([["some notes"], ["nothing structured here"]])
    out = exporters.from_xlsx(raw)
    assert out["sections"] == []
    assert any("No header row" in f for f in out["flags"])


def test_import_treats_a_code_with_no_amount_as_a_section():
    raw = exporters.write_xlsx([
        ["Code", "Description", "Amount"],
        ["11300", "CAMERA", ""],
        ["11301", "Camera package", "180000"],
        ["12600", "CATERING", ""],
        ["12601", "Crew catering", "90000"],
        ["", "TOTAL", "270000"],
    ])
    out = exporters.from_xlsx(raw)
    assert [s["name"] for s in out["sections"]] == ["CAMERA", "CATERING"]
    assert [len(s["items"]) for s in out["sections"]] == [1, 1]
    assert out["imported"]["total"] == 270000, "the TOTAL row must not be imported as a line"


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
    print(f"\n exporters: {len(tests) - failed} passed · {failed} failed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run() else 0)
