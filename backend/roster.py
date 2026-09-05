"""
roster.py — the people and vendors a company actually uses, and what it paid them.

Crew records already existed, but only inside a project: hire a gaffer on three
jobs and you get three unconnected rows and no memory. The question a producer
asks — *what did we pay him last time?* — had no answer anywhere in the system,
which is the same hole `ratecard.py` fills for market rates, one level down. A
rate card says what a gaffer costs. A roster says what **this** gaffer costs, and
whether he has gone up twice this year.

Two record types:

  * a **roster entry** — a person or a vendor, at tenant level, deduplicated on
    phone or email before name, because the same person appears as "Ravi",
    "Ravi K." and "RAVI KULKARNI" across three call sheets;
  * an **engagement** — one production, one role, one rate, one date. Rate
    history is just engagements sorted by date, and every figure it reports is
    traceable to one.

It feeds the rate library rather than duplicating it: `propose_rates()` turns
repeated engagements into rate-card rows, with the median rather than the last
paid, and never from a single observation — the same rule `variance.py` applies
to recurring findings, for the same reason.

Pure logic plus a tenant-scoped store. Offline-testable.
"""

from __future__ import annotations

import json
import re
import statistics
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

_redis = None
_mem: dict = {}

KIND_PERSON = "person"
KIND_VENDOR = "vendor"
SOURCES = ("manual", "budget", "actuals", "callsheet", "import")


def init(redis_client) -> None:
    global _redis
    _redis = redis_client


def _tkey(key: str) -> str:
    try:
        import tenancy  # noqa: PLC0415 — lazy, so this module imports without FastAPI
        return tenancy.tkey(key)
    except Exception:
        return key


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── storage ───────────────────────────────────────────────────────────────────

def _key(rid: str) -> str:
    return _tkey(f"roster:{rid}")


def _index() -> str:
    return _tkey("roster:index")


def _store(row: dict) -> None:
    k = _key(row["id"])
    if _redis:
        _redis.set(k, json.dumps(row))
        _redis.sadd(_index(), row["id"])
    else:
        _mem[k] = row
        _mem.setdefault(_index(), set()).add(row["id"])


def get(rid: str) -> Optional[dict]:
    k = _key(rid)
    if _redis:
        raw = _redis.get(k)
        return json.loads(raw) if raw else None
    return _mem.get(k)


def all_entries() -> list[dict]:
    ids = sorted(_redis.smembers(_index())) if _redis else sorted(_mem.get(_index(), set()))
    return [row for row in (get(i) for i in ids) if row]


def delete(rid: str) -> bool:
    k = _key(rid)
    if _redis:
        existed = bool(_redis.delete(k))
        _redis.srem(_index(), rid)
        return existed
    existed = k in _mem
    _mem.pop(k, None)
    _mem.get(_index(), set()).discard(rid)
    return existed


# ── identity ──────────────────────────────────────────────────────────────────

def norm_name(value: str) -> str:
    """Casefolded, punctuation-stripped, initials collapsed. 'Ravi K.' and
    'RAVI  K' are the same person; 'Ravi Kulkarni' is not assumed to be."""
    v = re.sub(r"[^a-z0-9 ]", " ", (value or "").lower())
    return " ".join(v.split())


def norm_phone(value: str) -> str:
    """Digits only, last ten kept. Indian numbers arrive as +91 98200 00001,
    9820000001, 09820000001 and 919820000001 — all the same handset."""
    digits = re.sub(r"\D", "", value or "")
    return digits[-10:] if len(digits) >= 10 else digits


def find_existing(data: dict) -> Optional[dict]:
    """Phone, then email, then exact normalised name.

    Name alone is deliberately the weakest signal and only matches exactly —
    merging two different Ravis because their names are similar corrupts a rate
    history in a way nobody would ever notice.
    """
    phone = norm_phone(data.get("phone", ""))
    email = (data.get("email") or "").strip().lower()
    name = norm_name(data.get("name", ""))
    for row in all_entries():
        if phone and norm_phone(row.get("phone", "")) == phone:
            return row
        if email and (row.get("email") or "").strip().lower() == email:
            return row
    if name:
        for row in all_entries():
            if norm_name(row.get("name", "")) == name and not row.get("phone") and not row.get("email"):
                return row
    return None


def _better_name(existing: str, incoming: str) -> str:
    """More words wins; on a tie the existing name stays. An explicit rename is
    still possible by passing the entry's `id` with the new name and no other
    match — see `upsert`."""
    if not existing:
        return incoming
    if not incoming:
        return existing
    if len(norm_name(incoming).split()) > len(norm_name(existing).split()):
        return incoming
    return existing


def _derive_item_key(role: str) -> str:
    """A role normalises to a rate-card key: "Gaffer" → `gaffer`, "1st AD" →
    `1st_ad`. Derived rather than guessed — it is the same normalisation the rate
    card uses on its own keys, so a match is a match and a miss is just a miss.
    Recorded as derived so a producer can see it was not confirmed."""
    if not role:
        return ""
    try:
        import ratecard  # noqa: PLC0415 — optional; roster works without it
        return ratecard.norm_key(role)
    except Exception:
        return ""


def upsert(data: dict) -> dict:
    """Add or update one person or vendor. Contact details fill in over time —
    a name off a call sheet today, a phone number from the next job — so a blank
    field never overwrites something we already know."""
    name = (data.get("name") or "").strip()
    if not name:
        raise ValueError("a roster entry needs a name")

    existing = find_existing(data) if not data.get("id") else get(data["id"])
    row = dict(existing) if existing else {
        "id": f"rt_{uuid.uuid4().hex[:12]}",
        "created_at": _now(),
        "engagements": [],
    }
    row.update({
        # Keep the fuller name. Call sheets shorten people — "Ravi Kulkarni"
        # becomes "Ravi K" on the next job — and letting the latest write win
        # degrades the roster every time it is used, silently.
        "name": _better_name(row.get("name", ""), name),
        "kind": data.get("kind") or row.get("kind") or KIND_PERSON,
        "role": (data.get("role") or row.get("role") or "").strip(),
        "department": (data.get("department") or row.get("department") or "").strip(),
        "city": (data.get("city") or row.get("city") or "").strip().lower(),
        "phone": (data.get("phone") or row.get("phone") or "").strip(),
        "email": (data.get("email") or row.get("email") or "").strip(),
        "gst_number": (data.get("gst_number") or row.get("gst_number") or "").strip(),
        "item_key": (data.get("item_key") or row.get("item_key")
                     or _derive_item_key(data.get("role") or row.get("role") or "")).strip(),
        "item_key_source": ("given" if data.get("item_key") or row.get("item_key")
                            else ("derived from role" if data.get("role") or row.get("role") else "")),
        "tags": sorted(set((row.get("tags") or []) + (data.get("tags") or []))),
        "notes": (data.get("notes") or row.get("notes") or "").strip(),
        "updated_at": _now(),
    })
    row.setdefault("engagements", [])
    _store(row)
    return row


# ── engagements ───────────────────────────────────────────────────────────────

def record_engagement(roster_id: str, engagement: dict) -> dict:
    """One job, one rate. `date` is what orders a rate history, so an engagement
    without one is accepted but sorts last and is flagged in the history."""
    row = get(roster_id)
    if not row:
        raise KeyError("roster entry not found")
    rate = engagement.get("rate")
    if not isinstance(rate, (int, float)) or isinstance(rate, bool) or rate < 0:
        raise ValueError(f"rate must be a non-negative number, got {rate!r}")

    entry = {
        "id": f"en_{uuid.uuid4().hex[:10]}",
        "production": (engagement.get("production") or "").strip(),
        "date": (engagement.get("date") or "").strip(),
        "role": (engagement.get("role") or row.get("role") or "").strip(),
        "rate": float(rate),
        "unit": engagement.get("unit") or "day",
        "quantity": engagement.get("quantity"),
        "currency": (engagement.get("currency") or "INR").upper(),
        "item_key": (engagement.get("item_key") or row.get("item_key") or "").strip(),
        "source": engagement.get("source") if engagement.get("source") in SOURCES else "manual",
        "note": (engagement.get("note") or "").strip(),
        "recorded_at": _now(),
    }
    row.setdefault("engagements", []).append(entry)
    row["updated_at"] = _now()
    _store(row)
    return entry


def rate_history(roster_id: str) -> dict:
    """What this person or vendor has actually been paid.

    Reports the median as the headline rather than the mean — one emergency
    weekend rate should not move the number a producer quotes from — and says
    plainly when there is only one observation.
    """
    row = get(roster_id)
    if not row:
        raise KeyError("roster entry not found")
    engagements = sorted(row.get("engagements", []),
                         key=lambda e: (not e.get("date"), e.get("date") or ""))
    rates = [e["rate"] for e in engagements]
    undated = sum(1 for e in engagements if not e.get("date"))

    stats: dict[str, Any] = {"engagements": len(engagements), "undated": undated}
    if rates:
        dated = [e for e in engagements if e.get("date")]
        stats.update({
            "last_paid": dated[-1]["rate"] if dated else engagements[-1]["rate"],
            "last_paid_on": dated[-1]["date"] if dated else None,
            "median": round(statistics.median(rates), 2),
            "min": min(rates),
            "max": max(rates),
            "spread_pct": round((max(rates) - min(rates)) / min(rates), 4) if min(rates) else None,
        })
        if len(dated) >= 2:
            first, last = dated[0]["rate"], dated[-1]["rate"]
            stats["change_since_first"] = round((last - first) / first, 4) if first else None
    return {
        "id": row["id"], "name": row["name"], "kind": row.get("kind"),
        "role": row.get("role"), "item_key": row.get("item_key"),
        "currency": engagements[0]["currency"] if engagements else "INR",
        "history": engagements,
        **stats,
        "confidence": ("none" if not engagements else
                       "single observation" if len(engagements) == 1 else
                       "indicative" if len(engagements) < 3 else "solid"),
    }


def search(query: str = "", *, kind: str = "", tag: str = "") -> list[dict]:
    """Name, role, department or tag. Returns the summary a picker needs, not
    the full engagement list."""
    q = norm_name(query)
    out = []
    for row in all_entries():
        if kind and row.get("kind") != kind:
            continue
        if tag and tag not in (row.get("tags") or []):
            continue
        haystack = norm_name(" ".join([row.get("name", ""), row.get("role", ""),
                                       row.get("department", ""), " ".join(row.get("tags") or [])]))
        if q and q not in haystack:
            continue
        engagements = row.get("engagements", [])
        rates = [e["rate"] for e in engagements]
        out.append({
            **{k: row.get(k) for k in ("id", "name", "kind", "role", "department", "city",
                                       "phone", "email", "item_key", "tags")},
            "engagements": len(engagements),
            "median_rate": round(statistics.median(rates), 2) if rates else None,
            "last_production": engagements[-1]["production"] if engagements else "",
        })
    out.sort(key=lambda r: (-r["engagements"], r["name"].lower()))
    return out


# ── the links out ─────────────────────────────────────────────────────────────

def import_crew(crew: list[dict], *, production: str = "", source: str = "callsheet") -> dict:
    """Bring a project's crew list into the roster. Idempotent: running it after
    every job is the intended usage, and the second run updates rather than
    duplicates."""
    added, updated = 0, 0
    for member in crew or []:
        if not (member.get("name") or "").strip():
            continue
        existed = find_existing(member) is not None
        row = upsert({
            "name": member.get("name"), "role": member.get("role") or member.get("department"),
            "department": member.get("department"), "phone": member.get("phone"),
            "email": member.get("email"), "kind": KIND_PERSON,
        })
        updated += 1 if existed else 0
        added += 0 if existed else 1
        rate = member.get("day_rate") or member.get("rate")
        if isinstance(rate, (int, float)) and not isinstance(rate, bool) and rate > 0:
            record_engagement(row["id"], {
                "production": production, "role": row.get("role"), "rate": float(rate),
                "unit": "day", "source": source,
            })
    return {"added": added, "updated": updated}


def ingest_ledger(ledger: dict, *, production: str = "") -> dict:
    """Record what vendors were actually paid on a production, from its variance
    ledger. This is the join that makes the loop worth having: a teardown now
    populates the vendor history as well as the rate card."""
    production = production or ledger.get("production") or ""
    recorded = 0
    for line in ledger.get("lines", []):
        actual = line.get("actual")
        if not actual:
            continue
        for vendor in line.get("vendors") or []:
            if not vendor.strip():
                continue
            row = upsert({"name": vendor, "kind": KIND_VENDOR,
                          "role": line.get("desc", ""), "item_key": line.get("item_key", "")})
            qty = line.get("actual_quantity") or line.get("quantity")
            record_engagement(row["id"], {
                "production": production,
                "role": line.get("desc", ""),
                "rate": round(actual / qty, 2) if qty else float(actual),
                "unit": "day" if qty else "flat",
                "quantity": qty,
                "currency": ledger.get("currency", "INR"),
                "item_key": line.get("item_key", ""),
                "source": "actuals",
                "note": f"from the variance ledger for {production}" if production else "",
            })
            recorded += 1
    return {"vendors_recorded": recorded, "production": production}


def propose_rates(*, region: str = "india", city: str = "", tier: str = "mid",
                  min_engagements: int = 2) -> list[dict]:
    """Roster history → rate-card proposals.

    Uses the median of at least two engagements. One job is a price someone
    quoted once; two or more is a rate.

    An entry with no `item_key` and no role is skipped — the key is what joins a
    rate to a budget line, and there is nothing to derive one from. Where the key
    was derived from a role rather than given, the proposal says so, because a
    derived key can create a rate-card row nobody chose the name of.
    """
    proposals = []
    for row in all_entries():
        key = (row.get("item_key") or "").strip()
        engagements = [e for e in row.get("engagements", []) if e.get("rate")]
        if not key or len(engagements) < min_engagements:
            continue
        rates = [e["rate"] for e in engagements]
        dated = sorted([e for e in engagements if e.get("date")], key=lambda e: e["date"])
        proposals.append({
            "region": region,
            "city": city or row.get("city") or "",
            "tier": tier,
            "item_key": key,
            "desc": row.get("role") or row.get("name"),
            "unit": engagements[-1].get("unit") or "day",
            "rate": round(statistics.median(rates), 2),
            "currency": engagements[-1].get("currency", "INR"),
            "gst_rate": 0,
            "confidence": "green",
            "verified_at": (dated[-1]["date"] if dated else None) or _now()[:10],
            "sample_size": len(engagements),
            "source": (f"roster: {row['name']}, {len(engagements)} engagement(s)"
                       + (f" to {dated[-1]['date']}" if dated else "")),
            # A key derived from a role has not been confirmed by anyone. It is
            # still worth proposing — it is how a crew list reaches the rate
            # card at all — but the reviewer should see which kind it is.
            "item_key_source": row.get("item_key_source") or "given",
            "spread_pct": round((max(rates) - min(rates)) / min(rates), 4) if min(rates) else None,
        })
    proposals.sort(key=lambda p: -p["sample_size"])
    return proposals
