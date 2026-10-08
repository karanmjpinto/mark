"""
test_documents.py — the document door and the Word writer, asserted.

Run: python3 evals/test_documents.py

The rules being protected: a file is routed by what it is, a format we cannot
read is refused with the fix rather than a shrug, the .docx we write is a real
zip with the parts Word requires, a producer's own words cannot break the XML,
and a call sheet that is not ready to send never looks finished on paper.
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import documents  # noqa: E402


SHEET = {
    "project_title": "Nike — Dawn Training",
    "client": "Nike",
    "shoot_day": "Day 1 of 1",
    "date": "2026-11-12",
    "general_call_time": "06:30",
    "wrap_time": "19:00",
    "locations": [{"name": "Alexandra Palace", "address": "Alexandra Palace Way, London N22"}],
    "nearest_hospital": "Whittington Hospital",
    "crew": [{"name": "Ravi Kulkarni", "role": "1st AD", "phone": "+447700900001",
              "call_time": "06:00"}],
    "cast": [{"artist": "Aisha Khan", "character": "Runner", "call_time": "07:15"}],
    "notes": ["No catering on site"],
    "needs": [],
}


def _text_of(blob: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        return z.read("word/document.xml").decode()


def test_a_file_is_routed_by_what_it_is():
    for name, kind in (("script.PDF", "pdf"), ("budget.xlsx", "xlsx"), ("sheet.docx", "docx"),
                       ("costs.csv", "csv"), ("notes.md", "text"), ("clip.mov", "unsupported")):
        assert documents.kind_of(name) == kind, name


def test_a_format_we_cannot_read_is_refused_with_the_fix():
    assert ".docx" in documents.refusal_for("old.doc")
    assert ".xlsx" in documents.refusal_for("old.xls")
    assert "PDF" in documents.refusal_for("script.fdx")
    assert "Numbers" in documents.refusal_for("budget.numbers")
    assert "PDF" in documents.refusal_for("clip.mov"), "the catch-all still names what works"


def test_the_word_file_is_a_real_zip_with_the_parts_word_requires():
    blob = documents.callsheet_docx(SHEET)
    assert blob[:2] == b"PK"
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        names = set(z.namelist())
        assert {"[Content_Types].xml", "_rels/.rels", "word/_rels/document.xml.rels",
                "word/styles.xml", "word/document.xml"} <= names, names
        assert z.testzip() is None


def test_the_content_carries_through_to_the_document():
    xml = _text_of(documents.callsheet_docx(SHEET))
    for expected in ("Nike", "Alexandra Palace", "Whittington Hospital", "Ravi Kulkarni",
                     "06:30", "Aisha Khan", "No catering on site"):
        assert expected in xml, expected


def test_a_producers_own_words_cannot_break_the_xml():
    hostile = {**SHEET, "project_title": 'Ben & Jerry <"Scoop"> ', "notes": ["3 < 4 & 5 > 2"]}
    blob = documents.callsheet_docx(hostile)
    xml = _text_of(blob)
    assert "&amp;" in xml and "&lt;" in xml and "&gt;" in xml
    assert "<\"Scoop\">" not in xml
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        assert z.read("word/document.xml").decode().count("<w:document") == 1


def test_an_empty_field_is_omitted_rather_than_printed_blank():
    thin = {"project_title": "Vodafone", "general_call_time": "07:00", "needs": []}
    xml = _text_of(documents.callsheet_docx(thin))
    assert "Vodafone" in xml and "07:00" in xml
    for absent in ("Nearest hospital", "Crew", "Cast", "Notes", "Weather", "Location"):
        assert absent not in xml, absent


def test_a_sheet_that_is_not_ready_never_looks_finished_on_paper():
    xml = _text_of(documents.callsheet_docx({
        **SHEET, "nearest_hospital": "",
        "needs": [{"field": "nearest_hospital", "ask": "the nearest hospital", "blocking": True}]}))
    assert "NOT READY TO SEND" in xml
    assert "the nearest hospital" in xml


def test_a_bare_string_block_is_a_plain_line_and_an_unknown_style_degrades():
    blob = documents.write_docx(["just a line", {"style": "interpretive-dance", "text": "odd"}],
                                title="T")
    xml = _text_of(blob)
    assert "just a line" in xml and "odd" in xml
    assert 'w:val="Title"' in xml
    assert "interpretive-dance" not in xml


def test_a_filename_survives_a_phone_and_a_filesystem():
    assert documents.safe_filename("Nike — Dawn/Training", suffix=".docx") == "Nike DawnTraining.docx"
    assert documents.safe_filename("", suffix=".xlsx") == "document.xlsx"
    assert documents.safe_filename("already.docx", suffix=".docx") == "already.docx"
    assert len(documents.safe_filename("x" * 200, suffix=".docx")) <= 65


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
    print(f"\n documents: {len(tests) - failed} passed · {failed} failed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run() else 0)
