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


def write_docx(blocks: list[dict | str], *, title: str = "") -> bytes:
    """Blocks → .docx bytes.

    A block is `{"style": "title"|"heading"|"text", "text": "..."}`, or a bare
    string for a plain line. An unknown style degrades to text rather than
    raising: a document that is slightly plainer than intended still opens.
    """
    styles = {"title": "Title", "heading": "Heading1", "text": "Normal"}
    body = []
    if title:
        body.append(_paragraph(title, "Title"))
    for block in (blocks or []):
        if isinstance(block, dict):
            body.append(_paragraph(block.get("text", ""),
                                   styles.get(str(block.get("style") or "text"), "Normal")))
        else:
            body.append(_paragraph(block, "Normal"))
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

def callsheet_blocks(sheet: dict) -> list[dict]:
    """A call sheet → document blocks. Every empty field is omitted rather than
    printed blank, and `needs` is printed as an explicit warning: a sheet that
    is not ready to send must not look finished on paper."""
    sheet = sheet or {}
    out: list[dict] = []
    head = " · ".join(f for f in [sheet.get("shoot_day") or "", sheet.get("date") or "",
                                  sheet.get("unit") or ""] if f)
    if head:
        out.append({"style": "text", "text": head})
    if sheet.get("client"):
        out.append({"style": "text", "text": f"Client: {sheet['client']}"})

    times = " · ".join(f for f in [
        f"General call {sheet['general_call_time']}" if sheet.get("general_call_time") else "",
        f"Estimated wrap {sheet['wrap_time']}" if sheet.get("wrap_time") else ""] if f)
    if times:
        out.append({"style": "heading", "text": "Times"})
        out.append({"style": "text", "text": times})

    if sheet.get("locations"):
        out.append({"style": "heading", "text": "Location"})
        for loc in sheet["locations"]:
            line = ", ".join(f for f in [loc.get("name") or "", loc.get("address") or ""] if f)
            out.append({"style": "text", "text": line})
            if loc.get("notes"):
                out.append({"style": "text", "text": loc["notes"]})
    if sheet.get("parking"):
        out.append({"style": "text", "text": f"Parking: {sheet['parking']}"})
    if sheet.get("nearest_hospital"):
        out.append({"style": "heading", "text": "Nearest hospital"})
        out.append({"style": "text", "text": sheet["nearest_hospital"]})
    if sheet.get("weather"):
        out.append({"style": "heading", "text": "Weather"})
        out.append({"style": "text", "text": sheet["weather"]})

    if sheet.get("cast"):
        out.append({"style": "heading", "text": "Cast"})
        for c in sheet["cast"]:
            line = " — ".join(f for f in [c.get("artist") or "", c.get("character") or ""] if f)
            extra = " · ".join(f for f in [
                f"call {c['call_time']}" if c.get("call_time") else "",
                f"on set {c['on_set']}" if c.get("on_set") else "",
                c.get("phone") or ""] if f)
            out.append({"style": "text", "text": line + (f"  ({extra})" if extra else "")})

    if sheet.get("crew"):
        out.append({"style": "heading", "text": "Crew"})
        for c in sheet["crew"]:
            line = " — ".join(f for f in [c.get("name") or "", c.get("role") or ""] if f)
            extra = " · ".join(f for f in [
                f"call {c['call_time']}" if c.get("call_time") else "",
                c.get("phone") or "", c.get("email") or ""] if f)
            out.append({"style": "text", "text": line + (f"  ({extra})" if extra else "")})

    if sheet.get("scenes"):
        out.append({"style": "heading", "text": "Scenes"})
        for s in sheet["scenes"]:
            bits = [str(s.get("scene") or ""), s.get("int_ext") or "", s.get("location") or "",
                    s.get("day_night") or "", s.get("description") or ""]
            out.append({"style": "text", "text": " · ".join(b for b in bits if b)})

    if sheet.get("notes"):
        out.append({"style": "heading", "text": "Notes"})
        for note in sheet["notes"]:
            out.append({"style": "text", "text": str(note)})

    blocking = [n["ask"] for n in (sheet.get("needs") or []) if n.get("blocking")]
    if blocking:
        out.append({"style": "heading", "text": "NOT READY TO SEND"})
        out.append({"style": "text", "text": "Still missing: " + "; ".join(blocking)})
    return out


def callsheet_docx(sheet: dict) -> bytes:
    title = sheet.get("project_title") or "Call sheet"
    return write_docx(callsheet_blocks(sheet), title=title)


def safe_filename(name: str, *, suffix: str) -> str:
    """A filename a phone and a filesystem will both accept."""
    stem = re.sub(r"[^A-Za-z0-9 _.-]", "", str(name or "document")).strip() or "document"
    stem = re.sub(r"\s+", " ", stem)[:60].strip()
    if stem.lower().endswith(suffix.lower()):
        return stem
    return f"{stem}{suffix}"
