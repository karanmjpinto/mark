"""
test_pdf_grid.py — a name in a grid comes back as a name in a grid.

Run: python3 evals/test_pdf_grid.py

The rule being protected is the one Krish found: "if it can't read names in a
grid its pretty much useless because 99% of the documents are in a grid." The
test writes a call sheet with our own renderer and reads it back, so both ends
of the round trip are covered at once — if either the table rules or the reader
break, the crew disappears again.

pdfplumber is a real dependency (requirements.txt). When it is not installed
these checks report as skipped rather than failing, so the rest of the offline
suite still runs on a bare interpreter.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import documents  # noqa: E402
import pdf_grid  # noqa: E402

SHEET = {
    "project_title": "MAX Fashion — Spring",
    "client": "MAX Fashion",
    "date": "01/10/26",
    "general_call_time": "08:00",
    "wrap_time": "17:15",
    "locations": [{"name": "Finsbury Circus Gardens", "address": "London EC2M 7DT"}],
    "nearest_hospital": "The Royal London Hospital",
    "crew": [
        {"name": "Krish Pinto", "role": "1st AD", "call_time": "07:30",
         "phone": "+447706936477"},
        {"name": "Jo Mensah", "role": "DoP", "call_time": "07:30",
         "phone": "+447700900002"},
        {"name": "Sam Okafor", "role": "Gaffer", "call_time": "07:00",
         "phone": "+447700900003"},
    ],
    "needs": [],
}


def _have_pdfplumber() -> bool:
    try:
        import pdfplumber  # noqa: F401
        return True
    except ImportError:
        return False


def test_a_name_in_a_grid_comes_back_in_a_row():
    out = pdf_grid.read_pdf(documents.callsheet_pdf(SHEET))
    rows = [row for table in out["tables"] for row in table["rows"]]
    joined = ["|".join(row) for row in rows]
    for name, role in (("Krish Pinto", "1st AD"), ("Jo Mensah", "DoP"),
                       ("Sam Okafor", "Gaffer")):
        assert any(name in r and role in r for r in joined), f"{name} lost its role"
    assert any("+447706936477" in r for r in joined), "a number must stay with its person"


def test_the_grid_also_reaches_the_model_as_text():
    """The rows are returned, and repeated in the text as TSV — a model reads a
    tab-separated row more reliably than a run of aligned spaces."""
    text = pdf_grid.read_pdf(documents.callsheet_pdf(SHEET))["text"]
    assert "TABLE 1" in text
    assert "Krish Pinto\t1st AD" in text


def test_the_single_facts_still_come_through():
    text = pdf_grid.read_pdf(documents.callsheet_pdf(SHEET))["text"]
    for fact in ("08:00", "17:15", "Finsbury Circus Gardens", "The Royal London Hospital"):
        assert fact in text, fact


def test_a_page_of_prose_is_not_forced_into_a_table():
    """The whitespace fallback will call any page of text a table if it is
    allowed to. A paragraph must come back as text and nothing else."""
    blob = documents.write_pdf(
        [{"style": "text", "text": "The shoot moves to the second location after lunch and "
                                   "the crew travel together from unit base."}] * 6,
        title="Memo")
    out = pdf_grid.read_pdf(blob)
    assert "unit base" in out["text"]
    assert out["tables"] == [], "prose is not a grid"


def test_an_empty_grid_is_dropped_rather_than_returned():
    assert pdf_grid._clean([["", ""], [None, " "]]) == []
    assert pdf_grid._clean([["Name", "", "Role"], ["Jo", "", "DoP"]]) == \
        [["Name", "Role"], ["Jo", "DoP"]]



def _run():
    if not _have_pdfplumber():
        print("  ⏭  pdfplumber not installed — grid reading not exercised")
        return 0
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
    print(f"\n pdf_grid: {len(tests) - failed} passed · {failed} failed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run() else 0)
