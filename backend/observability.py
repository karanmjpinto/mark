"""
observability.py — lightweight agent-run tracing for Mark.

Every agent call (Flue budget/refine/callsheet, the /claude proxy) is wrapped in
a span that records: name, model, latency, token usage (when the provider
surfaces it), cache_hit / retry_fired flags, a truncated view of inputs, and any
error. This is the single thing that lets you answer "why did this budget differ
from that one" without guessing — today the input-hash cache hides that symptom
instead of exposing the cause.

Sinks, in order of preference (all best-effort, never raise into the request):
  1. Langfuse   — if LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY are set (lazy import).
  2. Redis ring — a capped list `mark:traces` (newest first), readable via /admin/traces.
  3. stdout     — one compact JSON line per span, so Railway logs always have it.

Matches Mark's existing philosophy: works with zero configuration (stdout +
in-memory), gets better when you wire the env vars.
"""

from __future__ import annotations

import os
import json
import time
import uuid
import collections
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

_redis = None
_mem_ring: collections.deque = collections.deque(maxlen=500)
_RING_KEY = "mark:traces"
_RING_MAX = int(os.getenv("TRACE_RING_MAX", "500"))

# Langfuse client is created once, lazily, only if keys are present.
_langfuse = None
_langfuse_tried = False


def init(redis_client) -> None:
    """Wire the shared Redis client in from main. Safe to call with None."""
    global _redis
    _redis = redis_client


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_langfuse():
    global _langfuse, _langfuse_tried
    if _langfuse_tried:
        return _langfuse
    _langfuse_tried = True
    if not (os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")):
        return None
    try:
        from langfuse import Langfuse  # type: ignore
        _langfuse = Langfuse()  # reads keys + host from env
        print("✅ Langfuse tracing enabled")
    except Exception as e:  # pragma: no cover - optional dep
        print(f"⚠️  Langfuse not available: {e} — falling back to Redis/stdout traces")
        _langfuse = None
    return _langfuse


def _truncate(value: Any, limit: int = 2000) -> Any:
    """Keep traces small and cheap. Big blobs (script text, budget JSON) are
    summarised to a length marker rather than stored whole."""
    try:
        s = value if isinstance(value, str) else json.dumps(value, default=str)
    except Exception:
        s = str(value)
    if len(s) <= limit:
        return value
    return {"_truncated": True, "chars": len(s), "preview": s[:limit]}


def _flush(span: dict) -> None:
    """Persist one finished span to every available sink. Never raises."""
    # 1. stdout — always, so logs are self-sufficient.
    try:
        print("🛰️  trace " + json.dumps(span, default=str, separators=(",", ":")))
    except Exception:
        pass

    # 2. Redis ring buffer (newest first, capped).
    try:
        if _redis is not None:
            _redis.lpush(_RING_KEY, json.dumps(span, default=str))
            _redis.ltrim(_RING_KEY, 0, _RING_MAX - 1)
        else:
            _mem_ring.appendleft(span)
    except Exception:
        _mem_ring.appendleft(span)

    # 3. Langfuse (optional, structured LLM trace).
    lf = _get_langfuse()
    if lf is not None:
        try:
            usage = span.get("usage") or {}
            lf.trace(
                id=span.get("id"),
                name=span.get("name"),
                input=span.get("input"),
                output=span.get("output"),
                metadata={
                    k: span.get(k)
                    for k in ("model", "latency_ms", "cache_hit", "retry_fired", "ok", "error", "run_id")
                    if span.get(k) is not None
                },
                usage={
                    "input": usage.get("input_tokens"),
                    "output": usage.get("output_tokens"),
                } if usage else None,
            )
        except Exception:
            pass


@asynccontextmanager
async def trace(name: str, *, run_id: Optional[str] = None, model: Optional[str] = None, **fields):
    """
    Async span. Usage:

        async with observability.trace("flue:generate-budget", run_id=rid) as span:
            result = await do_work()
            span["output"] = summarise(result)
            span["cache_hit"] = False

    Timing, ok/error, and flushing are handled automatically. Mutate `span`
    to attach output, usage, cache_hit, retry_fired, etc.
    """
    span: dict = {
        "id": uuid.uuid4().hex,
        "name": name,
        "run_id": run_id,
        "model": model,
        "ts": _now_iso(),
        "ok": True,
    }
    for k, v in fields.items():
        span[k] = _truncate(v) if k in ("input", "output") else v
    start = time.monotonic()
    try:
        yield span
    except Exception as e:
        span["ok"] = False
        span["error"] = f"{type(e).__name__}: {e}"
        raise
    finally:
        span["latency_ms"] = round((time.monotonic() - start) * 1000, 1)
        # Truncate output if the caller set it after entering.
        if "output" in span:
            span["output"] = _truncate(span["output"])
        if "input" in span:
            span["input"] = _truncate(span["input"])
        _flush(span)


def recent(limit: int = 50) -> list[dict]:
    """Read back the most recent spans (newest first)."""
    limit = max(1, min(limit, _RING_MAX))
    if _redis is not None:
        try:
            raw = _redis.lrange(_RING_KEY, 0, limit - 1)
            return [json.loads(x) for x in raw]
        except Exception:
            pass
    return list(_mem_ring)[:limit]
