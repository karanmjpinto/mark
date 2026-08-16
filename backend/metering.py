"""
metering.py — per-tenant usage counters + plan quotas.

Counts the expensive actions (budget generations, call-sheet recipients sent,
optionally tokens) per tenant per calendar month, and can enforce a plan quota
before the work runs. Recording is always on (useful analytics even single-
tenant); enforcement is gated behind METERING_ENABLED so turning on multi-tenancy
never silently blocks the demo.

Quotas come from PLAN_QUOTAS (env JSON) or sane defaults. A metric absent from a
plan, or an `enterprise`-style empty map, means unlimited. The default plan for
the public/demo tenant is `pro`, whose limits are high enough to never bite.

Keys are tenant + period scoped (`usage:{tenant}:{YYYYMM}:{metric}`), Redis when
available, in-memory otherwise.
"""

from __future__ import annotations

import os
import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException

import tenancy

_redis = None
_mem: dict = {}

_ENFORCED = os.getenv("METERING_ENABLED", "").strip().lower() in ("1", "true", "yes")

_DEFAULT_QUOTAS = {
    "free": {"budgets": 25, "sends": 100},
    "pro": {"budgets": 1000, "sends": 5000},
    "enterprise": {},  # unlimited
}


def init(redis_client) -> None:
    global _redis
    _redis = redis_client


def _quotas() -> dict:
    raw = os.getenv("PLAN_QUOTAS", "").strip()
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    return _DEFAULT_QUOTAS


def _period() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m")


def _key(metric: str, period: Optional[str] = None) -> str:
    return f"usage:{tenancy.current_tenant()}:{period or _period()}:{metric}"


def _get(metric: str) -> int:
    key = _key(metric)
    if _redis:
        v = _redis.get(key)
        return int(v) if v else 0
    return int(_mem.get(key, 0))


def _limit_for(metric: str) -> Optional[int]:
    plan_quotas = _quotas().get(tenancy.current_plan(), {})
    return plan_quotas.get(metric)  # None → unlimited


def record(metric: str, n: int = 1) -> None:
    """Increment a usage counter. Always runs (analytics), even when enforcement
    is off."""
    key = _key(metric)
    if _redis:
        try:
            _redis.incrby(key, n)
        except Exception:
            pass
    else:
        _mem[key] = int(_mem.get(key, 0)) + n


def check_quota(metric: str, cost: int = 1) -> None:
    """Raise 402 if performing `cost` more of `metric` would exceed the tenant's
    plan quota this period. No-op unless METERING_ENABLED."""
    if not _ENFORCED:
        return
    limit = _limit_for(metric)
    if limit is None:
        return
    if _get(metric) + cost > limit:
        raise HTTPException(
            402,
            f"Plan quota exceeded for '{metric}': {_get(metric)}/{limit} used this month "
            f"on the {tenancy.current_plan()} plan. Upgrade or wait for the next cycle.",
        )


def usage() -> dict:
    plan = tenancy.current_plan()
    plan_quotas = _quotas().get(plan, {})
    metrics = {}
    for m in ("budgets", "sends", "tokens"):
        metrics[m] = {"used": _get(m), "limit": plan_quotas.get(m)}
    return {
        "tenant": tenancy.current_tenant(),
        "plan": plan,
        "period": _period(),
        "enforced": _ENFORCED,
        "metrics": metrics,
    }
