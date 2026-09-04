"""
variance.py — approved budget vs final actuals, as a product feature.

This is the Stage 0 teardown, moved out of a spreadsheet and into the system.
The SOW promises a Variance Ledger: every line that moved more than 10%,
classified as estimate error, scope change, vendor variance or unrecorded cost,
plus the annualised cost of the pattern at the client's production volume. Until
now that was five days of hand work per engagement producing a PDF and nothing
reusable. Here it is a function, and its output feeds `ratecard.propose_from_variance`
so a teardown makes the next budget better.

Three things this module refuses to do, because getting them wrong is worse than
not doing them:

  * **It does not guess a classification it cannot support.** Quantity moved →
    scope change. Same quantity, different unit rate → vendor variance. No
    quantity data on either side → estimate error, and the line says so. A
    producer can override any classification and the override is recorded.
  * **It does not silently drop unmatched actuals.** Money spent against no
    budget line is the most interesting finding in most teardowns, so it gets
    its own status rather than being absorbed into a total.
  * **It does not extrapolate from one production.** `recurring_patterns()`
    needs at least two ledgers before it will call anything recurring, and
    `annualise()` states its method and sample size in the output.

Pure functions over plain dicts, same as `schedule.py`. Offline-testable.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any, Iterable, Optional

DEFAULT_THRESHOLD = 0.10  # the SOW's 10%

STATUS_ON_BUDGET = "on_budget"
STATUS_OVER = "over"
STATUS_UNDER = "under"
STATUS_UNBUDGETED = "unbudgeted"   # spent with no budget line
STATUS_NOT_SPENT = "not_spent"     # budgeted, nothing spent

CLASS_ESTIMATE = "estimate_error"
CLASS_SCOPE = "scope_change"
CLASS_VENDOR = "vendor_variance"
CLASS_UNRECORDED = "unrecorded_cost"

_STOPWORDS = {"the", "and", "for", "of", "a", "per", "with", "on", "to", "day", "days"}


# ── input normalisation ───────────────────────────────────────────────────────

_AMOUNT_KEYS = ("amount", "actual", "actuals", "spent", "total", "value", "cost", "final")
_CODE_KEYS = ("code", "account", "account_code", "acc", "gl", "gl_code", "section")
_DESC_KEYS = ("desc", "description", "particulars", "narration", "item", "line", "head", "name")
_QTY_KEYS = ("qty", "quantity", "units", "days", "no", "nos", "count")
_RATE_KEYS = ("rate", "unit_rate", "day_rate", "price")
_VENDOR_KEYS = ("vendor", "supplier", "payee", "party")


def _clean_number(value: Any) -> Optional[float]:
    """Indian cost reports arrive with ₹, lakh commas, trailing CR/DR and
    brackets for negatives. All of it has to survive."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    negative = s.startswith("(") and s.endswith(")")
    s = re.sub(r"[^\d.\-]", "", s)
    if s in ("", "-", "."):
        return None
    try:
        n = float(s)
    except ValueError:
        return None
    return -n if negative else n


def _pick(row: dict, keys: Iterable[str]) -> Any:
    lowered = {str(k).strip().lower(): v for k, v in row.items()}
    for k in keys:
        if k in lowered and str(lowered[k]).strip() != "":
            return lowered[k]
    return None


def normalise_actuals(rows: Iterable[dict]) -> list[dict]:
    """Map whatever column names the client's cost report uses onto ours.

    Rows with no recoverable amount are dropped — a cost report is full of
    subtotal and header rows, and a subtotal counted as a line would double the
    variance on the section it summarises.
    """
    out = []
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        amount = _clean_number(_pick(raw, _AMOUNT_KEYS))
        if amount is None:
            continue
        desc = str(_pick(raw, _DESC_KEYS) or "").strip()
        code = str(_pick(raw, _CODE_KEYS) or "").strip()
        if not desc and not code:
            continue
        if _looks_like_total(desc):
            continue
        out.append({
            "code": code,
            "desc": desc,
            "amount": amount,
            "quantity": _clean_number(_pick(raw, _QTY_KEYS)),
            "rate": _clean_number(_pick(raw, _RATE_KEYS)),
            "vendor": str(_pick(raw, _VENDOR_KEYS) or "").strip(),
        })
    return out


def _looks_like_total(desc: str) -> bool:
    d = desc.strip().lower()
    return d in ("total", "grand total", "sub total", "subtotal", "sum") or d.startswith("total ")


def _unescape(text: str) -> str:
    """XML entities back to characters. A vendor called "Sound & Vision" arrives
    as "Sound &amp; Vision" and would otherwise be matched, grouped and printed
    that way for the rest of its life."""
    if "&" not in text:
        return text
    for entity, char in (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'),
                         ("&apos;", "'"), ("&#39;", "'"), ("&amp;", "&")):
        text = text.replace(entity, char)
    return text


def _col_index(ref: str) -> int:
    """'AB12' → 27. Column letters are base-26 with no zero."""
    letters = "".join(ch for ch in ref if ch.isalpha())
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def xlsx_rows(raw: bytes) -> list[list[str]]:
    """Read an .xlsx into rows of cell strings, stdlib only.

    The existing template extractor flattens a spreadsheet to text, which is fine
    for mimicking a call-sheet layout and useless for a cost report — a ledger
    needs columns. Blank cells are preserved by position so the header mapping
    doesn't shift halfway down the sheet.
    """
    import zipfile

    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names = z.namelist()
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            blob = z.read("xl/sharedStrings.xml").decode("utf-8", "ignore")
            shared = [_unescape(re.sub(r"<[^>]+>", "", si))
                      for si in re.findall(r"<si>(.*?)</si>", blob, re.DOTALL)]
        sheets = sorted(n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
        if not sheets:
            return []
        blob = z.read(sheets[0]).decode("utf-8", "ignore")

    rows: list[list[str]] = []
    for row_xml in re.findall(r"<row[^>]*>(.*?)</row>", blob, re.DOTALL):
        cells: dict[int, str] = {}
        # Both forms matter: a populated cell `<c ...>…</c>` and an empty one
        # `<c ... />`. Every real spreadsheet is full of the second, and missing
        # them shifts every column to its right — which silently reads an amount
        # out of the wrong column.
        for cell in re.findall(r"<c\b([^>]*?)(?:/>|>(.*?)</c>)", row_xml, re.DOTALL):
            attrs, body = cell[0], cell[1] or ""
            ref = re.search(r'r="([A-Z]+\d+)"', attrs)
            idx = _col_index(ref.group(1)) if ref else len(cells)
            is_shared = 't="s"' in attrs
            v = re.search(r"<v>(.*?)</v>", body, re.DOTALL)
            if v is None:
                inline = re.search(r"<is>(.*?)</is>", body, re.DOTALL)
                value = re.sub(r"<[^>]+>", "", inline.group(1)) if inline else ""
            elif is_shared:
                i = int(v.group(1) or 0)
                value = shared[i] if 0 <= i < len(shared) else ""
            else:
                value = v.group(1)
            cells[idx] = _unescape(value).strip()
        if cells:
            width = max(cells) + 1
            rows.append([cells.get(i, "") for i in range(width)])
    return rows


def parse_actuals_xlsx(raw: bytes) -> list[dict]:
    """.xlsx cost report → normalised rows.

    The header is the first row carrying at least two non-empty cells, one of
    which looks like an amount column — cost reports routinely open with a title
    row and a blank line before the real table starts.
    """
    rows = xlsx_rows(raw)
    header_at = None
    for i, row in enumerate(rows[:30]):
        filled = [c for c in row if c.strip()]
        if len(filled) < 2:
            continue
        lowered = {c.strip().lower() for c in row}
        if lowered & set(_AMOUNT_KEYS) and (lowered & set(_DESC_KEYS) or lowered & set(_CODE_KEYS)):
            header_at = i
            break
    if header_at is None:
        return []
    header = [c.strip() for c in rows[header_at]]
    out = []
    for row in rows[header_at + 1:]:
        record = {header[i]: row[i] for i in range(min(len(header), len(row))) if header[i]}
        if record:
            out.append(record)
    return normalise_actuals(out)


def parse_actuals_csv(text: str) -> list[dict]:
    """CSV or TSV cost report → normalised rows. Sniffs the delimiter because
    exports from Tally, Excel and Google Sheets all differ."""
    if not (text or "").strip():
        return []
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    return normalise_actuals(list(reader))


# ── matching ──────────────────────────────────────────────────────────────────

def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


def _similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _budget_lines(budget: dict) -> list[dict]:
    lines = []
    for section in budget.get("sections") or []:
        for item in section.get("items") or []:
            lines.append({
                "section": str(section.get("code") or ""),
                "section_name": section.get("name") or "",
                "code": str(item.get("code") or ""),
                "desc": item.get("desc") or "",
                "sub": item.get("sub") or "",
                "budget": float(item.get("amount") or 0),
                "gst_rate": item.get("gst_rate", 0),
                "conf": item.get("conf"),
                "item_key": item.get("item_key") or "",
                "quantity": _quantity_from_sub(item.get("sub") or ""),
            })
    return lines


_QTY_IN_SUB = re.compile(r"(\d+(?:\.\d+)?)\s*(?:x|×|days?|units?|persons?|heads?)", re.I)


def _quantity_from_sub(sub: str) -> Optional[float]:
    """Budget line bases read like '3 days × 1 unit' or '12 person-days'. Pull the
    leading quantity so a scope change can be told apart from a rate change."""
    m = _QTY_IN_SUB.search(sub or "")
    return float(m.group(1)) if m else None


def match_actuals(budget_lines: list[dict], actuals: list[dict], *,
                  min_similarity: float = 0.45) -> tuple[dict[int, list[dict]], list[dict]]:
    """Match each actual to a budget line: exact code first, then description
    similarity. Returns (matches by budget-line index, unmatched actuals)."""
    by_code: dict[str, int] = {}
    for i, bl in enumerate(budget_lines):
        if bl["code"]:
            by_code.setdefault(bl["code"], i)

    matches: dict[int, list[dict]] = {}
    unmatched: list[dict] = []
    for act in actuals:
        idx = by_code.get(act["code"]) if act["code"] else None
        if idx is None:
            best, best_score = None, 0.0
            for i, bl in enumerate(budget_lines):
                score = _similarity(act["desc"], f"{bl['desc']} {bl['sub']}")
                if score > best_score:
                    best, best_score = i, score
            idx = best if best_score >= min_similarity else None
        if idx is None:
            unmatched.append(act)
        else:
            matches.setdefault(idx, []).append(act)
    return matches, unmatched


# ── classification ────────────────────────────────────────────────────────────

def classify(budget_line: dict, actual_rows: list[dict]) -> tuple[str, str]:
    """Return (classification, basis). The basis is the sentence that goes in
    the report — every finding in the SOW has to be evidenced, and 'the model
    thought so' is not evidence."""
    b_qty = budget_line.get("quantity")
    a_qty = sum(a["quantity"] for a in actual_rows if a.get("quantity")) or None
    b_amount = budget_line.get("budget") or 0
    a_amount = sum(a["amount"] for a in actual_rows)

    if b_qty and a_qty and abs(a_qty - b_qty) / b_qty > 0.05:
        return CLASS_SCOPE, (f"quantity moved from {b_qty:g} to {a_qty:g} — "
                             f"more was bought, not mis-priced")
    if b_qty and a_qty and b_qty > 0:
        b_rate = b_amount / b_qty
        a_rate = a_amount / a_qty if a_qty else 0
        if b_rate and abs(a_rate - b_rate) / b_rate > 0.05:
            return CLASS_VENDOR, (f"same quantity, unit rate moved from {b_rate:,.0f} "
                                  f"to {a_rate:,.0f}")
    if len(actual_rows) > 1 and not b_qty:
        return CLASS_ESTIMATE, (f"{len(actual_rows)} actual line(s) against one budget line, "
                                f"no quantity basis recorded either side")
    return CLASS_ESTIMATE, "no quantity basis on the budget line — variance cannot be split into scope vs rate"


# ── the ledger ────────────────────────────────────────────────────────────────

def build_ledger(budget: dict, actuals: list[dict], *,
                 threshold: float = DEFAULT_THRESHOLD,
                 currency: str = "INR",
                 production: str = "",
                 overrides: Optional[dict] = None) -> dict:
    """Budget + actuals → the Variance Ledger the SOW sells.

    `overrides` maps a budget line code to a classification the producer
    supplied in the interview ("that was a client scope change"). A human beats
    the heuristic every time, and the ledger records that a human decided.
    """
    overrides = overrides or {}
    blines = _budget_lines(budget)
    matches, unmatched = match_actuals(blines, normalise_actuals(actuals))

    lines: list[dict] = []
    for i, bl in enumerate(blines):
        rows = matches.get(i, [])
        actual = sum(r["amount"] for r in rows) if rows else 0.0
        budgeted = bl["budget"]
        delta = actual - budgeted
        pct = (delta / budgeted) if budgeted else None

        if not rows:
            status = STATUS_NOT_SPENT if budgeted else STATUS_ON_BUDGET
        elif pct is not None and abs(pct) < threshold:
            status = STATUS_ON_BUDGET
        else:
            status = STATUS_OVER if delta > 0 else STATUS_UNDER

        cls, basis = (None, "")
        if status in (STATUS_OVER, STATUS_UNDER, STATUS_NOT_SPENT):
            cls, basis = classify(bl, rows)
            if bl["code"] in overrides:
                cls, basis = overrides[bl["code"]], "classified by the producer in interview"

        lines.append({
            **{k: bl[k] for k in ("section", "section_name", "code", "desc", "sub", "gst_rate", "item_key")},
            "budget": round(budgeted, 2),
            "actual": round(actual, 2),
            "delta": round(delta, 2),
            "delta_pct": round(pct, 4) if pct is not None else None,
            "status": status,
            "material": status in (STATUS_OVER, STATUS_UNDER, STATUS_NOT_SPENT),
            "classification": cls,
            "basis": basis,
            "quantity": bl.get("quantity"),
            # What was actually bought, when the cost report says. A rate
            # derived from actuals must divide by this and not by the budgeted
            # quantity — otherwise a line that ran three days instead of two
            # comes back as a 50%-inflated day rate.
            "actual_quantity": (sum(r["quantity"] for r in rows if r.get("quantity")) or None),
            "actual_rows": len(rows),
            "vendors": sorted({r["vendor"] for r in rows if r.get("vendor")}),
        })

    for act in unmatched:
        lines.append({
            "section": act.get("code") or "",
            "section_name": "",
            "code": act.get("code") or "",
            "desc": act["desc"],
            "sub": "",
            "gst_rate": 0,
            "item_key": "",
            "budget": 0.0,
            "actual": round(act["amount"], 2),
            "delta": round(act["amount"], 2),
            "delta_pct": None,
            "status": STATUS_UNBUDGETED,
            "material": True,
            "classification": CLASS_UNRECORDED,
            "basis": "spent against no budget line",
            "quantity": act.get("quantity"),
            "actual_rows": 1,
            "vendors": [act["vendor"]] if act.get("vendor") else [],
        })

    budget_total = sum(l["budget"] for l in lines)
    actual_total = sum(l["actual"] for l in lines)
    material = [l for l in lines if l["material"]]
    material.sort(key=lambda l: -abs(l["delta"]))

    by_class: dict[str, dict] = {}
    for l in material:
        c = l["classification"] or "unclassified"
        agg = by_class.setdefault(c, {"classification": c, "lines": 0, "delta": 0.0})
        agg["lines"] += 1
        agg["delta"] += l["delta"]
    for agg in by_class.values():
        agg["delta"] = round(agg["delta"], 2)

    return {
        "production": production,
        "currency": currency,
        "threshold": threshold,
        "lines": lines,
        "material_lines": material,
        "sections": _section_rollup(lines),
        "by_classification": sorted(by_class.values(), key=lambda a: -abs(a["delta"])),
        "totals": {
            "budget": round(budget_total, 2),
            "actual": round(actual_total, 2),
            "delta": round(actual_total - budget_total, 2),
            "delta_pct": round((actual_total - budget_total) / budget_total, 4) if budget_total else None,
            "material_lines": len(material),
            "unbudgeted_spend": round(sum(l["actual"] for l in lines if l["status"] == STATUS_UNBUDGETED), 2),
            "unspent_budget": round(sum(l["budget"] for l in lines if l["status"] == STATUS_NOT_SPENT), 2),
        },
        "unmatched_actuals": len(unmatched),
    }


def _section_rollup(lines: list[dict]) -> list[dict]:
    agg: dict[str, dict] = {}
    for l in lines:
        key = l["section"] or "—"
        row = agg.setdefault(key, {
            "section": key, "section_name": l.get("section_name") or "",
            "budget": 0.0, "actual": 0.0, "lines": 0, "material_lines": 0,
        })
        row["budget"] += l["budget"]
        row["actual"] += l["actual"]
        row["lines"] += 1
        row["material_lines"] += 1 if l["material"] else 0
    out = []
    for row in agg.values():
        row["delta"] = round(row["actual"] - row["budget"], 2)
        row["delta_pct"] = round(row["delta"] / row["budget"], 4) if row["budget"] else None
        row["budget"] = round(row["budget"], 2)
        row["actual"] = round(row["actual"], 2)
        out.append(row)
    out.sort(key=lambda r: -abs(r["delta"]))
    return out


# ── across productions ────────────────────────────────────────────────────────

def _pattern_key(line: dict) -> str:
    return (line.get("item_key") or f"{line.get('section','')}:{_normalise_desc(line.get('desc',''))}").strip(":")


def _normalise_desc(desc: str) -> str:
    return " ".join(sorted(_tokens(desc)))[:80]


def recurring_patterns(ledgers: list[dict], *, min_productions: int = 2,
                       threshold: float = DEFAULT_THRESHOLD) -> list[dict]:
    """Lines that are wrong in the same direction across productions.

    This is the finding that justifies the engagement — one job's overrun is a
    story, the same line wrong three times is a process problem. Nothing is
    called recurring below `min_productions`, and the count is reported so the
    reader can judge the sample themselves.
    """
    buckets: dict[str, list[dict]] = {}
    for led in ledgers:
        seen_in_this_ledger: set[str] = set()
        for line in led.get("lines", []):
            if not line.get("material"):
                continue
            # An unbudgeted cost has no percentage — there is nothing to divide
            # by. Excluding it would drop the most systemic finding a teardown
            # produces: a cost the template has no line for, spent every time.
            if line.get("delta_pct") is None and line.get("status") != STATUS_UNBUDGETED:
                continue
            key = _pattern_key(line)
            if key in seen_in_this_ledger:
                continue
            seen_in_this_ledger.add(key)
            buckets.setdefault(key, []).append({**line, "_production": led.get("production", "")})

    patterns = []
    for key, rows in buckets.items():
        if len(rows) < min_productions:
            continue
        directions = {1 if r["delta"] > 0 else -1 for r in rows}
        if len(directions) != 1:
            continue  # moved both ways — noise, not a pattern
        pcts = [r["delta_pct"] for r in rows if r.get("delta_pct") is not None]
        avg_pct = (sum(pcts) / len(pcts)) if pcts else None
        # Percentage materiality where a percentage exists; where it does not
        # (unbudgeted spend), any repeated cost qualifies — it was never
        # estimated, so there is no baseline to be 10% away from.
        if avg_pct is not None and abs(avg_pct) < threshold:
            continue
        patterns.append({
            "key": key,
            "desc": rows[0]["desc"],
            "section": rows[0]["section"],
            "productions": len(rows),
            "direction": "over" if rows[0]["delta"] > 0 else "under",
            "avg_delta_pct": round(avg_pct, 4) if avg_pct is not None else None,
            "avg_delta": round(sum(r["delta"] for r in rows) / len(rows), 2),
            "total_delta": round(sum(r["delta"] for r in rows), 2),
            "classifications": sorted({r["classification"] for r in rows if r.get("classification")}),
            "seen_in": [r["_production"] for r in rows],
        })
    patterns.sort(key=lambda p: -abs(p["avg_delta"]))
    return patterns


def annualise(ledgers: list[dict], *, productions_per_year: int,
              patterns: Optional[list[dict]] = None) -> dict:
    """The D3 number: what the recurring pattern costs across a year's volume.

    States its method in the output because the SOW requires the extrapolation
    method to be stated, and because a single number with no method attached is
    the thing a CFO will (rightly) refuse to accept.
    """
    if not ledgers:
        return {"error": "no ledgers supplied"}
    patterns = patterns if patterns is not None else recurring_patterns(ledgers)
    sample = len(ledgers)
    recurring_per_production = sum(p["avg_delta"] for p in patterns)
    observed_per_production = sum(l["totals"]["delta"] for l in ledgers) / sample

    return {
        "sample_productions": sample,
        "productions_per_year": productions_per_year,
        "recurring_lines": len(patterns),
        "recurring_cost_per_production": round(recurring_per_production, 2),
        "recurring_cost_per_year": round(recurring_per_production * productions_per_year, 2),
        "observed_variance_per_production": round(observed_per_production, 2),
        "observed_variance_per_year": round(observed_per_production * productions_per_year, 2),
        "method": (
            f"Recurring cost is the mean variance of the {len(patterns)} line(s) that moved in the "
            f"same direction across all {sample} sampled production(s), multiplied by a stated annual "
            f"volume of {productions_per_year}. It excludes one-off variances, which is why it is lower "
            f"than total observed variance. It assumes the sampled productions are representative of "
            f"the year's slate — a {sample}-production sample is indicative, not statistical."
        ),
        "caveats": [
            "Not an audit. Findings are evidenced against supplied documents only.",
            "Excludes variance the client classified as scope change where the scope was billed on.",
        ],
    }
