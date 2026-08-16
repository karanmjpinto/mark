"""
invariants.py — budget correctness checks, as pure functions.

These are the things that must be true of ANY budget Mark produces, independent
of the exact numbers. They're the promotion of the one-dimensional
`_post_ratio_hint` guardrail (which only runs in prod, on one axis) into a real,
CI-runnable suite. A prompt tweak or rate-card change that breaks any of these
should fail the build, not surface as a producer complaint.

Every check takes the budget dict (the `budget_data` / BudgetResult shape) plus
an optional `expect` dict of per-fixture bounds, and returns a list of Result.
No network, no API key — safe to run in CI on every push.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class Result:
    name: str
    passed: bool
    detail: str = ""

    @property
    def icon(self) -> str:
        return "✅" if self.passed else "❌"


def _sections(budget: dict) -> list[dict]:
    return budget.get("sections") or []


def _all_items(budget: dict) -> list[dict]:
    out = []
    for s in _sections(budget):
        out.extend(s.get("items") or [])
    return out


def _section_codes(budget: dict) -> set[str]:
    return {str(s.get("code") or "") for s in _sections(budget)}


# ── individual checks ──────────────────────────────────────────────────────────

def check_shape(budget: dict, expect: dict) -> list[Result]:
    required = ["title", "production_type", "shoot_days", "scale_tier", "sections"]
    missing = [k for k in required if k not in budget]
    out = [Result("shape.required_keys", not missing,
                  "" if not missing else f"missing top-level keys: {missing}")]
    secs = _sections(budget)
    out.append(Result("shape.sections_nonempty", len(secs) > 0,
                      f"{len(secs)} sections"))
    out.append(Result("shape.section_count_8_12", 8 <= len(secs) <= 12,
                      f"{len(secs)} sections (prompt target 8–12)"))
    return out


def check_gst_decimal(budget: dict, expect: dict) -> list[Result]:
    """The 18-vs-0.18 bug: gst_rate is a decimal multiplier. Anything > 1 means
    the frontend will multiply the tax line by ~100x."""
    bad = []
    for it in _all_items(budget):
        g = it.get("gst_rate")
        if not isinstance(g, (int, float)) or g < 0 or g > 1:
            bad.append(f"{it.get('code')}={g!r}")
    return [Result("tax.gst_rate_is_decimal", not bad,
                   "" if not bad else f"non-decimal gst_rate(s): {bad[:6]}")]


def check_confidence_markers(budget: dict, expect: dict) -> list[Result]:
    allowed = {"green", "amber", "red"}
    bad = [f"{it.get('code')}={it.get('conf')!r}"
           for it in _all_items(budget) if it.get("conf") not in allowed]
    return [Result("items.conf_valid", not bad,
                   "" if not bad else f"invalid conf marker(s): {bad[:6]}")]


def check_amounts_numeric(budget: dict, expect: dict) -> list[Result]:
    bad = []
    for it in _all_items(budget):
        a = it.get("amount")
        if not isinstance(a, (int, float)) or isinstance(a, bool) or a < 0:
            bad.append(f"{it.get('code')}={a!r}")
    return [Result("items.amounts_numeric", not bad,
                   "" if not bad else f"non-numeric/negative amount(s): {bad[:6]}")]


def check_required_sections(budget: dict, expect: dict) -> list[Result]:
    """Editorial (12900) + Post Sound (13100) must exist for anything with an
    edit + sound deliverable — i.e. every TVC / MV / feature. This is the
    structural half of the post-production guardrail."""
    codes = _section_codes(budget)
    need = expect.get("require_sections", ["12900", "13100"])
    missing = [c for c in need if c not in codes]
    return [Result("sections.required_present", not missing,
                   "" if not missing else f"missing required section code(s): {missing}")]


def check_post_ratio(budget: dict, expect: dict) -> list[Result]:
    """Post should land in a sane band relative to production. Mirrors
    backend `_post_ratio_hint` — kept here as the canonical definition."""
    prod_total = 0.0
    post_total = 0.0
    has_vfx = False
    for s in _sections(budget):
        items_total = sum(float(li.get("amount") or 0) for li in (s.get("items") or []))
        stype = s.get("type", "")
        code = str(s.get("code") or "")
        if stype in ("below_the_line", "above_the_line"):
            prod_total += items_total
        elif stype == "post" or code.startswith(("129", "131", "133")):
            post_total += items_total
        if code == "13300" or "vfx" in (s.get("name") or "").lower():
            has_vfx = True
    if prod_total <= 0:
        return [Result("ratio.post_vs_production", False, "production total is 0 — cannot compute ratio")]
    ratio = post_total / prod_total
    lo = expect.get("post_ratio_min", 0.12)
    hi = expect.get("post_ratio_max", 0.40)
    # VFX-heavy jobs legitimately blow past the ceiling; only enforce the floor.
    ok = (ratio >= lo) and (has_vfx or ratio <= hi)
    return [Result("ratio.post_vs_production", ok,
                   f"post/production={ratio:.2%} (band {lo:.0%}–{hi:.0%}, vfx={has_vfx})")]


def check_reconciliation(budget: dict, expect: dict) -> list[Result]:
    """When a fixture declares an expected grand-total band, the sum of all line
    items (pre-tax) must fall inside it. Catches scale drift — the NIKE
    ₹1.95Cr-vs-₹3.21Cr class of problem — at eval time instead of in prod."""
    out = []
    grand = sum(float(it.get("amount") or 0) for it in _all_items(budget))
    tmin = expect.get("total_min")
    tmax = expect.get("total_max")
    if tmin is not None or tmax is not None:
        ok = (tmin is None or grand >= tmin) and (tmax is None or grand <= tmax)
        out.append(Result("reconcile.total_in_band", ok,
                           f"grand_total={grand:,.0f} (band {tmin:,}–{tmax:,})"))
    if expect.get("shoot_days") is not None:
        ok = budget.get("shoot_days") == expect["shoot_days"]
        out.append(Result("reconcile.shoot_days", ok,
                           f"shoot_days={budget.get('shoot_days')} expected {expect['shoot_days']}"))
    return out


ALL_CHECKS = [
    check_shape,
    check_gst_decimal,
    check_confidence_markers,
    check_amounts_numeric,
    check_required_sections,
    check_post_ratio,
    check_reconciliation,
]


def run_all(budget: dict, expect: Optional[dict] = None) -> list[Result]:
    expect = expect or {}
    results: list[Result] = []
    for check in ALL_CHECKS:
        try:
            results.extend(check(budget, expect))
        except Exception as e:  # a check crashing is itself a failure
            results.append(Result(check.__name__, False, f"check raised {type(e).__name__}: {e}"))
    return results
