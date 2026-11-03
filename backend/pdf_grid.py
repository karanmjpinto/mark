"""
pdf_grid.py — read a PDF that is not a screenplay.

The only PDF reader here used to be `screenplay_pdf_to_json`. A screenplay is
one column of text, so that parser returns words in reading order and throws
away where each one sat on the page. Feed it a call sheet and the single facts
survive — date, call time, address — while the crew table does not: the names
form a column because of their position, not because of word order, and the
position is exactly what was discarded. Krish: "if it can't read names in a
grid its pretty much useless because 99% of the documents are in a grid."

So this reader keeps the geometry. `pdfplumber` gives the x and y of every
word; `layout=True` text keeps the columns standing up, and `extract_tables`
turns ruled grids into rows. Both go back to the model, because a production
document is usually half prose and half table and we cannot tell in advance
which half holds the answer.

One dependency, deliberately: writing a PDF from nothing is a weekend, reading
an arbitrary one correctly is not.
"""

from __future__ import annotations

import io
from typing import Any

MAX_TABLE_ROWS = 400          # a crew list, not a database dump


def _clean(rows: list[list]) -> list[list[str]]:
    """Strip cells, drop rows and columns that are entirely empty."""
    grid = [[(str(c).strip() if c is not None else "") for c in row] for row in (rows or [])]
    grid = [r for r in grid if any(r)]
    if not grid:
        return []
    width = max(len(r) for r in grid)
    grid = [r + [""] * (width - len(r)) for r in grid]
    keep = [i for i in range(width) if any(r[i] for r in grid)]
    return [[r[i] for i in keep] for r in grid]


def _tables(page: Any) -> list[list[list[str]]]:
    """Grids with rules or filled cells, which is what a production document
    exported from Excel or InDesign has.

    There is deliberately no whitespace fallback. pdfplumber will happily call
    a page of prose a table if asked to infer columns from word gaps, and a
    wrong table is worse than none: it is returned with the same confidence as
    a real one. A grid with no rules still reaches the model through the
    layout-preserving text below.
    """
    ruled = [_clean(t) for t in (page.extract_tables() or [])]
    return [t[:MAX_TABLE_ROWS] for t in ruled if len(t) >= 2 and len(t[0]) >= 2]


def read_pdf(raw: bytes) -> dict:
    """A PDF → layout-preserving text, plus every grid found, as rows.

    `text` is what the model reads. Each page's text keeps its columns, and any
    table found is appended again as TSV under a heading, because a model reads
    a tab-separated row more reliably than a run of aligned spaces.
    """
    import pdfplumber

    pages: list[dict] = []
    tables: list[dict] = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for number, page in enumerate(pdf.pages, 1):
            try:
                text = page.extract_text(layout=True) or ""
            except Exception:  # noqa: BLE001 — one bad page must not lose the rest
                text = page.extract_text() or ""
            try:
                found = _tables(page)
            except Exception:  # noqa: BLE001
                found = []
            pages.append({"page": number, "text": text, "tables": found})
            tables += [{"page": number, "rows": rows} for rows in found]

    chunks: list[str] = []
    for page in pages:
        chunks.append(f"### PAGE {page['page']}\n{page['text']}".rstrip())
        for index, rows in enumerate(page["tables"], 1):
            body = "\n".join("\t".join(cell for cell in row) for row in rows)
            chunks.append(f"### PAGE {page['page']} TABLE {index}\n{body}")

    return {"text": "\n\n".join(chunks).strip(),
            "pages": [{"page": p["page"], "table_count": len(p["tables"])} for p in pages],
            "page_count": len(pages),
            "tables": tables}
