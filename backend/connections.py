"""
connections.py — per-tenant credential broker.

Each tenant connects their own providers (Unipile for messaging, Anthropic for
inference) instead of everyone sharing one set of process env vars. The broker
stores creds encrypted at rest and hands them to server-side callers only — the
client never sees the secret values back. Status/list endpoints return a masked
summary (which providers are connected, non-secret fields), never the tokens.

Encryption: Fernet when `cryptography` is installed AND CONNECTIONS_SECRET is
set (the key is derived from the secret). Without both, creds are stored
unencrypted with `enc:"none"` and a loud startup warning — fine for local dev,
not for production. `status()` exposes `encrypted` so the state is never hidden.

Keys are tenant-scoped (`conn:{tenant}:{provider}`), so one tenant can never
read another's connection. Falls back to an in-memory dict when Redis is absent.
"""

from __future__ import annotations

import os
import json
import base64
import hashlib
from typing import Optional

import tenancy

_redis = None
_mem: dict = {}

# Field names whose values must never be returned to a client.
_SECRET_FIELDS = {"api_key", "secret", "token", "password", "dsn"}

_fernet = None
_fernet_tried = False


def init(redis_client) -> None:
    global _redis
    _redis = redis_client
    # Warm the cipher once so the warning prints at startup, not first use.
    _get_fernet()


def _get_fernet():
    global _fernet, _fernet_tried
    if _fernet_tried:
        return _fernet
    _fernet_tried = True
    secret = os.getenv("CONNECTIONS_SECRET")
    if not secret:
        print("⚠️  CONNECTIONS_SECRET not set — connection creds stored UNENCRYPTED (dev only)")
        return None
    try:
        from cryptography.fernet import Fernet  # type: ignore
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
        _fernet = Fernet(key)
        print("✅ Connection credential encryption enabled (Fernet)")
    except Exception as e:  # pragma: no cover - optional dep
        print(f"⚠️  cryptography not available ({e}) — connection creds stored UNENCRYPTED")
        _fernet = None
    return _fernet


def _key(provider: str) -> str:
    return f"conn:{tenancy.current_tenant()}:{provider}"


def _store(key: str, record: dict) -> None:
    if _redis:
        _redis.set(key, json.dumps(record))
        _redis.sadd(f"conn:{tenancy.current_tenant()}:all", key)
    else:
        _mem[key] = record
        _mem.setdefault(f"conn:{tenancy.current_tenant()}:all", set()).add(key)


def _load(key: str) -> Optional[dict]:
    if _redis:
        raw = _redis.get(key)
        return json.loads(raw) if raw else None
    return _mem.get(key)


def _encrypt(creds: dict) -> tuple[str, str]:
    blob = json.dumps(creds)
    f = _get_fernet()
    if f is not None:
        return f.encrypt(blob.encode()).decode(), "fernet"
    return base64.b64encode(blob.encode()).decode(), "none"


def _decrypt(payload: str, enc: str) -> dict:
    if enc == "fernet":
        f = _get_fernet()
        if f is None:
            raise RuntimeError("connection was Fernet-encrypted but CONNECTIONS_SECRET is now missing")
        return json.loads(f.decrypt(payload.encode()).decode())
    return json.loads(base64.b64decode(payload.encode()).decode())


def _mask(creds: dict) -> dict:
    out = {}
    for k, v in creds.items():
        if k in _SECRET_FIELDS and v:
            out[k] = "•••set"
        else:
            out[k] = v
    return out


# ── public API ─────────────────────────────────────────────────────────────────

def set_connection(provider: str, creds: dict) -> dict:
    """Store (encrypted) creds for a provider under the active tenant. Returns a
    masked summary — never the raw secret."""
    payload, enc = _encrypt(creds)
    record = {"provider": provider, "enc": enc, "payload": payload,
              "fields": sorted(creds.keys())}
    _store(_key(provider), record)
    return status(provider)


def get_connection(provider: str) -> Optional[dict]:
    """Server-side only: return decrypted creds for making requests, or None if
    the tenant hasn't connected this provider. Never expose the result to a
    client."""
    record = _load(_key(provider))
    if not record:
        return None
    try:
        return _decrypt(record["payload"], record.get("enc", "none"))
    except Exception:
        return None


def status(provider: str) -> dict:
    record = _load(_key(provider))
    if not record:
        return {"provider": provider, "connected": False}
    creds = get_connection(provider) or {}
    return {
        "provider": provider,
        "connected": True,
        "encrypted": record.get("enc") == "fernet",
        "fields": _mask(creds),
    }


def list_connections() -> list[dict]:
    tenant = tenancy.current_tenant()
    if _redis:
        keys = _redis.smembers(f"conn:{tenant}:all")
    else:
        keys = _mem.get(f"conn:{tenant}:all", set())
    out = []
    for k in keys:
        prov = k.rsplit(":", 1)[-1]
        out.append(status(prov))
    return out


def delete_connection(provider: str) -> bool:
    key = _key(provider)
    tenant = tenancy.current_tenant()
    if _redis:
        existed = _redis.delete(key)
        _redis.srem(f"conn:{tenant}:all", key)
        return bool(existed)
    if key in _mem:
        del _mem[key]
        _mem.get(f"conn:{tenant}:all", set()).discard(key)
        return True
    return False
