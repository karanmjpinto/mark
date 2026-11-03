"""
documents.py — the document door: one kind for every file, and a Word writer.

Krish, on a voice note: "It needs to be able to read Excel files and Word
documents and be able to edit the Excel files and Word documents. Right now it
can only do PDFs."

He was right about the door, not quite right about the readers: main.py already
extracted text from .docx and .xlsx for the call-sheet template path, and
variance.py already read .xlsx into rows for a cost report. What was missing was
(a) one entry point that routes a file by what it is rather than by which
feature asked for it, and (b) anything at all that could write a .docx — so a
call sheet could be read but never handed back as Word.

`write_docx()` is the counterpart to `exporters.write_xlsx()`: a real Word file
built from nothing but `zipfile` and the XML Word expects, no dependency to
install and nothing to break on a Railway rebuild.

What this does NOT do, and should not be sold as doing: edit an arbitrary
formatted document in place. It reads the content out, and writes a Mark
document back. A producer's own template keeps its layout through
`/callsheet/render-template`, which was built for exactly that.

Pure logic, no FastAPI: covered offline in `evals/test_documents.py`.
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Any

import pdfdoc

KINDS = {
    "pdf": (".pdf",),
    "docx": (".docx",),
    "xlsx": (".xlsx",),
    "csv": (".csv", ".tsv"),
    "text": (".txt", ".md", ".rst", ".log"),
}

# Spelled out so the refusal can name the fix rather than shrugging.
LEGACY = {
    ".doc": "Legacy .doc is not readable — save it as .docx or PDF.",
    ".xls": "Legacy .xls is not readable — save it as .xlsx.",
    ".pages": "Pages files are not readable — export as .docx or PDF.",
    ".numbers": "Numbers files are not readable — export as .xlsx.",
    ".fdx": "Final Draft .fdx is not readable — export the script as PDF.",
}


def kind_of(filename: str) -> str:
    """What this file is, by extension. "unsupported" is an answer, not a failure."""
    name = (filename or "").strip().lower()
    for kind, suffixes in KINDS.items():
        if name.endswith(suffixes):
            return kind
    return "unsupported"


def refusal_for(filename: str) -> str:
    """The sentence to send a producer whose file cannot be read."""
    name = (filename or "").strip().lower()
    for suffix, message in LEGACY.items():
        if name.endswith(suffix):
            return message
    return ("I can read PDF, Word (.docx), Excel (.xlsx), CSV and plain text. "
            "Export it as one of those and send it again.")


# ── Word, written from nothing ────────────────────────────────────────────────
# A .docx is a zip of four files. Word is strict about the content types and the
# relationship ids and lenient about almost everything else, so the smallest
# honest document is a body of paragraphs with two styles: a heading and a line.

_DOCX_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>"""

_DOCX_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

_DOCX_DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

_DOCX_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/>
<w:pPr><w:spacing w:after="240"/></w:pPr>
<w:rPr><w:b/><w:sz w:val="40"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>
<w:pPr><w:spacing w:before="240" w:after="120"/></w:pPr>
<w:rPr><w:b/><w:sz w:val="26"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/>
<w:rPr><w:sz w:val="22"/></w:rPr></w:style>
</w:styles>"""


def _xml_escape(text: Any) -> str:
    return (str(text if text is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _paragraph(text: Any, style: str = "Normal") -> str:
    # xml:space="preserve" or Word eats a line that is only spaces.
    return (f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr>'
            f'<w:r><w:t xml:space="preserve">{_xml_escape(text)}</w:t></w:r></w:p>')


def _cell(text: Any, *, bold: bool = False, fill: str = "", white: bool = False) -> str:
    runs = ("<w:b/>" if bold else "") + ('<w:color w:val="FFFFFF"/>' if white else "")
    shade = f'<w:shd w:val="clear" w:color="auto" w:fill="{fill}"/>' if fill else ""
    return (f"<w:tc><w:tcPr><w:tcW w:w=\"0\" w:type=\"auto\"/>{shade}</w:tcPr>"
            f'<w:p><w:pPr><w:pStyle w:val="Normal"/></w:pPr>'
            f"<w:r><w:rPr>{runs}</w:rPr>"
            f'<w:t xml:space="preserve">{_xml_escape(text)}</w:t></w:r></w:p></w:tc>')


def _docx_table(columns: list, rows: list[list]) -> str:
    """A real Word table. A crew list pasted as lines of text is a crew list
    nobody can sort, widen or paste into their own sheet."""
    columns = [c for c in (columns or [])]
    if not columns:
        return ""
    grid = "".join('<w:gridCol w:w="%d"/>' % (9638 // len(columns)) for _ in columns)
    border = ('<w:tblBorders>' + "".join(
        f'<w:{side} w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"/>'
        for side in ("top", "left", "bottom", "right", "insideH", "insideV")) + "</w:tblBorders>")
    head = "<w:tr>" + "".join(_cell(c, bold=True, fill="2F3236", white=True)
                              for c in columns) + "</w:tr>"
    body = []
    for number, row in enumerate(rows or []):
        cells = list(row)[:len(columns)] + [""] * max(0, len(columns) - len(row))
        body.append("<w:tr>" + "".join(
            _cell(v, bold=(index == 0), fill=("F5F5F7" if number % 2 else ""))
            for index, v in enumerate(cells)) + "</w:tr>")
    return (f'<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/>{border}</w:tblPr>'
            f"<w:tblGrid>{grid}</w:tblGrid>{head}{''.join(body)}</w:tbl>"
            + _paragraph(""))


def write_docx(blocks: list[dict | str], *, title: str = "") -> bytes:
    """Blocks → .docx bytes.

    A block is `{"style": "title"|"heading"|"text", "text": "..."}`, or a bare
    string for a plain line. The call-sheet styles (`header`, `strip`, `band`,
    `kv`, `table`, `banner`, `bullets`) render to the nearest Word equivalent,
    so one block list makes both documents. An unknown style degrades to text
    rather than raising: a document that is slightly plainer than intended
    still opens.
    """
    styles = {"title": "Title", "heading": "Heading1", "text": "Normal"}
    body = []
    if title:
        body.append(_paragraph(title, "Title"))
    for block in (blocks or []):
        if not isinstance(block, dict):
            body.append(_paragraph(block, "Normal"))
            continue
        style = str(block.get("style") or "text")
        if style == "header":
            body.append(_paragraph(block.get("text", ""), "Title"))
            right = " ".join(f for f in [block.get("right_label") or "",
                                         block.get("right_value") or ""] if f)
            for line in list(block.get("lines") or []) + ([right] if right else []):
                if line:
                    body.append(_paragraph(line, "Normal"))
        elif style == "strip":
            cells = [f"{label}: {value}" for label, value in (block.get("cells") or []) if value]
            if cells:
                body.append(_paragraph("   ·   ".join(cells), "Normal"))
        elif style == "band":
            body.append(_paragraph(block.get("text", ""), "Heading1"))
        elif style == "kv":
            for label, value in (block.get("pairs") or []):
                if value not in (None, "", []):
                    body.append(_paragraph(f"{label}: {value}", "Normal"))
        elif style == "table":
            body.append(_docx_table(block.get("columns") or [], block.get("rows") or []))
        elif style == "banner":
            body.append(_paragraph(str(block.get("text", "")).upper(), "Heading1"))
            if block.get("detail"):
                body.append(_paragraph(block["detail"], "Normal"))
        elif style == "bullets":
            for item in (block.get("items") or []):
                body.append(_paragraph(f"- {item}", "Normal"))
        else:
            body.append(_paragraph(block.get("text", ""),
                                   styles.get(style, "Normal")))
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                f'<w:body>{"".join(body)}'
                '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
                '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"/>'
                '</w:sectPr></w:body></w:document>')

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _DOCX_CONTENT_TYPES)
        z.writestr("_rels/.rels", _DOCX_ROOT_RELS)
        z.writestr("word/_rels/document.xml.rels", _DOCX_DOC_RELS)
        z.writestr("word/styles.xml", _DOCX_STYLES)
        z.writestr("word/document.xml", document)
    return buf.getvalue()


# ── a call sheet as a document ────────────────────────────────────────────────

def _crew_rows(people: list) -> list[list]:
    return [[p.get("name") or "", p.get("role") or "", p.get("call_time") or "",
             p.get("phone") or "", p.get("email") or ""] for p in people]


def _cast_rows(people: list) -> list[list]:
    return [[p.get("artist") or "", p.get("character") or "", p.get("call_time") or "",
             p.get("on_set") or "", p.get("phone") or ""] for p in people]


def callsheet_blocks(sheet: dict) -> list[dict]:
    """A call sheet → document blocks, in the order a crew member reads them.

    The shape is deliberate, not decorative. The title and the date go in the
    header band; the call and the wrap go in the strip, because that is what
    somebody checks at six in the morning; crew and cast are tables, because a
    name next to a role next to a number is a grid and reads as nothing else.
    Every empty field is omitted rather than printed blank, and `needs` becomes
    a banner at the top: a sheet that is not ready to send must not look
    finished on paper, and must not be scrolled past.
    """
    sheet = sheet or {}
    out: list[dict] = []

    out.append({"style": "header",
                "text": sheet.get("project_title") or "Call sheet",
                "lines": [" · ".join(f for f in [sheet.get("client") or "",
                                                 sheet.get("unit") or ""] if f)],
                "right_label": "CALL SHEET" + (f" · {sheet['shoot_day']}"
                                               if sheet.get("shoot_day") else ""),
                "right_value": sheet.get("date") or ""})

    blocking = [n["ask"] for n in (sheet.get("needs") or []) if n.get("blocking")]
    if blocking:
        out.append({"style": "banner", "text": "NOT READY TO SEND",
                    "detail": "Still missing: " + "; ".join(blocking)})

    out.append({"style": "strip", "cells": [
        ("General call", sheet.get("general_call_time") or ""),
        ("Estimated wrap", sheet.get("wrap_time") or ""),
        ("Shoot day", sheet.get("shoot_day") or ""),
        ("Date", sheet.get("date") or ""),
    ]})

    pairs: list[tuple[str, Any]] = []
    for loc in (sheet.get("locations") or []):
        # "Location" is the label; the name belongs in the value, where it has
        # the width to be read in full.
        pairs.append(("Location", " — ".join(f for f in [loc.get("name") or "",
                                                         loc.get("address") or "",
                                                         loc.get("notes") or ""] if f)))
    for label, key in (("Parking", "parking"), ("Nearest hospital", "nearest_hospital"),
                       ("Weather", "weather")):
        if sheet.get(key):
            pairs.append((label, sheet[key]))
    if pairs:
        out.append({"style": "band", "text": "Location & logistics"})
        out.append({"style": "kv", "pairs": pairs})

    if sheet.get("scenes"):
        rows = [[str(s.get("scene") or ""), s.get("int_ext") or "", s.get("location") or "",
                 s.get("day_night") or "", s.get("description") or ""]
                for s in sheet["scenes"]]
        out.append({"style": "band", "text": "Schedule"})
        out.append({"style": "table", "columns": ["Sc.", "I/E", "Set", "D/N", "Description"],
                    "rows": rows, "widths": [0.08, 0.08, 0.24, 0.08, 0.52]})

    if sheet.get("cast"):
        out.append({"style": "band", "text": "Cast"})
        out.append({"style": "table",
                    "columns": ["Artist", "Character", "Call", "On set", "Phone"],
                    "rows": _cast_rows(sheet["cast"]),
                    "widths": [0.26, 0.24, 0.12, 0.12, 0.26]})

    if sheet.get("crew"):
        out.append({"style": "band", "text": "Crew"})
        out.append({"style": "table",
                    "columns": ["Name", "Role", "Call", "Phone", "Email"],
                    "rows": _crew_rows(sheet["crew"]),
                    "widths": [0.23, 0.21, 0.10, 0.20, 0.26]})

    if sheet.get("notes"):
        out.append({"style": "band", "text": "Notes"})
        out.append({"style": "bullets", "items": [str(n) for n in sheet["notes"]]})
    return out


def callsheet_docx(sheet: dict) -> bytes:
    # No `title=`: the header block already carries the production name.
    return write_docx(callsheet_blocks(sheet))


def safe_filename(name: str, *, suffix: str) -> str:
    """A filename a phone and a filesystem will both accept."""
    stem = re.sub(r"[^A-Za-z0-9 _.-]", "", str(name or "document")).strip() or "document"
    stem = re.sub(r"\s+", " ", stem)[:60].strip()
    if stem.lower().endswith(suffix.lower()):
        return stem
    return f"{stem}{suffix}"


# ── PDF ───────────────────────────────────────────────────────────────────────
# Crew get a PDF. Not a Word file they open on a phone and reflow, and not a
# spreadsheet — a PDF, because it looks the same on every device and prints.
# The drawing lives in pdfdoc.py; this is only the door to it.

def write_pdf(blocks: list[dict | str], *, title: str = "", footer: str = "") -> bytes:
    """Blocks → a real PDF. Same block shape as write_docx."""
    return pdfdoc.render(blocks, title=title, footer=footer)


def callsheet_pdf(sheet: dict) -> bytes:
    sheet = sheet or {}
    footer = " · ".join(f for f in [sheet.get("project_title") or "Call sheet",
                                    sheet.get("date") or "",
                                    sheet.get("shoot_day") or ""] if f)
    return pdfdoc.render(callsheet_blocks(sheet), footer=footer)


# ── a call sheet as a spreadsheet ─────────────────────────────────────────────

def callsheet_rows(sheet: dict) -> list[list]:
    """A call sheet as rows, for .xlsx. The crew and cast tables are real
    tables, so a coordinator can sort and filter them."""
    sheet = sheet or {}
    rows: list[list] = [[sheet.get("project_title") or "Call sheet"]]
    for label, key in (("Client", "client"), ("Shoot day", "shoot_day"), ("Date", "date"),
                       ("Unit", "unit"), ("General call", "general_call_time"),
                       ("Estimated wrap", "wrap_time"), ("Nearest hospital", "nearest_hospital"),
                       ("Weather", "weather"), ("Parking", "parking")):
        if sheet.get(key):
            rows.append([label, sheet[key]])
    for loc in (sheet.get("locations") or []):
        rows.append(["Location", ", ".join(f for f in [loc.get("name"), loc.get("address")] if f)])
    if sheet.get("crew"):
        rows += [[], ["CREW"], ["Name", "Role", "Call", "Phone", "Email"]]
        rows += [[c.get("name"), c.get("role"), c.get("call_time"), c.get("phone"), c.get("email")]
                 for c in sheet["crew"]]
    if sheet.get("cast"):
        rows += [[], ["CAST"], ["Artist", "Character", "Call", "On set", "Phone"]]
        rows += [[c.get("artist"), c.get("character"), c.get("call_time"), c.get("on_set"),
                  c.get("phone")] for c in sheet["cast"]]
    if sheet.get("notes"):
        rows += [[], ["NOTES"]] + [[str(n)] for n in sheet["notes"]]
    blocking = [n["ask"] for n in (sheet.get("needs") or []) if n.get("blocking")]
    if blocking:
        rows += [[], ["NOT READY TO SEND"], ["Still missing", "; ".join(blocking)]]
    return rows
