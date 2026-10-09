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


def test_the_pdf_is_a_real_pdf_with_a_working_cross_reference_table():
    blob = documents.callsheet_pdf(SHEET)
    assert blob.startswith(b"%PDF-1.4") and blob.rstrip().endswith(b"%%EOF")
    text = blob.decode("latin-1")
    start = text.rindex("startxref")
    offset = int(text[start:].split()[1])
    assert text[offset:offset + 4] == "xref", "startxref must point at the table"
    count = int(text[offset:].split()[2])
    assert text.count(" 0 obj") == count - 1, "every object must be in the table"


def test_the_pdf_carries_the_content_and_paginates():
    text = documents.callsheet_pdf(SHEET).decode("latin-1")
    for expected in ("Nike", "Whittington Hospital", "Ravi Kulkarni", "06:30"):
        assert expected in text, expected
    big = documents.write_pdf([{"style": "text", "text": f"Line {i} " + "x" * 60}
                               for i in range(200)], title="Long")
    assert b"/Count 4" in big or b"/Count 5" in big, "200 lines must run to several pages"


def test_a_bracket_in_a_producers_text_cannot_break_the_pdf():
    blob = documents.write_pdf(["Camera (Alexa) \\ rig", "Cost (net)"], title="T")
    text = blob.decode("latin-1")
    assert r"\(Alexa\)" in text, "an unescaped bracket ends the string operator"
    assert text.count("startxref") == 1


def test_a_call_sheet_becomes_a_table_a_coordinator_can_sort():
    rows = documents.callsheet_rows(SHEET)
    flat = [str(r) for r in rows]
    assert ["Name", "Role", "Call", "Phone", "Email"] in rows, "the crew table needs a header"
    assert any("Ravi Kulkarni" in f for f in flat)
    assert any("CAST" in f for f in flat) and any("Aisha Khan" in f for f in flat)
    assert any("Whittington" in f for f in flat)


def test_a_sheet_that_is_not_ready_says_so_in_the_spreadsheet_too():
    rows = documents.callsheet_rows({
        **SHEET, "needs": [{"field": "crew", "ask": "the crew", "blocking": True}]})
    assert any("NOT READY TO SEND" in str(r) for r in rows)


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
