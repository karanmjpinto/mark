"""
ratecard.py — the per-tenant rate library.

The problem this solves: Mark's regional rate knowledge lives inside a prompt.
It cannot be corrected by a producer, versioned, audited, or carried from one
engagement to the next — which means the single most valuable thing the company
learns on every job (what things actually cost, here, now) evaporates the moment
the budget is generated.

This module is where it goes instead. A rate is a row:

    region · city · tier · item_key → rate, unit, currency, tax treatment,
    a source string, and a verification date.

Three things consume it:

  1. `resolve_pack()` builds a compact list for the budget agent, which is
     instructed to use a supplied rate verbatim and cite its source rather than
     estimating. That is the difference between "mid-tier 2026 market reference"
     and "₹45,000/day — verified on 3 productions, March 2026".
  2. The variance ledger (`variance.py`) proposes corrections from real actuals,
     via `propose_from_variance()`. Corrections are the moat.
  3. The producer edits rows directly. A rate a client corrected is worth more
     than a rate we guessed.

**Seeded rates are placeholders.** Everything in `backend/seeds/` ships with
`verified_at: null` and `confidence: "amber"`, because this repo is public and
real client rates do not belong in it. They exist so a cold tenant produces a
sane budget on day one, and they are meant to be overwritten by the first
teardown. `is_verified()` is the check that matters — never present an unverified
seed to a client as a verified number.

Storage follows the house pattern: Redis when available, in-memory otherwise,
every key namespaced by the active tenant via `tenancy.tkey`.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_redis = None
_mem: dict = {}


def _tkey(key: str) -> str:
    """Tenant-namespace a key.

    `tenancy` is imported lazily so this module stays importable — and therefore
    testable in CI — without FastAPI installed. Outside the running app there is
    no tenant context, and the un-prefixed key is correct.
    """
    try:
        import tenancy  # noqa: PLC0415 — deliberate, see above
        return tenancy.tkey(key)
    except Exception:
        return key

SEEDS_DIR = Path(__file__).resolve().parent / "seeds"

UNITS = ("day", "week", "flat", "person_day", "unit", "hour", "shift", "episode")
TIERS = ("low", "mid", "high", "any")
CONFIDENCE = ("green", "amber", "red")


def init(redis_client) -> None:
    global _redis
    _redis = redis_client


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


# ── storage ───────────────────────────────────────────────────────────────────

def _key(rid: str) -> str:
    return _tkey(f"rate:{rid}")


def _index_key() -> str:
    return _tkey("rates:index")


def _store(row: dict) -> None:
    k = _key(row["id"])
    if _redis:
        _redis.set(k, json.dumps(row))
        _redis.sadd(_index_key(), row["id"])
    else:
        _mem[k] = row
        _mem.setdefault(_index_key(), set()).add(row["id"])


def _load(rid: str) -> Optional[dict]:
    k = _key(rid)
    if _redis:
        raw = _redis.get(k)
        return json.loads(raw) if raw else None
    return _mem.get(k)


def _ids() -> list[str]:
    if _redis:
        return sorted(_redis.smembers(_index_key()))
    return sorted(_mem.get(_index_key(), set()))


def all_rates() -> list[dict]:
    return [row for row in (_load(rid) for rid in _ids()) if row]


def delete_rate(rid: str) -> bool:
    k = _key(rid)
    if _redis:
        existed = bool(_redis.delete(k))
        _redis.srem(_index_key(), rid)
        return existed
    existed = k in _mem
    _mem.pop(k, None)
    _mem.get(_index_key(), set()).discard(rid)
    return existed


# ── the row itself ────────────────────────────────────────────────────────────

def norm_key(value: str) -> str:
    """Item keys are the join between a rate and a budget line, so they have to
    be stable and boring: lowercase, dots and underscores only."""
    out = []
    for ch in (value or "").strip().lower():
        if ch.isalnum() or ch in "._":
            out.append(ch)
        elif ch in " -/":
            out.append("_")
    key = "".join(out).strip("._")
    while "__" in key:
        key = key.replace("__", "_")
    return key


def validate(data: dict) -> tuple[bool, str]:
    """Returns (ok, reason). Kept as a pure function so tests and the endpoint
    share one definition of a valid rate."""
    for field in ("region", "item_key", "desc", "unit", "rate", "currency"):
        if not str(data.get(field, "")).strip() and data.get(field) != 0:
            return False, f"missing required field: {field}"
    rate = data.get("rate")
    if not isinstance(rate, (int, float)) or isinstance(rate, bool) or rate < 0:
        return False, f"rate must be a non-negative number, got {rate!r}"
    if data.get("unit") not in UNITS:
        return False, f"unit must be one of {UNITS}, got {data.get('unit')!r}"
    tier = data.get("tier", "any")
    if tier not in TIERS:
        return False, f"tier must be one of {TIERS}, got {tier!r}"
    conf = data.get("confidence", "amber")
    if conf not in CONFIDENCE:
        return False, f"confidence must be one of {CONFIDENCE}, got {conf!r}"
    gst = data.get("gst_rate", 0)
    if not isinstance(gst, (int, float)) or isinstance(gst, bool) or not (0 <= gst <= 1):
        # Same 18-vs-0.18 trap the budget invariants guard. Catch it at write time.
        return False, f"gst_rate must be a decimal multiplier between 0 and 1, got {gst!r}"
    return True, ""


def upsert(data: dict) -> dict:
    """Create or replace a rate. Identity is (region, city, tier, item_key) —
    upserting the same tuple corrects the existing row rather than duplicating
    it, which is what makes the correction loop work."""
    ok, reason = validate(data)
    if not ok:
        raise ValueError(reason)

    region = str(data["region"]).strip().lower()
    city = str(data.get("city") or "").strip().lower()
    tier = data.get("tier", "any")
    item_key = norm_key(data["item_key"])

    existing = find_one(region=region, city=city, tier=tier, item_key=item_key)
    row = {
        "id": existing["id"] if existing else f"rc_{uuid.uuid4().hex[:12]}",
        "region": region,
        "city": city,
        "tier": tier,
        "item_key": item_key,
        "section": str(data.get("section") or ""),
        "desc": str(data["desc"]).strip(),
        "unit": data["unit"],
        "rate": float(data["rate"]),
        "currency": str(data["currency"]).strip().upper(),
        "gst_rate": float(data.get("gst_rate", 0)),
        "tds_section": str(data.get("tds_section") or ""),
        "confidence": data.get("confidence", "amber"),
        "source": str(data.get("source") or "").strip(),
        "verified_at": data.get("verified_at"),
        "sample_size": int(data.get("sample_size") or 0),
        "notes": str(data.get("notes") or "").strip(),
        "created_at": existing["created_at"] if existing else _now(),
        "updated_at": _now(),
    }
    _store(row)
    return row


def find_one(*, region: str, city: str, tier: str, item_key: str) -> Optional[dict]:
    for row in all_rates():
        if (row.get("region") == region and (row.get("city") or "") == (city or "")
                and row.get("tier") == tier and row.get("item_key") == item_key):
            return row
    return None


def is_verified(row: dict) -> bool:
    """A rate counts as verified only if someone put a date on it. Seeds never do."""
    return bool(row.get("verified_at"))


# ── resolution ────────────────────────────────────────────────────────────────

def _tier_rank(row_tier: str, wanted: str) -> int:
    """Exact tier beats `any` beats everything else. Lower is better."""
    if row_tier == wanted:
        return 0
    if row_tier == "any":
        return 1
    return 9


def _city_rank(row_city: str, wanted: str) -> int:
    """A city-specific rate beats a region-wide one.

    When a city IS named, a rate for a *different* city is never substituted —
    Mumbai and Hyderabad are different markets and quietly swapping one for the
    other is the error this module exists to stop. When no city is named, a
    city-specific rate is allowed through as the weakest match, and the pack
    carries its city so the agent can cite it as indicative rather than local.
    """
    if row_city == wanted:
        return 0
    if not row_city:
        return 1
    if not wanted:
        return 2
    return 9


def resolve_pack(
    region: str,
    *,
    city: str = "",
    tier: str = "mid",
    currency: Optional[str] = None,
    limit: int = 120,
) -> list[dict]:
    """Build the compact rate pack handed to the budget agent.

    One row per item_key — the best match for this (city, tier) — sorted so the
    verified rates come first, because the prompt tells the agent to treat those
    as binding. Trimmed to `limit` rows to keep input tokens bounded.
    """
    region = (region or "").strip().lower()
    city = (city or "").strip().lower()
    best: dict[str, tuple[int, dict]] = {}

    for row in all_rates():
        if row.get("region") != region:
            continue
        if currency and row.get("currency") != currency.strip().upper():
            continue
        cr = _city_rank(row.get("city") or "", city)
        tr = _tier_rank(row.get("tier") or "any", tier)
        if cr >= 9 or tr >= 9:
            continue
        score = cr * 10 + tr
        prev = best.get(row["item_key"])
        if prev is None or score < prev[0]:
            best[row["item_key"]] = (score, row)

    rows = [row for _, row in best.values()]
    rows.sort(key=lambda r: (not is_verified(r), r.get("section") or "", r["item_key"]))

    pack = []
    for row in rows[:limit]:
        pack.append({
            "item_key": row["item_key"],
            "section": row.get("section") or "",
            "desc": row["desc"],
            "unit": row["unit"],
            "rate": row["rate"],
            "currency": row["currency"],
            "gst_rate": row.get("gst_rate", 0),
            "tier": row.get("tier", "any"),
            "city": row.get("city") or "",
            "verified": is_verified(row),
            "source": row.get("source") or "",
        })
    return pack


def coverage(region: str, *, city: str = "", tier: str = "mid") -> dict:
    """How much of the pack is actually verified. This is the number to put in
    front of a client: 'your budget is 62% built on rates verified from your own
    last three productions' is a different sentence to 'AI estimated it'."""
    pack = resolve_pack(region, city=city, tier=tier, limit=10_000)
    verified = sum(1 for p in pack if p["verified"])
    return {
        "region": region,
        "city": city,
        "tier": tier,
        "rates": len(pack),
        "verified": verified,
        "verified_pct": round(verified / len(pack), 4) if pack else 0.0,
    }


# ── learning from actuals ─────────────────────────────────────────────────────

def propose_from_variance(ledger: dict, *, region: str, city: str = "",
                          tier: str = "mid", source: str = "") -> list[dict]:
    """Turn a variance ledger into proposed rate corrections.

    Deliberately returns proposals rather than writing them: a single production
    is one data point, and an actual can be high because the estimate was wrong
    OR because the shoot went sideways. A human decides. `apply_proposals()`
    commits the ones they accept.
    """
    proposals = []
    for line in ledger.get("lines", []):
        actual = line.get("actual")
        # Divide by what was actually bought. Falling back to the budgeted
        # quantity would turn a scope change (three days instead of two) into a
        # 50% inflated day rate and poison the rate card with it.
        qty = line.get("actual_quantity") or line.get("quantity") or 0
        derived_from_budget_qty = not line.get("actual_quantity") and bool(line.get("quantity"))
        if not actual or line.get("status") == "unbudgeted":
            continue
        if not line.get("item_key"):
            continue
        unit_rate = actual / qty if qty else actual
        proposals.append({
            "region": region,
            "city": city,
            "tier": tier,
            "item_key": line["item_key"],
            "section": line.get("section") or "",
            "desc": line.get("desc") or line["item_key"],
            "unit": "day" if qty else "flat",
            "rate": round(unit_rate, 2),
            "currency": ledger.get("currency", "INR"),
            "gst_rate": line.get("gst_rate", 0),
            "confidence": "green",
            "verified_at": _today(),
            "sample_size": 1,
            "source": source or f"actuals: {ledger.get('production') or 'unnamed production'}",
            "delta_pct": line.get("delta_pct"),
            "quantity_basis": "actual" if line.get("actual_quantity")
                              else ("budget" if line.get("quantity") else "none"),
            # A rate divided by the budgeted quantity is a guess about the unit
            # rate, not an observation of it. Surfaced so the producer reviewing
            # the proposal can see which is which.
            "needs_review": derived_from_budget_qty,
        })
    return proposals


def apply_proposals(proposals: list[dict]) -> list[dict]:
    """Commit accepted proposals. When a rate already exists and is verified,
    the new observation is averaged in and the sample size grows — one job is an
    anecdote, five are a rate."""
    written = []
    for p in proposals:
        existing = find_one(
            region=str(p.get("region", "")).lower(),
            city=str(p.get("city") or "").lower(),
            tier=p.get("tier", "mid"),
            item_key=norm_key(p.get("item_key", "")),
        )
        row = dict(p)
        # Review metadata travels with the proposal, not into the stored row.
        for meta in ("delta_pct", "quantity_basis", "needs_review"):
            row.pop(meta, None)
        if existing and is_verified(existing) and existing.get("sample_size", 0) > 0:
            n = existing["sample_size"]
            blended = (existing["rate"] * n + float(p["rate"])) / (n + 1)
            row["rate"] = round(blended, 2)
            row["sample_size"] = n + 1
            row["source"] = f"{existing.get('source','')} + {p.get('source','')}".strip(" +")
        written.append(upsert(row))
    return written


# ── seeds ─────────────────────────────────────────────────────────────────────

def load_seed_file(path: Path) -> list[dict]:
    with open(path) as f:
        payload = json.load(f)
    return payload.get("rates", [])


def seed(region: str, *, overwrite: bool = False) -> dict:
    """Load `seeds/rates_{region}.json` into the tenant's library.

    Non-destructive by default: an existing row for the same identity tuple is
    left alone, so seeding a tenant that has already corrected its rates cannot
    stamp on their work.
    """
    region = (region or "").strip().lower()
    path = SEEDS_DIR / f"rates_{region}.json"
    if not path.exists():
        raise FileNotFoundError(f"no seed file for region {region!r}")

    written, skipped = 0, 0
    for raw in load_seed_file(path):
        row = dict(raw)
        row.setdefault("region", region)
        row.setdefault("confidence", "amber")
        row["verified_at"] = None  # seeds are never verified. See module docstring.
        existing = find_one(
            region=region,
            city=str(row.get("city") or "").lower(),
            tier=row.get("tier", "any"),
            item_key=norm_key(row.get("item_key", "")),
        )
        if existing and not overwrite:
            skipped += 1
            continue
        upsert(row)
        written += 1
    return {"region": region, "written": written, "skipped": skipped,
            "note": "Seeded rates are unverified market references. Replace them "
                    "with verified rates from a teardown before quoting a client."}


def available_seeds() -> list[str]:
    if not SEEDS_DIR.exists():
        return []
    return sorted(p.stem.replace("rates_", "") for p in SEEDS_DIR.glob("rates_*.json"))
