"""
pdfdoc.py — a PDF that looks like a call sheet, not a transcript.

The first version of this wrote one left-aligned line per fact. Everything was
correct and nobody would send it: a crew member opens a call sheet on a phone
and wants the call time in the first second, which needs a header band, a strip
of times, section bars and real tables with rules. So this module is a small
canvas — rectangles, rules, measured text — and a renderer over the same block
list `write_docx` takes.

Still stdlib only. A PDF is a few objects and a byte-offset table, and the
alternative is a wheel to install on every rebuild.
"""

from __future__ import annotations

from typing import Any, Iterable

PAGE_W, PAGE_H = 595, 842                 # A4 at 72 dpi
MARGIN = 40
LEFT, RIGHT = MARGIN, PAGE_W - MARGIN
WIDTH = RIGHT - LEFT
TOP = PAGE_H - MARGIN
FLOOR = 64                                 # the footer lives below this

INK = (0.10, 0.10, 0.11)
MUTED = (0.42, 0.42, 0.45)
BAR = (0.11, 0.12, 0.14)
PANEL = (0.945, 0.945, 0.95)
ZEBRA = (0.972, 0.972, 0.978)
RULE = (0.80, 0.80, 0.83)
WHITE = (1, 1, 1)
ALERT = (0.65, 0.11, 0.11)

# Helvetica and Helvetica-Bold advance widths, characters 32–126, in 1/1000 em.
# Packed because the alternative is a 95-line literal twice over. Anything
# outside this range falls back to the width of "n", which is close enough for
# a column edge and never zero.
_W = {
    "F1": '02780278035505560556088906670191033303330389058402780333027802780556055605560556055605560556055605560556027802780584058405840556101506670667072207220667061107780722027805000667055608330722077806670778072206670611072206670944066706670611027802780278046905560333055605560500055605560278055605560222022205000222083305560556055605560333050002780556050007220500050005000334026003340584',
    "F2": '02780333047405560556088907220238033303330389058402780333027802780556055605560556055605560556055605560556033303330584058405840611097507220722072207220667061107780722027805560722061108330722077806670778072206670611072206670944066706670611033302780333058405560333055606110556061105560333061106110278027805560278088906110611061106110389055603330611055607780556055605000389028003890584',
}


def width_of(text: Any, font: str = "F1", size: float = 9) -> float:
    """How wide a string prints. The whole layout hangs off this."""
    table = _W.get(font, _W["F1"])
    total = 0
    for ch in str(text if text is not None else ""):
        code = ord(ch) - 32
        if 0 <= code < 95:
            total += int(table[code * 4:code * 4 + 4])
        else:
            total += 556
    return total * size / 1000.0


def wrap(text: Any, max_w: float, font: str = "F1", size: float = 9) -> list[str]:
    """Break on spaces to fit a column. A word longer than the column is cut
    rather than allowed to run over the rule."""
    out: list[str] = []
    for para in str(text if text is not None else "").splitlines() or [""]:
        line = ""
        for word in para.split():
            candidate = f"{line} {word}".strip()
            if width_of(candidate, font, size) <= max_w or not line:
                if width_of(candidate, font, size) > max_w and not line:
                    out.append(fit(word, max_w, font, size))
                    line = ""
                    continue
                line = candidate
            else:
                out.append(line)
                line = word
        out.append(line)
    return [ln for ln in out] or [""]


def fit(text: Any, max_w: float, font: str = "F1", size: float = 9) -> str:
    """One line, truncated with an ellipsis if it will not fit."""
    text = str(text if text is not None else "")
    if width_of(text, font, size) <= max_w:
        return text
    cut = text
    while cut and width_of(cut + "...", font, size) > max_w:
        cut = cut[:-1]
    return (cut.rstrip() + "...") if cut else ""


def _encode(text: str) -> bytes:
    """The fonts are declared /WinAnsiEncoding, which is cp1252 — so encode as
    cp1252. Encoding as latin-1 turned every em dash and curly quote in a
    producer's own words into a question mark on the printed page."""
    return text.encode("cp1252", "replace")


def _esc(text: Any) -> str:
    return (str(text if text is not None else "")
            .replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)"))


class Canvas:
    """Pages of content-stream operators, with a cursor that runs down the page
    and breaks when a block will not fit."""

    def __init__(self, footer: str = "") -> None:
        self._pages: list[list[str]] = [[]]
        self.y = TOP
        self.footer = footer
        self.on_break = None          # redrawn at the top of each new page

    # ── primitives ───────────────────────────────────────────────────────────
    def _op(self, op: str) -> None:
        self._pages[-1].append(op)

    def rect(self, x: float, y: float, w: float, h: float, fill: tuple) -> None:
        self._op(f"{fill[0]:.3f} {fill[1]:.3f} {fill[2]:.3f} rg "
                 f"{x:.2f} {y:.2f} {w:.2f} {h:.2f} re f")

    def rule(self, y: float, x1: float = LEFT, x2: float = RIGHT,
             w: float = 0.5, color: tuple = RULE) -> None:
        self._op(f"{color[0]:.3f} {color[1]:.3f} {color[2]:.3f} RG {w:.2f} w "
                 f"{x1:.2f} {y:.2f} m {x2:.2f} {y:.2f} l S")

    def vrule(self, x: float, y1: float, y2: float,
              w: float = 0.5, color: tuple = RULE) -> None:
        self._op(f"{color[0]:.3f} {color[1]:.3f} {color[2]:.3f} RG {w:.2f} w "
                 f"{x:.2f} {y1:.2f} m {x:.2f} {y2:.2f} l S")

    def text(self, x: float, y: float, s: Any, font: str = "F1", size: float = 9,
             color: tuple = INK) -> None:
        self._op(f"BT /{font} {size:g} Tf {color[0]:.3f} {color[1]:.3f} {color[2]:.3f} rg "
                 f"1 0 0 1 {x:.2f} {y:.2f} Tm ({_esc(s)}) Tj ET")

    def text_right(self, x: float, y: float, s: Any, font: str = "F1", size: float = 9,
                   color: tuple = INK) -> None:
        self.text(x - width_of(s, font, size), y, s, font, size, color)

    # ── flow ─────────────────────────────────────────────────────────────────
    def new_page(self) -> None:
        self._pages.append([])
        self.y = TOP
        if self.on_break:
            self.on_break(self)

    def need(self, height: float) -> None:
        if self.y - height < FLOOR:
            self.new_page()

    # ── output ───────────────────────────────────────────────────────────────
    def to_bytes(self) -> bytes:
        total = len(self._pages)
        for index, page in enumerate(self._pages, 1):
            page.append(f"{RULE[0]:.3f} {RULE[1]:.3f} {RULE[2]:.3f} RG 0.50 w "
                        f"{LEFT:.2f} 50.00 m {RIGHT:.2f} 50.00 l S")
            if self.footer:
                page.append(f"BT /F1 7.5 Tf {MUTED[0]:.3f} {MUTED[1]:.3f} {MUTED[2]:.3f} rg "
                            f"1 0 0 1 {LEFT:.2f} 38.00 Tm "
                            f"({_esc(fit(self.footer, WIDTH - 70, 'F1', 7.5))}) Tj ET")
            stamp = f"Page {index} of {total}"
            page.append(f"BT /F1 7.5 Tf {MUTED[0]:.3f} {MUTED[1]:.3f} {MUTED[2]:.3f} rg "
                        f"1 0 0 1 {RIGHT - width_of(stamp, 'F1', 7.5):.2f} 38.00 Tm "
                        f"({_esc(stamp)}) Tj ET")
        return _assemble([_encode("\n".join(p)) for p in self._pages])


def _assemble(streams: list[bytes]) -> bytes:
    """Streams → a PDF with a cross-reference table that resolves."""
    page_ids = [5 + 2 * i for i in range(len(streams))]
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: ("<< /Type /Pages /Count " + str(len(streams)) + " /Kids ["
            + " ".join(f"{pid} 0 R" for pid in page_ids) + "] >>").encode(),
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
           b"/Encoding /WinAnsiEncoding >>",
        4: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold "
           b"/Encoding /WinAnsiEncoding >>",
    }
    for index, stream in enumerate(streams):
        pid = page_ids[index]
        objects[pid] = (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            "/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> "
            f"/Contents {pid + 1} 0 R >>").encode()
        objects[pid + 1] = (b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
                            + stream + b"\nendstream")

    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for oid in sorted(objects):
        offsets[oid] = len(out)
        out += str(oid).encode() + b" 0 obj\n" + objects[oid] + b"\nendobj\n"
    xref_at = len(out)
    highest = max(objects)
    out += b"xref\n0 " + str(highest + 1).encode() + b"\n0000000000 65535 f \n"
    for oid in range(1, highest + 1):
        out += ("%010d 00000 n \n" % offsets.get(oid, 0)).encode()
    out += (b"trailer\n<< /Size " + str(highest + 1).encode() + b" /Root 1 0 R >>\nstartxref\n"
            + str(xref_at).encode() + b"\n%%EOF\n")
    return bytes(out)


# ── the parts a call sheet is made of ────────────────────────────────────────

def header(c: Canvas, title: str, left_lines: Iterable[Any] = (),
           right_label: str = "", right_value: str = "") -> None:
    """The band across the top: what this is, and the one date it is for."""
    left_lines = [str(l) for l in left_lines if l]
    height = 44 + 11 * max(0, len(left_lines) - 1)
    c.need(height + 8)
    top = c.y
    c.rect(LEFT, top - height, WIDTH, height, BAR)
    c.text(LEFT + 14, top - 24, fit(title, WIDTH - 180, "F2", 16), "F2", 16, WHITE)
    y = top - 38
    for line in left_lines:
        c.text(LEFT + 14, y, fit(line, WIDTH - 180, "F1", 8.5), "F1", 8.5, (0.78, 0.78, 0.80))
        y -= 11
    if right_label:
        c.text_right(RIGHT - 14, top - 17, right_label, "F1", 7.5, (0.72, 0.72, 0.74))
    if right_value:
        c.text_right(RIGHT - 14, top - 33, fit(right_value, 180, "F2", 13), "F2", 13, WHITE)
    c.y = top - height - 10


def strip(c: Canvas, cells: list[tuple[str, Any]]) -> None:
    """The times, in boxes, side by side. This is the part somebody reads at
    06:00 on a phone, so it is the biggest type on the page after the title."""
    cells = [(str(label), str(value)) for label, value in cells if value]
    if not cells:
        return
    height = 40
    c.need(height + 8)
    top = c.y
    c.rect(LEFT, top - height, WIDTH, height, PANEL)
    col = WIDTH / len(cells)
    for index, (label, value) in enumerate(cells):
        x = LEFT + index * col
        if index:
            c.vrule(x, top - height + 6, top - 6)
        c.text(x + 12, top - 15, label.upper(), "F1", 7, MUTED)
        c.text(x + 12, top - 31, fit(value, col - 24, "F2", 12), "F2", 12, INK)
    c.y = top - height - 14


def band(c: Canvas, label: str) -> None:
    """A section bar. Dark, so the eye can find the crew list by scrolling."""
    c.need(40)
    top = c.y
    c.rect(LEFT, top - 15, WIDTH, 15, BAR)
    c.text(LEFT + 10, top - 11, str(label).upper(), "F2", 8.5, WHITE)
    c.y = top - 15 - 9


def kv(c: Canvas, pairs: list[tuple[str, Any]]) -> None:
    """Label on the left, wrapped value on the right. For the address, the
    hospital, the parking — things that are a sentence, not a column."""
    label_w = 108
    for label, value in pairs:
        if value in (None, "", []):
            continue
        lines = wrap(value, WIDTH - label_w - 12, "F1", 9.5)
        height = max(13, 12 * len(lines))
        c.need(height + 4)
        top = c.y
        c.text(LEFT + 2, top - 9, fit(str(label).upper(), label_w - 8, "F2", 7.5), "F2", 7.5, MUTED)
        y = top - 9
        for line in lines:
            c.text(LEFT + label_w, y, line, "F1", 9.5, INK)
            y -= 12
        c.y = top - height - 2
        c.rule(c.y + 3, color=(0.90, 0.90, 0.92))
    c.y -= 7


def table(c: Canvas, columns: list[str], rows: list[list], widths: list[float] | None = None,
          bold_first: bool = True) -> None:
    """A real table: filled header, column rules, banded rows. The header
    repeats on every page, because a crew list that runs over a page break and
    loses its column names is worse than no table at all."""
    columns = [str(h) for h in (columns or [])]
    if not columns:
        return
    fractions = widths or [1.0 / len(columns)] * len(columns)
    scale = sum(fractions) or 1
    cols = [WIDTH * f / scale for f in fractions]
    xs, running = [], LEFT
    for w in cols:
        xs.append(running)
        running += w

    def draw_head(canvas: Canvas) -> None:
        top = canvas.y
        canvas.rect(LEFT, top - 15, WIDTH, 15, (0.22, 0.23, 0.26))
        for index, name in enumerate(columns):
            canvas.text(xs[index] + 6, top - 11, fit(name.upper(), cols[index] - 12, "F2", 7.5),
                        "F2", 7.5, WHITE)
            if index:                      # barely visible on the bar, but it
                canvas.vrule(xs[index], top - 15, top,   # keeps the header row
                             color=(0.30, 0.31, 0.34))   # parseable as cells
        canvas.vrule(LEFT, top - 15, top, color=(0.30, 0.31, 0.34))
        canvas.vrule(RIGHT, top - 15, top, color=(0.30, 0.31, 0.34))
        canvas.y = top - 15

    c.need(50)
    draw_head(c)
    previous_break, c.on_break = c.on_break, lambda canvas: draw_head(canvas)
    try:
        for number, row in enumerate(rows or []):
            cells = [("" if v is None else str(v)) for v in list(row)[:len(columns)]]
            cells += [""] * (len(columns) - len(cells))
            wrapped = [wrap(cell, cols[i] - 12, "F2" if (bold_first and i == 0) else "F1", 8.5)
                       for i, cell in enumerate(cells)]
            height = max(14, 11 * max(len(w) for w in wrapped) + 5)
            if c.y - height < FLOOR:
                c.new_page()
            top = c.y
            if number % 2:
                c.rect(LEFT, top - height, WIDTH, height, ZEBRA)
            for index, lines in enumerate(wrapped):
                font = "F2" if (bold_first and index == 0) else "F1"
                y = top - 11
                for line in lines:
                    c.text(xs[index] + 6, y, line, font, 8.5, INK)
                    y -= 11
                # Every column edge, the outer two included. Without them the
                # first column has no left boundary, and a reader looking for a
                # grid finds the roles and loses the names.
                c.vrule(xs[index], top - height, top, color=(0.88, 0.88, 0.90))
            c.vrule(RIGHT, top - height, top, color=(0.88, 0.88, 0.90))
            c.rule(top - height, color=(0.88, 0.88, 0.90))
            c.y = top - height
    finally:
        c.on_break = previous_break
    c.rule(c.y, w=0.8)
    c.y -= 14


def banner(c: Canvas, label: str, text: Any = "", color: tuple = ALERT) -> None:
    """The stop sign. A sheet that is missing something must say so where it
    cannot be scrolled past."""
    lines = wrap(text, WIDTH - 24, "F1", 9) if text else []
    height = 20 + (12 * len(lines) + 6 if lines else 0)
    c.need(height + 8)
    top = c.y
    c.rect(LEFT, top - height, WIDTH, height, color)
    c.text(LEFT + 12, top - 14, str(label).upper(), "F2", 9.5, WHITE)
    y = top - 30
    for line in lines:
        c.text(LEFT + 12, y, line, "F1", 9, WHITE)
        y -= 12
    c.y = top - height - 12


def paragraph(c: Canvas, text: Any, font: str = "F1", size: float = 9.5,
              color: tuple = INK, leading: float = 13) -> None:
    for line in wrap(text, WIDTH, font, size):
        c.need(leading)
        c.text(LEFT, c.y - size, line, font, size, color)
        c.y -= leading


def bullets(c: Canvas, items: Iterable[Any]) -> None:
    for item in items:
        lines = wrap(item, WIDTH - 16, "F1", 9.5)
        c.need(13 * len(lines))
        top = c.y
        c.text(LEFT + 3, top - 9.5, "-", "F1", 9.5, MUTED)
        y = top - 9.5
        for line in lines:
            c.text(LEFT + 16, y, line, "F1", 9.5, INK)
            y -= 13
        c.y = top - 13 * len(lines)
    c.y -= 6


# ── the block list, rendered ─────────────────────────────────────────────────

def render(blocks: list[dict | str], *, title: str = "", footer: str = "") -> bytes:
    """The same block list `write_docx` takes, drawn properly.

    `title`/`heading`/`text` keep working for every other document in the app;
    `header`/`strip`/`band`/`kv`/`table`/`banner`/`bullets` are what a call
    sheet is built from.
    """
    c = Canvas(footer=footer)
    if title:
        paragraph(c, title, "F2", 17, INK, 22)
        c.y -= 4
    for block in (blocks or []):
        if not isinstance(block, dict):
            paragraph(c, block)
            continue
        style = str(block.get("style") or "text")
        if style == "header":
            header(c, block.get("text") or "", block.get("lines") or (),
                   block.get("right_label") or "", block.get("right_value") or "")
        elif style == "strip":
            strip(c, [tuple(cell) for cell in (block.get("cells") or [])])
        elif style == "band":
            band(c, block.get("text") or "")
        elif style == "kv":
            kv(c, [tuple(pair) for pair in (block.get("pairs") or [])])
        elif style == "table":
            table(c, block.get("columns") or [], block.get("rows") or [],
                  block.get("widths"), bold_first=bool(block.get("bold_first", True)))
        elif style == "banner":
            banner(c, block.get("text") or "", block.get("detail") or "")
        elif style == "bullets":
            bullets(c, block.get("items") or [])
        elif style == "title":
            paragraph(c, block.get("text") or "", "F2", 17, INK, 22)
        elif style == "heading":
            c.y -= 6
            paragraph(c, block.get("text") or "", "F2", 11.5, INK, 16)
        else:
            paragraph(c, block.get("text") or "")
    return c.to_bytes()
