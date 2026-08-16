"""
tenancy.py — multi-tenant identity + data isolation for Mark.

Three modes, chosen by AUTH_MODE (default `off` → single "public" tenant, so the
demo and every existing deploy keep working with zero config):

  off     Single tenant "public". If API_KEY is set, the legacy shared secret is
          still enforced (back-compat). This is today's behaviour.
  apikey  Per-tenant keys. The caller sends X-Tenant-Key (or X-API-Key); it's
          looked up in TENANT_REGISTRY (env JSON) to a {tenant, plan}. Works now,
          no external service.
  jwt     Verifies a bearer JWT against a JWKS (Clerk / Auth0 / any OIDC). Tenant
          comes from a claim (AUTH_TENANT_CLAIM, default org_id → sub). Real
          verification when PyJWT + AUTH_JWKS_URL are present; an honest 501
          otherwise — no fake auth.

Isolation mechanism: the resolved tenant id is put in a ContextVar, and the
backend's db_* helpers prefix every Redis key with `t:{tenant}:`. One dependency
+ one prefix function isolates all per-tenant data (projects, crew, budgets,
feedback, leads, call sheets) without touching each endpoint's key strings.
Intentionally-global data (the determinism cache, ephemeral uuid-keyed jobs and
send proposals) bypasses db_* and stays shared.

Postgres-swap seam: the same db_* layer is the single choke point. Swapping
Redis for a tenant-scoped SQL store means reimplementing db_* + tkey against
rows keyed by (tenant_id, ...), with no endpoint changes.
"""

from __future__ import annotations

import os
import json
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional

from fastapi import Request, HTTPException

AUTH_MODE = os.getenv("AUTH_MODE", "off").strip().lower()  # off | apikey | jwt
_DEFAULT_PLAN = os.getenv("DEFAULT_PLAN", "pro")

_current_tenant: ContextVar[str] = ContextVar("current_tenant", default="public")
_current_plan: ContextVar[str] = ContextVar("current_plan", default=_DEFAULT_PLAN)


@dataclass
class Tenant:
    id: str
    plan: str = _DEFAULT_PLAN


# ── key namespacing ────────────────────────────────────────────────────────────

def tkey(key: str) -> str:
    """Namespace a Redis key by the active tenant. Called by the db_* helpers.
    A key already starting with `g:` is treated as global (prefix-immune)."""
    if key.startswith("g:"):
        return key
    return f"t:{_current_tenant.get()}:{key}"


def current_tenant() -> str:
    return _current_tenant.get()


def current_plan() -> str:
    return _current_plan.get()


def activate(tenant: Tenant) -> None:
    _current_tenant.set(tenant.id)
    _current_plan.set(tenant.plan)


# ── resolution ─────────────────────────────────────────────────────────────────

_registry_cache: Optional[dict] = None


def _registry() -> dict:
    """Parse TENANT_REGISTRY once. Shape: {"<key>": {"tenant": "...", "plan": "..."}}."""
    global _registry_cache
    if _registry_cache is None:
        raw = os.getenv("TENANT_REGISTRY", "").strip()
        try:
            _registry_cache = json.loads(raw) if raw else {}
        except Exception:
            _registry_cache = {}
    return _registry_cache


def _resolve_jwt(request: Request) -> Tenant:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token")
    token = auth[7:]
    try:
        import jwt  # PyJWT
        from jwt import PyJWKClient
    except ImportError:
        raise HTTPException(501, "jwt mode requires PyJWT — pip install 'pyjwt[crypto]'")
    jwks_url = os.getenv("AUTH_JWKS_URL")
    if not jwks_url:
        raise HTTPException(501, "jwt mode requires AUTH_JWKS_URL")
    try:
        signing_key = PyJWKClient(jwks_url).get_signing_key_from_jwt(token)
        audience = os.getenv("AUTH_AUDIENCE")
        claims = jwt.decode(
            token, signing_key.key, algorithms=["RS256"],
            audience=audience, options={"verify_aud": bool(audience)},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(401, f"Token verification failed: {type(e).__name__}")
    claim_name = os.getenv("AUTH_TENANT_CLAIM", "org_id")
    tenant_id = claims.get(claim_name) or claims.get("sub")
    if not tenant_id:
        raise HTTPException(401, "No tenant claim in token")
    return Tenant(id=str(tenant_id), plan=str(claims.get("plan", "free")))


def resolve(request: Request) -> Tenant:
    """Resolve (and authenticate) the tenant for a request. Raises HTTPException
    on any auth failure."""
    if AUTH_MODE == "off":
        server_key = os.getenv("API_KEY")
        if server_key and request.headers.get("X-API-Key") != server_key:
            raise HTTPException(401, "Invalid or missing API key")
        return Tenant(id="public", plan=_DEFAULT_PLAN)
    if AUTH_MODE == "apikey":
        key = request.headers.get("X-Tenant-Key") or request.headers.get("X-API-Key") or ""
        ent = _registry().get(key)
        if not ent:
            raise HTTPException(401, "Invalid or missing tenant key")
        return Tenant(id=str(ent.get("tenant")), plan=str(ent.get("plan", "free")))
    if AUTH_MODE == "jwt":
        return _resolve_jwt(request)
    raise HTTPException(500, f"Unknown AUTH_MODE: {AUTH_MODE}")


async def require_tenant(request: Request) -> Tenant:
    """FastAPI dependency: resolve, authenticate, activate the tenant context.
    Returns the Tenant so metered endpoints can read the plan."""
    tenant = resolve(request)
    activate(tenant)
    return tenant
