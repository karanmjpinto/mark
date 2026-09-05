"""
budgetdiff.py — what changed between two budgets, and what it cost.

A production budget is renegotiated five to ten times before it locks. Today
Mark stores every version (`/budget/save` → `budget:{id}`, indexed per project)
but has no way to answer the question every producer asks in the meeting:
*what moved since the version I approved, and who moved it?*

Comparing two budgets is not a text diff. Line codes are reused, descriptions get
reworded, whole sections appear and vanish, and the only thing anyone cares about
is the money. So the unit here is the line item, matched on code first and
description second — the same two-pass match `variance.py` uses against a cost
report, for the same reason.

Pure functions over the `budget_data` shape. No storage, no network.
"""

from __future__ import annotations

from typing import Any, Optional

# Reuse the matcher rather than growing a second, subtly different one.
from variance import _similarity  # noqa: F401  (private by convention, shared on purpose)

ADDED = "added"
REMOVED = "removed"
CHANGED = "changed"
UNCHANGED = "unchanged"
RENAMED = "renamed"


def _lines(budget: dict) -> list[dict]:
    out = []
    for section in budget.get("sections") or []:
        for item in section.get("items") or []:
            out.append({
                "section": str(section.get("code") or ""),
                "section_name": section.get("name") or "",
                "code": str(item.get("code") or ""),
                "desc": item.get("desc") or "",
                "sub": item.get("sub") or "",
                "amount": float(item.get("amount") or 0),
                "gst_rate": item.get("gst_rate", 0),
                "conf": item.get("conf"),
                "item_key": item.get("item_key") or "",
            })
    return out


def _match(before: list[dict], after: list[dict], *, min_similarity: float = 0.5):
    """Pair lines across two versions. Code is authoritative; where a code is
    absent or reused, description similarity within the same section decides."""
    pairs: list[tuple[Optional[int], Optional[int]]] = []
    used_after: set[int] = set()

    by_code: dict[str, list[int]] = {}
    for j, a in enumerate(after):
        if a["code"]:
            by_code.setdefault(a["code"], []).append(j)

    for i, b in enumerate(before):
        j = None
        for cand in by_code.get(b["code"], []):
            if cand not in used_after:
                j = cand
                break
        if j is None:
            best, best_score = None, 0.0
            for k, a in enumerate(after):
                if k in used_after or a["section"] != b["section"]:
                    continue
                score = _similarity(b["desc"], a["desc"])
                if score > best_score:
                    best, best_score = k, score
            j = best if best_score >= min_similarity else None
        if j is not None:
            used_after.add(j)
        pairs.append((i, j))

    for j in range(len(after)):
        if j not in used_after:
            pairs.append((None, j))
    return pairs


def diff(before: dict, after: dict, *, currency: str = "INR") -> dict:
    """Two budgets in, one change list out.

    Every line carries a `status` and, where it moved, the delta in money — which
    is the column the room reads. Reworded lines that kept their number are
    reported as `renamed` rather than buried in `unchanged`, because a line that
    changed meaning without changing price is exactly how scope creeps.
    """
    b_lines, a_lines = _lines(before), _lines(after)
    changes: list[dict] = []

    for i, j in _match(b_lines, a_lines):
        b = b_lines[i] if i is not None else None
        a = a_lines[j] if j is not None else None

        if b and not a:
            changes.append({**b, "status": REMOVED, "before": b["amount"], "after": 0.0,
                            "delta": -b["amount"], "delta_pct": -1.0})
            continue
        if a and not b:
            changes.append({**a, "status": ADDED, "before": 0.0, "after": a["amount"],
                            "delta": a["amount"], "delta_pct": None})
            continue

        delta = a["amount"] - b["amount"]
        if abs(delta) > 0.005:
            status = CHANGED
        elif b["desc"] != a["desc"] or b["sub"] != a["sub"]:
            status = RENAMED
        else:
            status = UNCHANGED
        changes.append({
            **a,
            "status": status,
            "before": b["amount"],
            "after": a["amount"],
            "delta": round(delta, 2),
            "delta_pct": round(delta / b["amount"], 4) if b["amount"] else None,
            "desc_before": b["desc"] if b["desc"] != a["desc"] else None,
            "sub_before": b["sub"] if b["sub"] != a["sub"] else None,
            "conf_before": b["conf"] if b["conf"] != a["conf"] else None,
        })

    moved = [c for c in changes if c["status"] != UNCHANGED]
    moved.sort(key=lambda c: -abs(c["delta"]))

    before_total = sum(l["amount"] for l in b_lines)
    after_total = sum(l["amount"] for l in a_lines)

    sections: dict[str, dict] = {}
    for c in changes:
        s = sections.setdefault(c["section"] or "—", {
            "section": c["section"] or "—", "section_name": c.get("section_name") or "",
            "before": 0.0, "after": 0.0, "changed_lines": 0,
        })
        s["before"] += c["before"]
        s["after"] += c["after"]
        if c["status"] != UNCHANGED:
            s["changed_lines"] += 1
    for s in sections.values():
        s["delta"] = round(s["after"] - s["before"], 2)
        s["before"] = round(s["before"], 2)
        s["after"] = round(s["after"], 2)

    counts = {k: sum(1 for c in changes if c["status"] == k)
              for k in (ADDED, REMOVED, CHANGED, RENAMED, UNCHANGED)}

    return {
        "currency": currency,
        "changes": changes,
        "moved": moved,
        "sections": sorted(sections.values(), key=lambda s: -abs(s["delta"])),
        "counts": counts,
        "totals": {
            "before": round(before_total, 2),
            "after": round(after_total, 2),
            "delta": round(after_total - before_total, 2),
            "delta_pct": round((after_total - before_total) / before_total, 4) if before_total else None,
        },
        "summary": summarise(counts, before_total, after_total, currency),
    }


def summarise(counts: dict, before_total: float, after_total: float, currency: str) -> str:
    """One sentence a producer can paste into an email."""
    delta = after_total - before_total
    direction = "up" if delta > 0 else ("down" if delta < 0 else "unchanged")
    bits = []
    if counts.get(CHANGED):
        bits.append(f"{counts[CHANGED]} line(s) repriced")
    if counts.get(ADDED):
        bits.append(f"{counts[ADDED]} added")
    if counts.get(REMOVED):
        bits.append(f"{counts[REMOVED]} removed")
    if counts.get(RENAMED):
        bits.append(f"{counts[RENAMED]} reworded at the same price")
    if not bits:
        return "No change."
    pct = f" ({abs(delta) / before_total:.1%})" if before_total else ""
    return (f"{', '.join(bits)}. Total {direction} {currency} {abs(delta):,.0f}{pct} — "
            f"{currency} {before_total:,.0f} → {currency} {after_total:,.0f}.")


def version_summary(budget_record: dict) -> dict:
    """A one-row description of a stored version, for the picker."""
    data = budget_record.get("budget_data") or {}
    total = sum(float(i.get("amount") or 0)
                for s in (data.get("sections") or []) for i in (s.get("items") or []))
    return {
        "id": budget_record.get("id"),
        "version": budget_record.get("version"),
        "created_at": budget_record.get("created_at"),
        "locked": bool(budget_record.get("locked")),
        "title": data.get("title"),
        "shoot_days": data.get("shoot_days"),
        "lines": sum(len(s.get("items") or []) for s in (data.get("sections") or [])),
        "total": round(total, 2),
        "source": budget_record.get("source"),
    }
