"""
exporters.py — getting a budget out of Mark and into the tools it has to live in.

A budget that can only be read inside Mark stops at the producer's desk. The
production accountant works in Excel or in Movie Magic, and the review noted this
as the third-largest gap: no interop, so no path from Mark's output to the people
who actually spend the money.

Three directions:

  * **to_xlsx()** — a real .xlsx, written with nothing but zipfile and the XML
    the format requires. The frontend already exports client-side via SheetJS;
    this exists because an agent driving Mark over MCP, a scheduled job, or the
    teardown report has no browser to do it in.
  * **to_mm_interchange()** — a delimited account/detail file of the kind Movie
    Magic Budgeting imports. **This is not a `.mmb`.** The binary format is
    proprietary and undocumented; writing one blind would produce a file that
    either fails to open or, worse, opens with wrong numbers. A delimited
    interchange file is what an accountant actually uses to bring outside figures
    into MMB, and it is honest about what it is.
  * **from_xlsx()** — read the client's own budget spreadsheet back into Mark's
    shape, so a first engagement starts from their existing numbers rather than
    from a blank page. Best-effort and explicit about what it could not map.

Pure functions over plain dicts and bytes. No dependencies.
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Any, Iterable, Optional

from variance import _clean_number, xlsx_rows, _looks_like_total

# ── xlsx writing ──────────────────────────────────────────────────────────────

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

_WORKBOOK_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

# Style indices used below: 0 body, 1 bold, 2 money, 3 bold money, 4 header.
_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="1"><numFmt numFmtId="164" formatCode="#,##0"/></numFmts>
<fonts count="2">
<font><sz val="11"/><name val="Calibri"/></font>
<font><b/><sz val="11"/><name val="Calibri"/></font>
</fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFEFEFEA"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="5">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>
<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
<xf numFmtId="164" fontId="1" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyFont="1"/>
<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>
</cellXfs>
</styleSheet>"""


def _col_letter(idx: int) -> str:
    letters = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _esc(text: Any) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _cell(ref: str, value: Any, style: int = 0) -> str:
    if value is None or value == "":
        return f'<c r="{ref}" s="{style}"/>'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # Write 180000 rather than 180000.0 — both are valid, but the trailing
        # .0 shows up in anything that reads the raw cell as text.
        num = int(value) if float(value).is_integer() else value
        return f'<c r="{ref}" s="{style}"><v>{num}</v></c>'
    return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{_esc(value)}</t></is></c>'


def write_xlsx(rows: list[list], *, sheet_name: str = "Budget",
               col_widths: Optional[list[int]] = None) -> bytes:
    """Rows of cells → .xlsx bytes.

    A cell is a bare value, or `(value, style)` where style indexes `_STYLES`:
    0 body · 1 bold · 2 money · 3 bold money · 4 section header.
    """
    xml_rows = []
    for r, row in enumerate(rows, start=1):
        cells = []
        for c, cell in enumerate(row):
            value, style = cell if isinstance(cell, tuple) else (cell, 0)
            cells.append(_cell(f"{_col_letter(c)}{r}", value, style))
        xml_rows.append(f'<row r="{r}">{"".join(cells)}</row>')

    cols = ""
    if col_widths:
        specs = "".join(f'<col min="{i+1}" max="{i+1}" width="{w}" customWidth="1"/>'
                        for i, w in enumerate(col_widths))
        cols = f"<cols>{specs}</cols>"

    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"{cols}<sheetData>{''.join(xml_rows)}</sheetData></worksheet>"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{_esc(sheet_name)[:31]}" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _ROOT_RELS)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", _WORKBOOK_RELS)
        z.writestr("xl/styles.xml", _STYLES)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


def budget_rows(budget: dict, *, currency: str = "INR") -> list[list]:
    """The budget laid out the way a producer reads it: sections in code order,
    a subtotal per section, tax carried per line, and the reconciliation at the
    bottom rather than hidden in a corner."""
    rows: list[list] = [
        [(f"{budget.get('title') or 'Budget'}", 1)],
        [f"{budget.get('production_type') or ''} · {budget.get('shoot_days') or '?'} shoot days "
         f"· {budget.get('scale_tier') or ''} tier"],
        [],
        [("Code", 4), ("Description", 4), ("Basis", 4), (f"Amount ({currency})", 4),
         ("Tax", 4), (f"Tax amt ({currency})", 4), ("Conf", 4), ("Note", 4)],
    ]
    grand = gst_total = 0.0
    for section in budget.get("sections") or []:
        rows.append([(str(section.get("code") or ""), 4), (section.get("name") or "", 4),
                     ("", 4), ("", 4), ("", 4), ("", 4), ("", 4), ("", 4)])
        subtotal = 0.0
        for item in section.get("items") or []:
            amount = float(item.get("amount") or 0)
            gst_rate = float(item.get("gst_rate") or 0)
            gst_amount = round(amount * gst_rate, 2)
            subtotal += amount
            gst_total += gst_amount
            rows.append([
                str(item.get("code") or ""), item.get("desc") or "", item.get("sub") or "",
                (amount, 2), f"{gst_rate:.0%}" if gst_rate else "—", (gst_amount, 2),
                item.get("conf") or "", item.get("note") or "",
            ])
        rows.append(["", (f"{section.get('name') or ''} subtotal", 1), "", (round(subtotal, 2), 3),
                     "", "", "", ""])
        grand += subtotal

    rows += [
        [],
        ["", ("Subtotal", 1), "", (round(grand, 2), 3)],
        ["", ("Tax", 1), "", (round(gst_total, 2), 3)],
        ["", ("Total", 1), "", (round(grand + gst_total, 2), 3)],
    ]
    if budget.get("excluded"):
        rows += [[], [("Excluded", 1)]] + [["", e] for e in budget["excluded"]]
    if budget.get("flags"):
        rows += [[], [("Flags", 1)]] + [["", f] for f in budget["flags"]]
    return rows


def to_xlsx(budget: dict, *, currency: str = "INR") -> bytes:
    return write_xlsx(budget_rows(budget, currency=currency),
                      sheet_name=(budget.get("title") or "Budget")[:31],
                      col_widths=[10, 42, 30, 14, 8, 14, 8, 40])


def ledger_to_xlsx(ledger: dict) -> bytes:
    """The Variance Ledger as a spreadsheet — deliverable D2 of the Stage 0 SOW,
    which the SOW specifies as a spreadsheet rather than a PDF because the client
    will want to sort and filter it themselves."""
    cur = ledger.get("currency", "INR")
    rows: list[list] = [
        [(f"Variance ledger — {ledger.get('production') or 'production'}", 1)],
        [f"Materiality threshold: {ledger.get('threshold', 0.1):.0%}"],
        [],
        [("Section", 4), ("Code", 4), ("Description", 4), (f"Budget ({cur})", 4),
         (f"Actual ({cur})", 4), (f"Delta ({cur})", 4), ("Delta %", 4),
         ("Status", 4), ("Classification", 4), ("Evidence", 4), ("Vendors", 4)],
    ]
    for l in ledger.get("lines", []):
        rows.append([
            l.get("section") or "", l.get("code") or "", l.get("desc") or "",
            (l.get("budget") or 0, 2), (l.get("actual") or 0, 2), (l.get("delta") or 0, 2),
            f"{l['delta_pct']:.1%}" if l.get("delta_pct") is not None else "—",
            l.get("status") or "", l.get("classification") or "", l.get("basis") or "",
            ", ".join(l.get("vendors") or []),
        ])
    t = ledger.get("totals") or {}
    rows += [
        [],
        ["", "", ("Totals", 1), (t.get("budget", 0), 3), (t.get("actual", 0), 3),
         (t.get("delta", 0), 3)],
        ["", "", "Unbudgeted spend", "", (t.get("unbudgeted_spend", 0), 2)],
        ["", "", "Unspent budget", (t.get("unspent_budget", 0), 2)],
    ]
    return write_xlsx(rows, sheet_name="Variance ledger",
                      col_widths=[10, 10, 40, 14, 14, 14, 10, 12, 18, 52, 24])


# ── Movie Magic interchange ───────────────────────────────────────────────────

MM_INTERCHANGE_HEADER = [
    "Account", "Description", "Units", "X", "Rate", "Total", "Currency", "Notes",
]


def to_mm_interchange(budget: dict, *, currency: str = "INR", delimiter: str = ",") -> str:
    """A delimited account/detail file for Movie Magic Budgeting's import.

    Columns follow MMB's detail-line grammar — account, description, units,
    multiplier, rate, total — so an accountant can map them in one pass. Where
    Mark's basis string carries a quantity ("3 days × 1 unit") it is split out;
    where it does not, the line imports as a single unit at its full amount,
    which is what MMB does with a flat line anyway.

    Not a `.mmb`. See the module docstring — the binary format is undocumented
    and a wrong one is worse than none.
    """
    import csv as _csv

    out = io.StringIO()
    w = _csv.writer(out, delimiter=delimiter, lineterminator="\n")
    w.writerow(MM_INTERCHANGE_HEADER)
    for section in budget.get("sections") or []:
        code = str(section.get("code") or "")
        w.writerow([code, (section.get("name") or "").upper(), "", "", "", "", "", "section"])
        for item in section.get("items") or []:
            amount = float(item.get("amount") or 0)
            units, rate = _units_and_rate(item.get("sub") or "", amount)
            w.writerow([
                str(item.get("code") or code), item.get("desc") or "",
                units if units else 1, 1, f"{rate:.2f}", f"{amount:.2f}", currency,
                " · ".join(x for x in (item.get("sub"), item.get("note")) if x),
            ])
    return out.getvalue()


_QTY = re.compile(r"(\d+(?:\.\d+)?)\s*(?:x|×|days?|units?|persons?|heads?|episodes?)", re.I)


def _units_and_rate(sub: str, amount: float) -> tuple[Optional[float], float]:
    m = _QTY.search(sub or "")
    if not m:
        return None, amount
    units = float(m.group(1))
    return units, (amount / units if units else amount)


# ── reading a client's own budget back in ─────────────────────────────────────

_SECTION_ROW = re.compile(r"^\s*(\d{4,5})\s*$")

# A budget sheet is not just lines: it carries subtotals, a totals block, and a
# trailer of excluded items and assumptions. Importing any of those as a line
# item double-counts the money — which is exactly the class of error a teardown
# is supposed to find, not create.
_SUMMARY_WORDS = ("subtotal", "sub total", "grand total", "total")
_SUMMARY_ROWS = {"tax", "gst", "vat", "net", "sum", "contingency %"}
_TRAILER_ROWS = {"excluded", "exclusions", "flags", "notes", "assumptions", "caveats"}


def _is_summary_row(desc: str, code: str) -> bool:
    d = (desc or "").strip().lower()
    if not d:
        return False
    if any(w in d for w in _SUMMARY_WORDS):
        return True
    return not code and d in _SUMMARY_ROWS


def from_xlsx(raw: bytes, *, currency: str = "INR", title: str = "Imported budget") -> dict:
    """Best-effort read of a client's budget spreadsheet into Mark's shape.

    Deliberately conservative: it finds the header row, maps the columns it
    recognises, treats a bare 4–5 digit code with no amount as a section break,
    and reports everything it skipped in `flags` rather than silently guessing.
    An import that quietly drops a line is worse than one that refuses it.
    """
    rows = xlsx_rows(raw)

    def _norm_header(cell: str) -> str:
        """`Amount (INR)` and `Amount:` are the same column. Currency suffixes and
        punctuation are the norm in real budget sheets, so strip them before
        matching rather than demanding an exact word."""
        return re.sub(r"\(.*?\)", "", str(cell or "")).strip().strip(":*").lower()

    header_at = None
    for i, row in enumerate(rows[:40]):
        lowered = {_norm_header(c) for c in row}
        if lowered & {"amount", "total", "cost", "value"} and \
           lowered & {"description", "desc", "particulars", "item", "head"}:
            header_at = i
            break
    if header_at is None:
        return {"title": title, "sections": [], "flags": [
            "No header row found. The sheet needs a row with a description column "
            "and an amount column before anything can be imported."]}

    header = [_norm_header(c) for c in rows[header_at]]

    def col(*names) -> Optional[int]:
        for n in names:
            if n in header:
                return header.index(n)
        return None

    c_code = col("code", "account", "account code", "acc", "gl")
    c_desc = col("description", "desc", "particulars", "item", "head")
    c_amount = col("amount", "total", "cost", "value")
    c_sub = col("basis", "sub", "notes", "narration", "remarks")

    sections: list[dict] = []
    flags: list[str] = []
    current: Optional[dict] = None
    skipped = 0

    for row in rows[header_at + 1:]:
        def cell(idx):
            return row[idx].strip() if idx is not None and idx < len(row) else ""

        code, desc = cell(c_code), cell(c_desc)
        amount = _clean_number(cell(c_amount))

        if not code and not desc:
            continue
        # Everything below an "Excluded" / "Flags" heading is commentary, not cost.
        if not code and amount is None and desc.strip().lower() in _TRAILER_ROWS:
            break
        if _looks_like_total(desc) or _is_summary_row(desc, code):
            continue
        if amount is None:
            # A code with a name and no money reads as a section header.
            if desc and (not code or _SECTION_ROW.match(code)):
                current = {"code": code or f"{9000 + len(sections) * 100}",
                           "name": desc.upper(), "type": "below_the_line", "items": []}
                sections.append(current)
            else:
                skipped += 1
            continue

        if current is None:
            current = {"code": "10000", "name": "IMPORTED", "type": "below_the_line", "items": []}
            sections.append(current)
        current["items"].append({
            "code": code or f"{current['code']}{len(current['items']) + 1:02d}",
            "desc": desc or "(no description)",
            "sub": cell(c_sub),
            "amount": float(amount),
            "gst_rate": 0,
            "conf": "amber",
            "note": "imported — tax treatment and basis not carried over",
        })

    # A section that collected nothing is a heading we misread, not a section.
    empty = [s_ for s_ in sections if not s_["items"]]
    sections = [s_ for s_ in sections if s_["items"]]
    if empty:
        flags.append(f"{len(empty)} heading(s) with no line items under them were dropped: "
                     + ", ".join(s_["name"] for s_ in empty[:5]))
    if skipped:
        flags.append(f"{skipped} row(s) had no readable amount and were skipped.")
    flags.append("Imported from a spreadsheet: tax rates default to 0 and confidence to amber. "
                 "Set GST per line before using this for anything but comparison.")
    total = sum(i["amount"] for s in sections for i in s["items"])
    return {
        "title": title,
        "production_type": "Imported",
        "shoot_days": 0,
        "scale_tier": "mid",
        "locations": [],
        "comparable_note": "",
        "confidence_note": "Imported from the client's own spreadsheet; not generated by Mark.",
        "sections": sections,
        "excluded": [],
        "flags": flags,
        "imported": {"lines": sum(len(s["items"]) for s in sections),
                     "sections": len(sections), "total": round(total, 2), "currency": currency},
    }
