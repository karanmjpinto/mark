"""
AskMark Backend — Complete FastAPI Server
Run with: uvicorn main:app --reload --port 8000
"""

from fastapi import FastAPI, HTTPException, Request, Depends, UploadFile, File
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from typing import Optional
import asyncio
import uuid
import json
import os
import io
import base64
import redis as redis_lib
from datetime import datetime, timezone
import httpx
import time
import hashlib
import hmac
import collections
from dotenv import load_dotenv

import observability
import tenancy
import connections
import metering
import ratecard
import schedule as scheduling
import variance
import compliance
import screenplay
import budgetdiff
import exporters
import teardown_report
import delivery
import roster

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'), override=True)

app = FastAPI(title="AskMark API", version="1.0.0")

# ── CORS ──────────────────────────────────────────────────────────────────────
# allow_origins=["*"] and allow_credentials=True cannot coexist (CORS spec).
# Set ALLOWED_ORIGINS env var (comma-separated) to enable credentials for specific origins.
_raw_origins = os.getenv("ALLOWED_ORIGINS", "")
_allowed_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()] or ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials="*" not in _allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── REDIS ─────────────────────────────────────────────────────────────────────
# Prefer REDIS_URL when set (Railway / Heroku-style), fall back to discrete
# host/port/password env vars. Without any of these we use an in-memory dict
# (dev only — caching is disabled in that mode).
try:
    _redis_url = os.getenv("REDIS_URL")
    if _redis_url:
        r = redis_lib.Redis.from_url(_redis_url, decode_responses=True)
    else:
        r = redis_lib.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            password=os.getenv("REDIS_PASSWORD") or None,
            db=0,
            decode_responses=True,
        )
    r.ping()
    print("✅ Redis connected")
except Exception as e:
    print(f"⚠️  Redis not available: {e} — using in-memory fallback")
    r = None

# Wire the shared Redis client into the tracing layer so agent-run spans land in
# the `mark:traces` ring buffer (and Langfuse, if configured).
observability.init(r)

_mem = {}

# ── TENANT-SCOPED STORAGE ─────────────────────────────────────────────────────
# Every db_* key is namespaced by the active tenant (tenancy.tkey). With
# AUTH_MODE=off the tenant is "public" and behaviour is identical to before.
# Turning on apikey/jwt gives each tenant an isolated keyspace with no endpoint
# changes. Intentionally-global data uses the _raw_* helpers below instead.

def db_set(key, value):
    key = tenancy.tkey(key)
    if r:
        r.set(key, json.dumps(value))
    else:
        _mem[key] = value

def db_get(key):
    key = tenancy.tkey(key)
    if r:
        raw = r.get(key)
        return json.loads(raw) if raw else None
    return _mem.get(key)

def db_delete(key):
    key = tenancy.tkey(key)
    if r: r.delete(key)
    elif key in _mem: del _mem[key]

def db_sadd(key, val):
    key = tenancy.tkey(key)
    if r: r.sadd(key, val)
    else: _mem.setdefault(key, set()).add(val)

def db_srem(key, val):
    key = tenancy.tkey(key)
    if r: r.srem(key, val)
    elif key in _mem: _mem[key].discard(val)

def db_smembers(key):
    key = tenancy.tkey(key)
    if r: return r.smembers(key)
    return _mem.get(key, set())

# Global (tenant-immune) store — for ephemeral uuid-keyed jobs and send
# proposals, and anything intentionally cross-tenant. No tkey prefixing.
def _raw_set(key, value, ttl=None):
    if r:
        if ttl: r.setex(key, ttl, json.dumps(value))
        else: r.set(key, json.dumps(value))
    else:
        _mem[key] = value

def _raw_get(key):
    if r:
        raw = r.get(key)
        return json.loads(raw) if raw else None
    return _mem.get(key)

# Wire the shared clients into the tenant-aware helper modules.
connections.init(r)
metering.init(r)
ratecard.init(r)
delivery.init(r)
roster.init(r)

def now():
    return datetime.now(timezone.utc).isoformat()

# ── AUTH / TENANCY ────────────────────────────────────────────────────────────
# require_api_key now resolves and activates the tenant for the request (see
# tenancy.py). AUTH_MODE=off keeps the legacy single-tenant shared-secret
# behaviour; apikey/jwt turn on real per-tenant isolation. All ~40 existing
# `Depends(require_api_key)` call sites get tenancy for free — the returned value
# is now the Tenant, and tenancy.current_tenant()/current_plan() read the context.
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

async def require_api_key(request: Request, key: Optional[str] = Depends(_api_key_header)):
    return await tenancy.require_tenant(request)

# ── RATE LIMITING ─────────────────────────────────────────────────────────────
# Limits /claude calls to CLAUDE_RATE_LIMIT requests per minute per IP (default 10).
_claude_calls: dict = collections.defaultdict(list)
_RATE_LIMIT = int(os.getenv("CLAUDE_RATE_LIMIT", "10"))
_RATE_WINDOW = 60  # seconds

def check_rate_limit(ip: str):
    now_ts = time.monotonic()
    window_start = now_ts - _RATE_WINDOW
    _claude_calls[ip] = [t for t in _claude_calls[ip] if t > window_start]
    if len(_claude_calls[ip]) >= _RATE_LIMIT:
        raise HTTPException(429, f"Rate limit: max {_RATE_LIMIT} Claude requests per minute")
    _claude_calls[ip].append(now_ts)

# ── MODELS ────────────────────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    name: str
    project_type: Optional[str] = "tvc"
    client_name: Optional[str] = ""
    director_name: Optional[str] = ""
    producer_name: Optional[str] = ""
    total_budget: Optional[float] = 0
    currency: Optional[str] = "INR"
    shoot_start_date: Optional[str] = ""
    shoot_end_date: Optional[str] = ""
    delivery_date: Optional[str] = ""
    status: Optional[str] = "pre-production"
    brief: Optional[str] = ""

class ProjectUpdate(BaseModel):
    project_id: str
    name: Optional[str] = None
    project_type: Optional[str] = None
    client_name: Optional[str] = None
    director_name: Optional[str] = None
    producer_name: Optional[str] = None
    total_budget: Optional[float] = None
    currency: Optional[str] = None
    shoot_start_date: Optional[str] = None
    shoot_end_date: Optional[str] = None
    delivery_date: Optional[str] = None
    status: Optional[str] = None
    brief: Optional[str] = None

class ProjectIdRequest(BaseModel):
    project_id: str

class CrewCreate(BaseModel):
    project_id: str
    name: str
    email: Optional[str] = ""
    phone: Optional[str] = ""
    role_title: Optional[str] = ""
    department: Optional[str] = "Other"
    day_rate: Optional[float] = 0
    rate_currency: Optional[str] = "INR"
    dietary_requirements: Optional[str] = ""
    emergency_contact: Optional[str] = ""
    notes: Optional[str] = ""

class CrewUpdate(BaseModel):
    crew_id: str
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    role_title: Optional[str] = None
    department: Optional[str] = None
    day_rate: Optional[float] = None
    rate_currency: Optional[str] = None
    dietary_requirements: Optional[str] = None
    emergency_contact: Optional[str] = None
    notes: Optional[str] = None

class CrewIdRequest(BaseModel):
    crew_id: str

class BudgetSave(BaseModel):
    project_id: str
    budget_data: dict
    version: Optional[str] = "1.0"

class QAPair(BaseModel):
    id: str
    question: str
    answer: str
    options: Optional[list] = None

class BudgetGenerate(BaseModel):
    # Either project_id (project-based flow) OR script + region + currency (standalone flow).
    project_id: Optional[str] = None
    script: Optional[str] = ""
    region: Optional[str] = "india"
    currency: Optional[dict] = None
    qa: Optional[list] = None
    answers: Optional[dict] = None  # legacy flat map; auto-converted if `qa` is missing
    breakdown: Optional[dict] = None  # output of /script/parse summary, when available
    model: Optional[str] = None  # optional model override (e.g. async path requests Sonnet)
    version: Optional[str] = "1.0"
    # Rate-library resolution. `city` is the shoot city — supplying it stops
    # another city's rates being substituted (see ratecard._city_rank).
    city: Optional[str] = ""
    tier: Optional[str] = "mid"
    use_rates: Optional[bool] = True

class CallSheetRefine(BaseModel):
    callsheet: dict
    instruction: str

class CallSheetSave(BaseModel):
    callsheet: dict
    project_id: Optional[str] = None

class CallSheetRenderTemplate(BaseModel):
    # Render the call sheet in the producer's own uploaded template format.
    callsheet: dict
    template_text: str

class CallSheetSend(BaseModel):
    callsheet: dict
    channels: list  # ["email", "whatsapp"]
    project_id: Optional[str] = None
    # Optional rendered call sheet PDF (base64, no data-URI prefix) generated
    # client-side from the exact preview. When present it is attached to the
    # email so crew get the full document, not just the HTML summary.
    pdf_base64: Optional[str] = None
    pdf_filename: Optional[str] = None

class BudgetRefine(BaseModel):
    # Standalone flow: pass the budget JSON directly. Project flow: pass project_id.
    project_id: Optional[str] = None
    budget: Optional[dict] = None              # current budget JSON (the .budget_data shape)
    instruction: str                            # producer's free-text refinement ask
    region: Optional[str] = None
    currency: Optional[dict] = None
    version: Optional[str] = "1.0"

class CrewEnrich(BaseModel):
    crew_id: str

class FeedbackCreate(BaseModel):
    context_type: str  # "budget" | "question" | "crew" | "general"
    rating: str        # "up" | "down" | "1".."5" — caller decides; we store as a string
    comment: Optional[str] = ""
    context_id: Optional[str] = None       # budget_id / project_id / crew_id, when applicable
    snapshot: Optional[dict] = None        # the artifact the user is rating (e.g. budget JSON)
    user_email: Optional[str] = None
    user_agent: Optional[str] = None
    page: Optional[str] = None

class LeadCreate(BaseModel):
    """Soft email gate — captured the first time a producer exports or refines.

    Not auth: the frontend remembers the unlock in localStorage and stops
    showing the modal. The intent is to know who's actually using the demo
    so we can follow up, not to enforce access."""
    email: str
    role: Optional[str] = None             # "Producer" | "Director" | "DOP" | "Other" — free text
    company: Optional[str] = None
    page: Optional[str] = None
    user_agent: Optional[str] = None
    trigger: Optional[str] = None          # which action prompted the gate ("export", "refine", etc.)

# ── HELPERS ───────────────────────────────────────────────────────────────────

def _apply_partial_update(existing: dict, data: BaseModel, exclude_keys: set) -> dict:
    """Only update fields that were explicitly provided in the request body."""
    # model_fields_set (Pydantic v2) or __fields_set__ (Pydantic v1)
    fields_set = getattr(data, "model_fields_set", None) or getattr(data, "__fields_set__", set())
    for field in fields_set:
        if field not in exclude_keys:
            existing[field] = getattr(data, field)
    return existing

# ── HEALTH ────────────────────────────────────────────────────────────────────

@app.post("/")
def health():
    return {"status": "Mark is live", "version": "1.0.0"}

@app.get("/health")
def health_get():
    return {"status": "Mark is live", "version": "1.0.0"}

# ── PROJECTS ──────────────────────────────────────────────────────────────────

@app.post("/projects/create")
def create_project(data: ProjectCreate, _=Depends(require_api_key)):
    pid = str(uuid.uuid4())
    project = {
        "id": pid, "name": data.name, "project_type": data.project_type,
        "client_name": data.client_name, "director_name": data.director_name,
        "producer_name": data.producer_name, "total_budget": data.total_budget,
        "currency": data.currency, "shoot_start_date": data.shoot_start_date,
        "shoot_end_date": data.shoot_end_date, "delivery_date": data.delivery_date,
        "status": data.status, "brief": data.brief,
        "created_at": now(), "updated_at": now()
    }
    db_set(f"project:{pid}", project)
    db_sadd("projects:all", pid)
    return {"success": True, "project": project}

@app.post("/projects/list")
def list_projects(_=Depends(require_api_key)):
    ids = db_smembers("projects:all")
    projects = []
    for pid in ids:
        p = db_get(f"project:{pid}")
        if p:
            p["crew_total"] = len(db_smembers(f"project:{pid}:crew"))
            projects.append(p)
    return {"success": True, "projects": sorted(projects, key=lambda x: x.get("created_at", ""), reverse=True)}

@app.post("/projects/get")
def get_project(data: ProjectIdRequest, _=Depends(require_api_key)):
    p = db_get(f"project:{data.project_id}")
    if not p: raise HTTPException(404, "Project not found")
    return {"success": True, "project": p}

@app.post("/projects/update")
def update_project(data: ProjectUpdate, _=Depends(require_api_key)):
    p = db_get(f"project:{data.project_id}")
    if not p: raise HTTPException(404, "Project not found")
    _apply_partial_update(p, data, exclude_keys={"project_id"})
    p["updated_at"] = now()
    db_set(f"project:{data.project_id}", p)
    return {"success": True, "project": p}

@app.post("/projects/delete")
def delete_project(data: ProjectIdRequest, _=Depends(require_api_key)):
    p = db_get(f"project:{data.project_id}")
    if not p: raise HTTPException(404, "Project not found")
    for cid in db_smembers(f"project:{data.project_id}:crew"):
        db_delete(f"crew:{cid}")
    db_delete(f"project:{data.project_id}:crew")
    for bid in db_smembers(f"project:{data.project_id}:budgets"):
        db_delete(f"budget:{bid}")
    db_delete(f"project:{data.project_id}:budgets")
    db_delete(f"budget:{data.project_id}:latest")
    db_delete(f"project:{data.project_id}")
    db_srem("projects:all", data.project_id)
    return {"success": True, "message": "Deleted"}

@app.post("/projects/dashboard")
def dashboard(data: ProjectIdRequest, _=Depends(require_api_key)):
    p = db_get(f"project:{data.project_id}")
    if not p: raise HTTPException(404, "Project not found")
    crew = [db_get(f"crew:{cid}") for cid in db_smembers(f"project:{data.project_id}:crew")]
    crew = [c for c in crew if c]
    dept_breakdown = {}
    total_day_rates = 0
    for c in crew:
        dept = c.get("department", "Other")
        dept_breakdown[dept] = dept_breakdown.get(dept, 0) + 1
        total_day_rates += c.get("day_rate", 0)
    # Use tracked spend from saved budget if available; day_rate alone isn't total spend
    budget = db_get(f"budget:{data.project_id}:latest")
    budget_spend = 0
    if budget and isinstance(budget.get("budget_data"), dict):
        budget_spend = budget["budget_data"].get("total_spend", 0) or 0
    return {
        "success": True,
        "dashboard": {
            "project": p,
            "stats": {
                "crew_total": len(crew),
                "total_day_rates": total_day_rates,
                "budget_spend": budget_spend,
                "budget_remaining": (p.get("total_budget") or 0) - budget_spend,
            },
            "department_breakdown": dept_breakdown,
        }
    }

# ── CREW ──────────────────────────────────────────────────────────────────────

@app.post("/crew/create")
def create_crew(data: CrewCreate, _=Depends(require_api_key)):
    p = db_get(f"project:{data.project_id}")
    if not p: raise HTTPException(404, "Project not found")
    cid = str(uuid.uuid4())
    member = {
        "id": cid, "project_id": data.project_id, "name": data.name,
        "email": data.email, "phone": data.phone, "role_title": data.role_title,
        "department": data.department, "day_rate": data.day_rate,
        "rate_currency": data.rate_currency,
        "dietary_requirements": data.dietary_requirements,
        "emergency_contact": data.emergency_contact, "notes": data.notes,
        "created_at": now(), "updated_at": now()
    }
    db_set(f"crew:{cid}", member)
    db_sadd(f"project:{data.project_id}:crew", cid)
    return {"success": True, "crew_member": member}

@app.post("/crew/list")
def list_crew(data: ProjectIdRequest, _=Depends(require_api_key)):
    ids = db_smembers(f"project:{data.project_id}:crew")
    crew = [db_get(f"crew:{cid}") for cid in ids]
    crew = [c for c in crew if c]
    return {"success": True, "crew": sorted(crew, key=lambda x: x.get("created_at", "")), "total": len(crew)}

@app.post("/crew/get")
def get_crew(data: CrewIdRequest, _=Depends(require_api_key)):
    c = db_get(f"crew:{data.crew_id}")
    if not c: raise HTTPException(404, "Crew member not found")
    return {"success": True, "crew_member": c}

@app.post("/crew/update")
def update_crew(data: CrewUpdate, _=Depends(require_api_key)):
    c = db_get(f"crew:{data.crew_id}")
    if not c: raise HTTPException(404, "Crew member not found")
    _apply_partial_update(c, data, exclude_keys={"crew_id"})
    c["updated_at"] = now()
    db_set(f"crew:{data.crew_id}", c)
    return {"success": True, "crew_member": c}

@app.post("/crew/delete")
def delete_crew(data: CrewIdRequest, _=Depends(require_api_key)):
    c = db_get(f"crew:{data.crew_id}")
    if not c: raise HTTPException(404, "Crew member not found")
    db_srem(f"project:{c['project_id']}:crew", data.crew_id)
    db_delete(f"crew:{data.crew_id}")
    return {"success": True, "message": "Deleted"}

# ── BUDGET STORAGE ────────────────────────────────────────────────────────────

@app.post("/admin/cache/clear")
async def admin_cache_clear(_=Depends(require_api_key)):
    """Drop the budget cache. Call after rate-card or prompt changes that
    invalidate previously-cached results. Requires API_KEY when set."""
    deleted = _budget_cache_clear()
    return {"success": True, "deleted_keys": deleted}

@app.post("/admin/traces")
async def admin_traces(data: dict = None, _=Depends(require_api_key)):
    """Read back recent agent-run traces (newest first). Each span carries
    name, model, latency_ms, token usage (Claude path), cache_hit, ok/error and
    a truncated view of inputs/outputs. Requires API_KEY when set."""
    limit = int((data or {}).get("limit", 50))
    spans = observability.recent(limit)
    return {"success": True, "traces": spans, "total": len(spans)}

# ── CONNECTIONS (per-tenant credential broker) ────────────────────────────────
# A tenant connects their own providers (Unipile, Anthropic) instead of sharing
# the process env. Creds are encrypted at rest and never returned to the client —
# status/list return a masked summary only.

class ConnectionSet(BaseModel):
    provider: str            # "unipile" | "anthropic" | ...
    creds: dict              # e.g. {"dsn": "...", "api_key": "..."} or {"api_key": "..."}

class ConnectionRef(BaseModel):
    provider: str

@app.post("/connections/set")
async def connections_set(data: ConnectionSet, _=Depends(require_api_key)):
    if not data.provider or not isinstance(data.creds, dict) or not data.creds:
        raise HTTPException(400, "provider and non-empty creds are required")
    return {"success": True, "connection": connections.set_connection(data.provider, data.creds)}

@app.post("/connections/list")
async def connections_list(_=Depends(require_api_key)):
    return {"success": True, "connections": connections.list_connections()}

@app.post("/connections/status")
async def connections_status(data: ConnectionRef, _=Depends(require_api_key)):
    return {"success": True, "connection": connections.status(data.provider)}

@app.post("/connections/delete")
async def connections_delete(data: ConnectionRef, _=Depends(require_api_key)):
    return {"success": True, "deleted": connections.delete_connection(data.provider)}

# ── USAGE (per-tenant metering) ───────────────────────────────────────────────

@app.post("/usage")
async def usage(_=Depends(require_api_key)):
    """Return the active tenant's usage this period, plan limits, and whether
    enforcement is on."""
    return {"success": True, "usage": metering.usage()}

# ── RATE LIBRARY ──────────────────────────────────────────────────────────────
# The per-tenant rate card. See ratecard.py for why this exists: rate knowledge
# that lives in a prompt cannot be corrected, versioned or compounded, and it is
# the one asset every engagement is supposed to accumulate.

class RateUpsert(BaseModel):
    rate: dict

class RateQuery(BaseModel):
    region: Optional[str] = None
    city: Optional[str] = ""
    tier: Optional[str] = "mid"
    currency: Optional[str] = None

class RateId(BaseModel):
    id: str

class RateSeed(BaseModel):
    region: str
    overwrite: Optional[bool] = False

@app.post("/rates/list")
def rates_list(data: RateQuery = None, _=Depends(require_api_key)):
    data = data or RateQuery()
    rows = ratecard.all_rates()
    if data.region:
        rows = [r for r in rows if r.get("region") == data.region.strip().lower()]
    if data.city:
        rows = [r for r in rows if (r.get("city") or "") == data.city.strip().lower()]
    rows.sort(key=lambda r: (r.get("section") or "", r.get("item_key") or ""))
    return {"success": True, "rates": rows, "count": len(rows),
            "verified": sum(1 for r in rows if ratecard.is_verified(r))}

@app.post("/rates/upsert")
def rates_upsert(data: RateUpsert, _=Depends(require_api_key)):
    try:
        row = ratecard.upsert(data.rate)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"success": True, "rate": row}

@app.post("/rates/delete")
def rates_delete(data: RateId, _=Depends(require_api_key)):
    return {"success": True, "deleted": ratecard.delete_rate(data.id)}

@app.post("/rates/seed")
def rates_seed(data: RateSeed, _=Depends(require_api_key)):
    """Load the market-reference seed for a region. Non-destructive by default —
    a tenant that has corrected its rates cannot be stamped on."""
    try:
        result = ratecard.seed(data.region, overwrite=bool(data.overwrite))
    except FileNotFoundError as e:
        raise HTTPException(404, f"{e}. Available: {ratecard.available_seeds()}")
    return {"success": True, **result}

@app.post("/rates/pack")
def rates_pack(data: RateQuery, _=Depends(require_api_key)):
    """The compact pack handed to the budget agent, plus the verified-coverage
    number worth quoting to a client."""
    region = (data.region or "india")
    pack = ratecard.resolve_pack(region, city=data.city or "", tier=data.tier or "mid",
                                 currency=data.currency)
    return {"success": True, "pack": pack,
            "coverage": ratecard.coverage(region, city=data.city or "", tier=data.tier or "mid")}

# ── SCHEDULE ──────────────────────────────────────────────────────────────────

class ScheduleGenerate(BaseModel):
    project_id: Optional[str] = None
    scenes: Optional[list] = None          # per-scene list from /script/parse
    breakdown: Optional[dict] = None       # falls back to the aggregate summary
    shoot_days: Optional[int] = None       # binding when supplied
    eighths_per_day: Optional[int] = None
    start_date: Optional[str] = None
    title: Optional[str] = ""
    save: Optional[bool] = True

class ScheduleRef(BaseModel):
    project_id: str

class CallsheetFromSchedule(BaseModel):
    project_id: Optional[str] = None
    schedule: Optional[dict] = None
    day: int

def _scenes_for(data: "ScheduleGenerate") -> list:
    if data.scenes:
        return data.scenes
    bd = data.breakdown or {}
    if bd.get("scenes"):
        return bd["scenes"]
    if bd:
        return scheduling.scenes_from_summary(bd)
    return []

@app.post("/schedule/generate")
def schedule_generate(data: ScheduleGenerate, _=Depends(require_api_key)):
    scenes = _scenes_for(data)
    if not scenes:
        raise HTTPException(422, "No scenes supplied. Pass `scenes` from /script/parse, "
                                 "or `breakdown` to synthesise an indicative shape.")
    sched = scheduling.build_schedule(
        scenes,
        shoot_days=data.shoot_days,
        eighths_per_day=data.eighths_per_day or scheduling.DEFAULT_EIGHTHS_PER_DAY,
        start_date=data.start_date,
        title=data.title or "",
    )
    if data.project_id and data.save:
        sched["project_id"] = data.project_id
        sched["updated_at"] = now()
        db_set(f"schedule:{data.project_id}", sched)
    return {"success": True, "schedule": sched}

class ScheduleSave(BaseModel):
    project_id: Optional[str] = None
    days: list                      # [[scene, ...], ...] — the producer's assignment
    start_date: Optional[str] = None
    title: Optional[str] = ""

@app.post("/schedule/save")
def schedule_save(data: ScheduleSave, _=Depends(require_api_key)):
    """Store a hand-edited schedule. The day→scene assignment is the producer's
    and is honoured exactly; every derived figure is recomputed server-side so
    the stored schedule and the numbers on screen cannot drift apart."""
    sched = scheduling.rebuild(data.days, start_date=data.start_date, title=data.title or "")
    if data.project_id:
        sched["project_id"] = data.project_id
        sched["updated_at"] = now()
        db_set(f"schedule:{data.project_id}", sched)
    return {"success": True, "schedule": sched}

@app.post("/schedule/get")
def schedule_get(data: ScheduleRef, _=Depends(require_api_key)):
    sched = db_get(f"schedule:{data.project_id}")
    if not sched:
        raise HTTPException(404, "No schedule saved for this project")
    return {"success": True, "schedule": sched}

@app.post("/schedule/reconcile")
def schedule_reconcile(data: ScheduleRef, _=Depends(require_api_key)):
    """Does the budget pay for the number of days the script implies? The check
    that was impossible before the schedule existed."""
    sched = db_get(f"schedule:{data.project_id}")
    if not sched:
        raise HTTPException(404, "No schedule saved for this project")
    budget = db_get(f"budget:{data.project_id}:latest")
    if not budget:
        raise HTTPException(404, "No budget saved for this project")
    return {"success": True,
            "reconciliation": scheduling.reconcile_with_budget(sched, budget.get("budget_data") or {})}

@app.post("/callsheet/from-schedule")
def callsheet_from_schedule(data: CallsheetFromSchedule, _=Depends(require_api_key)):
    """Seed a call sheet from one shooting day. Partial by design — call times,
    weather and hospital are left for the producer and the call-sheet agent
    rather than invented."""
    sched = data.schedule
    project = None
    crew: list = []
    if not sched and data.project_id:
        sched = db_get(f"schedule:{data.project_id}")
        project = db_get(f"project:{data.project_id}")
        crew = [c for c in (db_get(f"crew:{cid}") for cid in db_smembers(f"project:{data.project_id}:crew")) if c]
    if not sched:
        raise HTTPException(404, "No schedule supplied or saved for this project")
    try:
        cs = scheduling.callsheet_seed(sched, data.day, project=project, crew=crew)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"success": True, "callsheet": cs}

# ── VARIANCE / TEARDOWN ───────────────────────────────────────────────────────
# The Stage 0 teardown as a product feature rather than five days of spreadsheet
# work. See variance.py.

class VarianceCompute(BaseModel):
    project_id: Optional[str] = None
    budget: Optional[dict] = None          # budget_data shape
    actuals: Optional[list] = None         # rows of {code?, desc, amount, qty?, vendor?}
    actuals_csv: Optional[str] = None      # or a pasted CSV/TSV cost report
    threshold: Optional[float] = None
    currency: Optional[str] = "INR"
    production: Optional[str] = ""
    overrides: Optional[dict] = None       # line code → classification, from the interview
    save: Optional[bool] = True

class TeardownCompute(BaseModel):
    project_id: Optional[str] = None
    ledger_ids: Optional[list] = None
    ledgers: Optional[list] = None
    productions_per_year: int = 12

def _resolve_budget(project_id: Optional[str], budget: Optional[dict]) -> dict:
    if budget:
        return budget
    if project_id:
        stored = db_get(f"budget:{project_id}:latest")
        if stored:
            return stored.get("budget_data") or {}
    raise HTTPException(422, "Pass `budget`, or a `project_id` that has a saved budget")

@app.post("/variance/compute")
def variance_compute(data: VarianceCompute, _=Depends(require_api_key)):
    budget = _resolve_budget(data.project_id, data.budget)
    actuals = data.actuals or []
    if not actuals and data.actuals_csv:
        actuals = variance.parse_actuals_csv(data.actuals_csv)
    if not actuals:
        raise HTTPException(422, "No actuals supplied. Pass `actuals` rows, `actuals_csv`, "
                                 "or upload a cost report to /actuals/parse first.")
    ledger = variance.build_ledger(
        budget, actuals,
        threshold=data.threshold if data.threshold is not None else variance.DEFAULT_THRESHOLD,
        currency=data.currency or "INR",
        production=data.production or "",
        overrides=data.overrides or {},
    )
    lid = str(uuid.uuid4())
    ledger["id"] = lid
    ledger["created_at"] = now()
    if data.project_id and data.save:
        ledger["project_id"] = data.project_id
        db_set(f"ledger:{lid}", ledger)
        db_sadd(f"project:{data.project_id}:ledgers", lid)
    return {"success": True, "ledger": ledger}

@app.post("/actuals/parse")
async def actuals_parse(file: UploadFile = File(...), _=Depends(require_api_key)):
    """Upload a cost report (.csv/.tsv/.xlsx) and get normalised actual rows back.

    Deliberately separate from /variance/compute: a producer should see what was
    read out of their file — and what was dropped as a subtotal — before any
    finding is computed from it."""
    name = (file.filename or "").lower()
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Empty file")
    if len(raw) > _MAX_TEMPLATE_BYTES:
        raise HTTPException(413, f"File too large (max {_MAX_TEMPLATE_BYTES // 1024 // 1024} MB)")
    try:
        if name.endswith(".xlsx"):
            rows = await run_in_threadpool(variance.parse_actuals_xlsx, raw)
        elif name.endswith((".csv", ".tsv", ".txt")):
            rows = variance.parse_actuals_csv(raw.decode("utf-8", "ignore"))
        else:
            raise HTTPException(400, "Supported: .csv, .tsv, .xlsx")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(422, f"Could not read the cost report: {type(e).__name__}: {e}")
    if not rows:
        raise HTTPException(422, "No cost lines found. The sheet needs a header row with an "
                                 "amount column and a description or code column.")
    return {"success": True, "rows": rows, "count": len(rows),
            "total": round(sum(r["amount"] for r in rows), 2)}

@app.post("/teardown/compute")
def teardown_compute(data: TeardownCompute, _=Depends(require_api_key)):
    """Across ledgers: the recurring lines and the annualised cost — deliverables
    D2 and D3 of the Stage 0 SOW."""
    ledgers = data.ledgers or []
    if not ledgers and data.ledger_ids:
        ledgers = [db_get(f"ledger:{lid}") for lid in data.ledger_ids]
        ledgers = [l for l in ledgers if l]
    if not ledgers and data.project_id:
        ledgers = [db_get(f"ledger:{lid}") for lid in db_smembers(f"project:{data.project_id}:ledgers")]
        ledgers = [l for l in ledgers if l]
    if not ledgers:
        raise HTTPException(422, "No ledgers found. Run /variance/compute for each production first.")
    patterns = variance.recurring_patterns(ledgers)
    return {
        "success": True,
        "productions": len(ledgers),
        "recurring_patterns": patterns,
        "annualised": variance.annualise(ledgers, productions_per_year=data.productions_per_year,
                                         patterns=patterns),
        "rate_proposals": [
            p for led in ledgers
            for p in ratecard.propose_from_variance(led, region="india", city="mumbai",
                                                    source=f"teardown: {led.get('production') or led.get('id')}")
        ][:200],
    }

class RateProposals(BaseModel):
    proposals: list

@app.post("/rates/apply-proposals")
def rates_apply_proposals(data: RateProposals, _=Depends(require_api_key)):
    """Commit rate corrections a producer accepted. Repeated observations blend
    into the existing rate and grow its sample size."""
    try:
        written = ratecard.apply_proposals(data.proposals)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"success": True, "written": len(written), "rates": written}

# ── INDIA COMPLIANCE (GST + TDS) ──────────────────────────────────────────────

class ComplianceCompute(BaseModel):
    project_id: Optional[str] = None
    budget: Optional[dict] = None
    payee_types: Optional[dict] = None     # line code → "individual" | "entity"
    apply_thresholds: Optional[bool] = True

class PaymentScheduleRequest(BaseModel):
    project_id: Optional[str] = None
    budget: Optional[dict] = None
    advance_pct: Optional[float] = 0.4
    payee_types: Optional[dict] = None

@app.post("/compliance/compute")
def compliance_compute(data: ComplianceCompute, _=Depends(require_api_key)):
    budget = _resolve_budget(data.project_id, data.budget)
    return {"success": True,
            "compliance": compliance.compute_budget(
                budget,
                payee_types=data.payee_types or {},
                apply_thresholds=bool(data.apply_thresholds))}

@app.post("/compliance/payment-schedule")
def compliance_payment_schedule(data: PaymentScheduleRequest, _=Depends(require_api_key)):
    budget = _resolve_budget(data.project_id, data.budget)
    return {"success": True,
            "payment_schedule": compliance.payment_schedule(
                budget,
                advance_pct=data.advance_pct if data.advance_pct is not None else 0.4,
                payee_types=data.payee_types or {})}

# ── VERSIONS, DIFF AND EXPORT ─────────────────────────────────────────────────
# Budgets were already versioned in storage; what was missing was any way to see
# what moved between two of them, and any way to get a budget out of Mark into
# the tools the money actually lives in. See budgetdiff.py and exporters.py.

class BudgetDiffRequest(BaseModel):
    project_id: Optional[str] = None
    before_id: Optional[str] = None
    after_id: Optional[str] = None
    before: Optional[dict] = None
    after: Optional[dict] = None
    currency: Optional[str] = "INR"

class BudgetExport(BaseModel):
    project_id: Optional[str] = None
    budget: Optional[dict] = None
    currency: Optional[str] = "INR"
    format: Optional[str] = "xlsx"   # xlsx | mm | csv

class TeardownReportRequest(BaseModel):
    project_id: Optional[str] = None
    ledger_ids: Optional[list] = None
    ledgers: Optional[list] = None
    client: Optional[str] = ""
    productions_per_year: Optional[int] = None
    currency: Optional[str] = "INR"

def _budget_versions(project_id: str) -> list[dict]:
    ids = db_smembers(f"project:{project_id}:budgets")
    records = [db_get(f"budget:{bid}") for bid in ids]
    rows = [budgetdiff.version_summary(r) for r in records if r]
    rows.sort(key=lambda v: v.get("created_at") or "", reverse=True)
    return rows

def _resolve_ledgers(data) -> list[dict]:
    ledgers = data.ledgers or []
    if not ledgers and data.ledger_ids:
        ledgers = [db_get(f"ledger:{lid}") for lid in data.ledger_ids]
    if not ledgers and data.project_id:
        ledgers = [db_get(f"ledger:{lid}") for lid in db_smembers(f"project:{data.project_id}:ledgers")]
    ledgers = [l for l in ledgers if l]
    ledgers.sort(key=lambda l: l.get("created_at") or "")
    return ledgers

@app.post("/budget/versions")
def budget_versions(data: ProjectIdRequest, _=Depends(require_api_key)):
    """Every stored version of a project's budget, newest first, each with its
    line count and total — enough to pick two and diff them."""
    return {"success": True, "versions": _budget_versions(data.project_id)}

@app.post("/budget/diff")
def budget_diff(data: BudgetDiffRequest, _=Depends(require_api_key)):
    """What moved between two versions. Pass two budget objects, two stored ids,
    or a project_id alone to compare its two most recent versions."""
    before, after = data.before, data.after
    if not (before and after):
        if data.before_id and data.after_id:
            b = db_get(f"budget:{data.before_id}")
            a = db_get(f"budget:{data.after_id}")
            if not b or not a:
                raise HTTPException(404, "One or both budget versions not found")
            before, after = b.get("budget_data") or {}, a.get("budget_data") or {}
        elif data.project_id:
            versions = _budget_versions(data.project_id)
            if len(versions) < 2:
                raise HTTPException(422, f"Need two saved versions to diff; this project has "
                                         f"{len(versions)}")
            a_rec = db_get(f"budget:{versions[0]['id']}")
            b_rec = db_get(f"budget:{versions[1]['id']}")
            before, after = (b_rec.get("budget_data") or {}), (a_rec.get("budget_data") or {})
        else:
            raise HTTPException(422, "Pass `before`/`after`, `before_id`/`after_id`, or a project_id")
    return {"success": True, "diff": budgetdiff.diff(before, after, currency=data.currency or "INR")}

@app.post("/budget/export")
def budget_export(data: BudgetExport, _=Depends(require_api_key)):
    """Budget → .xlsx, or a Movie Magic interchange file.

    Returns base64 rather than a file response so the same call works from the
    browser, an agent over MCP and a scheduled job. The `note` on the Movie Magic
    format is deliberate: it is a delimited import file, not a `.mmb`."""
    budget = _resolve_budget(data.project_id, data.budget)
    fmt = (data.format or "xlsx").lower()
    currency = data.currency or "INR"
    title = (budget.get("title") or "budget").replace("/", "-")[:60]
    if fmt == "xlsx":
        raw = exporters.to_xlsx(budget, currency=currency)
        return {"success": True, "filename": f"{title}.xlsx",
                "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "base64": base64.b64encode(raw).decode()}
    if fmt in ("mm", "movie-magic", "mmb"):
        text = exporters.to_mm_interchange(budget, currency=currency)
        return {"success": True, "filename": f"{title}-mm-import.csv", "content_type": "text/csv",
                "text": text,
                "note": "Delimited account/detail file for Movie Magic Budgeting's import. "
                        "Not a .mmb — that format is proprietary and undocumented, and a wrong "
                        "one opens with wrong numbers."}
    raise HTTPException(422, "format must be one of: xlsx, mm")

@app.post("/budget/import")
async def budget_import(file: UploadFile = File(...), _=Depends(require_api_key)):
    """Read a client's own budget spreadsheet into Mark's shape, so a first
    engagement starts from their numbers. Reports what it skipped."""
    name = (file.filename or "").lower()
    if not name.endswith(".xlsx"):
        raise HTTPException(400, "Supported: .xlsx (export the sheet from Excel or Sheets first)")
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Empty file")
    if len(raw) > _MAX_TEMPLATE_BYTES:
        raise HTTPException(413, f"File too large (max {_MAX_TEMPLATE_BYTES // 1024 // 1024} MB)")
    try:
        budget = await run_in_threadpool(exporters.from_xlsx, raw,
                                         title=(file.filename or "Imported budget").rsplit(".", 1)[0])
    except Exception as e:
        raise HTTPException(422, f"Could not read the budget: {type(e).__name__}: {e}")
    if not budget.get("sections"):
        raise HTTPException(422, "; ".join(budget.get("flags") or ["Nothing importable found"]))
    return {"success": True, "budget": budget}

@app.post("/variance/export")
def variance_export(data: dict = None, _=Depends(require_api_key)):
    """The Variance Ledger as a spreadsheet — deliverable D2, which the SOW
    specifies as a spreadsheet because the client will want to sort it."""
    data = data or {}
    ledger = data.get("ledger")
    if not ledger and data.get("ledger_id"):
        ledger = db_get(f"ledger:{data['ledger_id']}")
    if not ledger:
        raise HTTPException(422, "Pass `ledger` or a stored `ledger_id`")
    raw = exporters.ledger_to_xlsx(ledger)
    name = (ledger.get("production") or "variance-ledger").replace("/", "-")[:60]
    return {"success": True, "filename": f"{name}-variance-ledger.xlsx",
            "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "base64": base64.b64encode(raw).decode()}

@app.post("/teardown/report")
def teardown_report_endpoint(data: TeardownReportRequest, _=Depends(require_api_key)):
    """The Stage 0 report (D1, D3, D4) as printable HTML. Print → Save as PDF.

    Every figure traces to a ledger line; the renderer states what it cannot
    support rather than filling gaps."""
    ledgers = _resolve_ledgers(data)
    if not ledgers:
        raise HTTPException(422, "No ledgers found. Run /variance/compute for each production first.")
    patterns = variance.recurring_patterns(ledgers)
    annualised = None
    if data.productions_per_year and len(ledgers) >= 2:
        annualised = variance.annualise(ledgers, productions_per_year=data.productions_per_year,
                                        patterns=patterns)
    html_doc = teardown_report.render(
        ledgers, client=data.client or "", annualised=annualised, patterns=patterns,
        currency=data.currency or "INR")
    return {"success": True, "html": html_doc, "productions": len(ledgers),
            "recurring_patterns": len(patterns)}

@app.post("/budget/save")
def save_budget(data: BudgetSave, _=Depends(require_api_key)):
    p = db_get(f"project:{data.project_id}")
    if not p: raise HTTPException(404, "Project not found")
    bid = str(uuid.uuid4())
    budget = {
        "id": bid, "project_id": data.project_id,
        "version": data.version, "budget_data": data.budget_data,
        "created_at": now(), "locked": False
    }
    db_set(f"budget:{data.project_id}:latest", budget)
    db_set(f"budget:{bid}", budget)
    db_sadd(f"project:{data.project_id}:budgets", bid)
    return {"success": True, "budget_id": bid}

@app.post("/budget/get")
def get_budget(data: ProjectIdRequest, _=Depends(require_api_key)):
    b = db_get(f"budget:{data.project_id}:latest")
    if not b: raise HTTPException(404, "No budget found for this project")
    return {"success": True, "budget": b}

@app.post("/budget/history")
def budget_history(data: ProjectIdRequest, _=Depends(require_api_key)):
    ids = db_smembers(f"project:{data.project_id}:budgets")
    budgets = [db_get(f"budget:{bid}") for bid in ids]
    budgets = [b for b in budgets if b]
    return {"success": True, "budgets": sorted(budgets, key=lambda x: x.get("created_at", ""), reverse=True)}

# ── FEEDBACK ──────────────────────────────────────────────────────────────────
# Captures thumbs/comments on budgets, questions, etc. Persists to Redis (or
# in-memory fallback). Listing requires the API key when one is configured.

@app.post("/feedback/create")
def create_feedback(data: FeedbackCreate, request: Request, _=Depends(require_api_key)):
    fid = str(uuid.uuid4())
    entry = {
        "id": fid,
        "context_type": data.context_type,
        "context_id": data.context_id,
        "rating": data.rating,
        "comment": (data.comment or "").strip(),
        "snapshot": data.snapshot,
        "user_email": (data.user_email or "").strip() or None,
        "user_agent": data.user_agent or request.headers.get("user-agent"),
        "page": data.page,
        "ip": request.client.host if request.client else None,
        "created_at": now(),
    }
    db_set(f"feedback:{fid}", entry)
    db_sadd("feedback:all", fid)
    if data.context_id:
        db_sadd(f"feedback:by-context:{data.context_id}", fid)
    return {"success": True, "feedback_id": fid}

@app.post("/lead/create")
async def create_lead(data: LeadCreate, request: Request, _=Depends(require_api_key)):
    """Record a producer email captured by the soft demo gate.

    Idempotent on email — repeat submits update the existing record's
    last_seen and accumulate the trigger list, rather than creating
    duplicate rows. This keeps the leads:all set clean for follow-up."""
    email = (data.email or "").strip().lower()
    if not email or "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(400, "Valid email required")

    key = f"lead:{email}"
    existing = db_get(key) or {}
    triggers = existing.get("triggers") or []
    if data.trigger and data.trigger not in triggers:
        triggers.append(data.trigger)

    record = {
        "email": email,
        "role": data.role or existing.get("role"),
        "company": data.company or existing.get("company"),
        "page": data.page or existing.get("page"),
        "user_agent": data.user_agent or existing.get("user_agent"),
        "ip": request.client.host if request.client else existing.get("ip"),
        "first_seen": existing.get("first_seen") or now(),
        "last_seen": now(),
        "triggers": triggers,
        "submission_count": (existing.get("submission_count") or 0) + 1,
    }
    db_set(key, record)
    db_sadd("leads:all", email)
    return {"success": True, "lead": record}

@app.post("/feedback/list")
def list_feedback(_=Depends(require_api_key)):
    ids = db_smembers("feedback:all")
    items = [db_get(f"feedback:{fid}") for fid in ids]
    items = [i for i in items if i]
    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return {"success": True, "feedback": items, "total": len(items)}

# ── SCRIPT PARSING ────────────────────────────────────────────────────────────
# Parsing lives in screenplay.py — pure, and therefore covered offline in
# evals/. This endpoint owns the upload, the size cap and the PDF call only.
_process_pages = screenplay.process_pages


_MAX_PDF_BYTES = int(os.getenv("MAX_PDF_BYTES", str(25 * 1024 * 1024)))  # 25MB default

def _parse_pdf_sync(raw: bytes) -> tuple[dict, str]:
    """CPU-bound work — must run via run_in_threadpool, not on the event loop."""
    from screenplay_pdf_to_json import convert
    pages = convert(io.BytesIO(raw), 0)  # second arg is start-page; 0 = scan from beginning
    return _process_pages(pages)

@app.post("/script/parse")
async def parse_script(file: UploadFile = File(...), _=Depends(require_api_key)):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF screenplays are supported")
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Empty file")
    if len(raw) > _MAX_PDF_BYTES:
        raise HTTPException(413, f"PDF too large (max {_MAX_PDF_BYTES // 1024 // 1024} MB)")

    try:
        summary, extracted_text = await run_in_threadpool(_parse_pdf_sync, raw)
    except ImportError:
        raise HTTPException(500, "screenplay-pdf-to-json not installed on server")
    except Exception as e:
        raise HTTPException(422, f"Failed to parse PDF: {e}")

    return {
        "success": True,
        "summary": summary,
        "extracted_text": extracted_text,
    }

# ── TEMPLATE PARSING ──────────────────────────────────────────────────────────
# Extracts the plain text of a producer's uploaded call-sheet template so the
# render-callsheet-template agent can mimic its layout. Uses only stdlib + the
# already-installed screenplay parser — no new dependencies. The goal is a
# faithful-enough textual skeleton of the template (section names, field labels,
# table headers, ordering), not pixel-perfect layout.
_MAX_TEMPLATE_BYTES = int(os.getenv("MAX_TEMPLATE_BYTES", str(15 * 1024 * 1024)))  # 15MB

def _strip_xml_tags(xml: str) -> str:
    import re
    # Turn block/paragraph/row/cell/tab boundaries into whitespace so words and
    # cell values don't fuse. Covers Word (w:p/w:tr/w:tc), HTML (tr/td) and
    # spreadsheet (row/c) element boundaries.
    xml = re.sub(r"</w:p>|</a:p>|<w:br/?>|</w:tr>|</tr>|</row>", "\n", xml)
    xml = re.sub(r"<w:tab/?>|</w:tc>|</td>|</c>", "\t", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&#160;", " ")
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)

def _extract_docx_text(raw: bytes) -> str:
    import zipfile
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        with z.open("word/document.xml") as f:
            return _strip_xml_tags(f.read().decode("utf-8", "ignore"))

def _extract_xlsx_text(raw: bytes) -> str:
    import zipfile, re
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names = z.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            with z.open("xl/sharedStrings.xml") as f:
                blob = f.read().decode("utf-8", "ignore")
            shared = re.findall(r"<t[^>]*>(.*?)</t>", blob, flags=re.DOTALL)
        sheets = sorted(n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
        out = []
        for s in sheets:
            with z.open(s) as f:
                out.append(_strip_xml_tags(f.read().decode("utf-8", "ignore")))
        joined = "\n".join(out)
        if shared:
            joined += "\n" + "\n".join(t.strip() for t in shared if t.strip())
        return joined

def _extract_template_sync(filename: str, raw: bytes) -> str:
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        _, text = _parse_pdf_sync(raw)
        return text
    if name.endswith(".docx"):
        return _extract_docx_text(raw)
    if name.endswith((".xlsx", ".xlsm")):
        return _extract_xlsx_text(raw)
    if name.endswith((".html", ".htm")):
        return _strip_xml_tags(raw.decode("utf-8", "ignore"))
    # txt, md, csv, and anything else: treat as text.
    return raw.decode("utf-8", "ignore")

@app.post("/template/parse")
async def parse_template(file: UploadFile = File(...), _=Depends(require_api_key)):
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Empty file")
    if len(raw) > _MAX_TEMPLATE_BYTES:
        raise HTTPException(413, f"Template too large (max {_MAX_TEMPLATE_BYTES // 1024 // 1024} MB)")
    name = (file.filename or "").lower()
    if name.endswith((".doc",)) and not name.endswith(".docx"):
        raise HTTPException(422, "Legacy .doc isn't supported — please save as .docx, PDF or another format.")
    try:
        text = await run_in_threadpool(_extract_template_sync, file.filename or "", raw)
    except ImportError:
        raise HTTPException(500, "PDF parser not installed on server")
    except Exception as e:
        raise HTTPException(422, f"Could not read template: {e}")
    text = (text or "").strip()
    if not text:
        raise HTTPException(422, "No text found in the template")
    return {"success": True, "filename": file.filename, "text": text, "chars": len(text)}

# ── FLUE AGENT PROXY ──────────────────────────────────────────────────────────
# Forwards questionnaire answers + project context to the Flue sidecar so the
# agent can produce a typed budget. Set FLUE_BASE_URL to the running Flue server
# (default http://localhost:3583 in dev, or the deployed Node URL in prod).

_FLUE_BASE_URL = os.getenv("FLUE_BASE_URL", "http://localhost:3583")

# ── UNIPILE INTEGRATION ───────────────────────────────────────────────────────
# Unipile is a unified messaging API — it proxies Gmail, Outlook, WhatsApp,
# LinkedIn, Instagram and Telegram behind one HTTP interface. We use it to
# actually send call sheets to crew, replacing the v1 mocked send.
#
# Required env vars (configure on Railway):
#   UNIPILE_DSN      — full base URL incl. port, e.g. https://api35.unipile.com:16583
#   UNIPILE_API_KEY  — the X-API-KEY value from the Unipile dashboard
#
# Behaviour: if either env var is missing we keep the legacy mocked behaviour
# so local dev still works without credentials. Production must set both.
_UNIPILE_DSN = (os.getenv("UNIPILE_DSN") or "").rstrip("/")
_UNIPILE_API_KEY = os.getenv("UNIPILE_API_KEY") or ""

def _unipile_creds() -> tuple[str, str]:
    """Resolve Unipile creds for the active tenant: their connected account
    first (brokered, encrypted at rest), then the process env fallback so
    single-tenant deploys keep working."""
    conn = connections.get_connection("unipile") or {}
    dsn = (conn.get("dsn") or _UNIPILE_DSN or "").rstrip("/")
    api_key = conn.get("api_key") or _UNIPILE_API_KEY
    return dsn, api_key

def _unipile_configured() -> bool:
    dsn, api_key = _unipile_creds()
    return bool(dsn and api_key)

async def _unipile_request(method: str, path: str, *, json_body=None, data=None, files=None, timeout=30):
    """Thin httpx wrapper for Unipile. Raises HTTPException on transport failure;
    returns the raw response so callers can inspect status codes individually."""
    dsn, api_key = _unipile_creds()
    if not (dsn and api_key):
        raise HTTPException(503, "Unipile is not connected for this tenant (connect it via /connections/set or set UNIPILE_DSN/UNIPILE_API_KEY)")
    url = f"{dsn}{path}"
    headers = {"X-API-KEY": api_key, "accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.request(method, url, headers=headers, json=json_body, data=data, files=files)
    except httpx.TimeoutException:
        raise HTTPException(504, f"Unipile timed out on {method} {path}")
    except httpx.RequestError as e:
        raise HTTPException(502, f"Unipile request failed: {type(e).__name__}")

@app.get("/integrations/unipile/accounts")
async def unipile_accounts(_=Depends(require_api_key)):
    """List the messaging accounts the producer has connected on Unipile.
    The frontend calls this on the call-sheet Send step so the producer can
    see which Gmail / WhatsApp / LinkedIn accounts will actually send."""
    if not _unipile_configured():
        return {"success": True, "configured": False, "accounts": []}
    resp = await _unipile_request("GET", "/api/v1/accounts")
    if not resp.is_success:
        raise HTTPException(resp.status_code, f"Unipile accounts fetch failed: {resp.text[:200]}")
    payload = resp.json() if resp.content else {}
    items = payload.get("items") or payload.get("accounts") or payload.get("data") or []
    return {"success": True, "configured": True, "accounts": items}

# Provider buckets — Unipile labels these on the account record so we can
# pick the right one per channel.
_UNIPILE_EMAIL_PROVIDERS = {"GOOGLE", "GOOGLE_OAUTH", "OUTLOOK", "OUTLOOK_OAUTH", "MAIL", "ICLOUD", "GMX"}
_UNIPILE_WHATSAPP_PROVIDERS = {"WHATSAPP"}
_UNIPILE_LINKEDIN_PROVIDERS = {"LINKEDIN"}
_UNIPILE_INSTAGRAM_PROVIDERS = {"INSTAGRAM"}
_UNIPILE_TELEGRAM_PROVIDERS = {"TELEGRAM"}

def _pick_account(accounts: list, providers: set) -> Optional[dict]:
    """Return the first connected, OK-status account whose provider matches."""
    for a in accounts or []:
        prov = (a.get("type") or a.get("provider") or "").upper()
        status = (a.get("status") or a.get("sync_status") or "OK").upper()
        if prov in providers and status in {"OK", "CONNECTED", "ACTIVE"}:
            return a
    return None

def _provider_message_id(resp) -> Optional[str]:
    """Pull the provider's message id out of a Unipile response.

    Delivery and read receipts arrive later as webhooks keyed on this id, so a
    send that does not capture it can never report anything but "sent". Unipile
    is not consistent about where it puts the id across endpoints, hence the
    tolerance — and a miss is silent by design, because failing a successful
    send over a missing receipt id would be the wrong trade.
    """
    try:
        body = resp.json()
    except Exception:
        return None
    if not isinstance(body, dict):
        return None
    for key in ("message_id", "id", "chat_id", "tracking_id"):
        value = body.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value)
    nested = body.get("message") or body.get("data") or {}
    if isinstance(nested, dict):
        for key in ("message_id", "id"):
            value = nested.get(key)
            if isinstance(value, (str, int)) and str(value).strip():
                return str(value)
    return None

def _render_callsheet_text(cs: dict, recipient: dict) -> str:
    """Plain-text call sheet body for WhatsApp / LinkedIn / Telegram. Kept
    short — these channels render best with a compact summary plus a few
    must-have details rather than a wall of formatting."""
    shoot = cs.get("shoot") or {}
    title = cs.get("project_title") or "Call Sheet"
    name = (recipient.get("name") or "team").split(" ")[0]
    lines = [
        f"Hi {name},",
        "",
        f"Call sheet — {title}",
        f"Day {shoot.get('day_number') or '—'} · {shoot.get('date') or 'TBD'}",
        f"Unit call: {shoot.get('unit_call') or '—'}    Wrap: {shoot.get('wrap_time') or '—'}",
    ]
    if shoot.get("locations"):
        lines.append(f"Location: {shoot.get('locations')}")
    if shoot.get("scenes"):
        lines.append(f"Scenes: {shoot.get('scenes')}")
    if recipient.get("role"):
        lines.append(f"Your role: {recipient.get('role')}")
    if shoot.get("hospital"):
        lines.append(f"Nearest A&E: {shoot.get('hospital')}")
    if shoot.get("production_notes"):
        lines.append("")
        lines.append(f"Notes: {shoot.get('production_notes')}")
    lines.append("")
    lines.append("— Sent via Mark (askmark.co)")
    return "\n".join(lines)

def _render_callsheet_html(cs: dict, recipient: dict) -> str:
    """Lightweight HTML body for email — paper-cream styling that survives
    Gmail / Outlook stripping. No external assets."""
    shoot = cs.get("shoot") or {}
    title = cs.get("project_title") or "Call Sheet"
    name = (recipient.get("name") or "team").split(" ")[0]
    def row(label, value):
        if not value: return ""
        return f"<tr><td style='padding:6px 12px;background:#EBE5D2;font-family:monospace;font-size:11px;text-transform:uppercase;letter-spacing:0.08em;color:#3A352D;'>{label}</td><td style='padding:6px 12px;font-family:Inter,Arial,sans-serif;font-size:14px;color:#14110D;'>{value}</td></tr>"
    notes_block = ""
    if shoot.get("production_notes"):
        notes_block = f"<div style='margin-top:16px;padding:12px 14px;background:#EBE5D2;border-left:3px solid #C8330A;font-family:Inter,Arial,sans-serif;font-size:13px;color:#3A352D;line-height:1.6;'>{shoot.get('production_notes')}</div>"
    return f"""
    <div style='background:#F5F1E8;padding:24px;font-family:Inter,Arial,sans-serif;color:#14110D;'>
      <div style='max-width:560px;margin:0 auto;background:#fff;border:1px solid rgba(20,17,13,0.16);padding:28px 32px;'>
        <div style='font-family:Georgia,serif;font-size:22px;color:#14110D;margin-bottom:4px;'>{title}</div>
        <div style='font-family:monospace;font-size:11px;color:#8A8275;letter-spacing:0.08em;text-transform:uppercase;margin-bottom:18px;'>Hi {name}, here's your call sheet.</div>
        <table style='width:100%;border-collapse:collapse;'>
          {row('Day', shoot.get('day_number'))}
          {row('Date', shoot.get('date'))}
          {row('Unit Call', shoot.get('unit_call'))}
          {row('Wrap', shoot.get('wrap_time'))}
          {row('Scenes', shoot.get('scenes'))}
          {row('Location', shoot.get('locations'))}
          {row('Your Role', recipient.get('role'))}
          {row('Nearest A&E', shoot.get('hospital'))}
        </table>
        {notes_block}
        <div style='margin-top:24px;font-family:monospace;font-size:10px;letter-spacing:0.08em;color:#8A8275;'>Sent via Mark · askmark.co</div>
      </div>
    </div>
    """

# ── BUDGET ENGINE GUARDRAILS ──────────────────────────────────────────────────
# Same input → same output. Krish's biggest complaint from the test runs was
# that running NIKE TVC twice with identical answers produced ₹1.95Cr vs
# ₹3.21Cr. LLM determinism is impossible at temp>0, so we hash the inputs and
# cache the result. TTL = 7 days (long enough for a producer to share a link
# with their team and have it look identical, short enough that a tweaked
# prompt or rate card eventually invalidates).
_BUDGET_CACHE_TTL_SECONDS = 7 * 24 * 3600

def _rate_fingerprint(pack: Optional[list]) -> Optional[str]:
    """A short, order-independent hash of the rate pack that priced a budget.

    Only the fields that change the number are included — item, rate, unit and
    whether it was verified. Descriptions and sources move without changing the
    arithmetic and would otherwise bust the cache for nothing."""
    if not pack:
        return None
    rows = sorted(
        (str(p.get("item_key")), float(p.get("rate") or 0), str(p.get("unit")), bool(p.get("verified")))
        for p in pack
    )
    blob = json.dumps(rows, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]

def _budget_cache_key(payload: dict) -> str:
    # Stable serialisation: sort_keys + drop None so two semantically equal
    # inputs hash the same. Project state isn't included — caching across
    # producers is fine, but project_id is.
    canon = {
        "script": payload.get("script", "").strip(),
        "region": payload.get("region", "india"),
        "currency": payload.get("currency"),
        "qa": payload.get("qa") or [],
        "breakdown": payload.get("breakdown"),
        # Model is part of the key so a Haiku (sync) and Sonnet (async) result for
        # the same inputs don't collide. Absent on the sync path → keys unchanged.
        "model": payload.get("model"),
        # Rate pack fingerprint. This cache is deliberately global (two producers
        # asking for the same TVC should see the same number), but rate cards are
        # per-tenant — without this, tenant A's rate-priced budget would be
        # served to tenant B, and a corrected rate would keep returning the
        # pre-correction budget until the cache expired.
        "rates": _rate_fingerprint(payload.get("rates")),
    }
    blob = json.dumps(canon, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]
    return f"budget-cache:{digest}"

def _budget_cache_get(payload: dict) -> Optional[dict]:
    if not r:  # in-memory fallback isn't persistent enough to be worth caching
        return None
    key = _budget_cache_key(payload)
    raw = r.get(key)
    return json.loads(raw) if raw else None

def _budget_cache_set(payload: dict, result: dict) -> None:
    if not r:
        return
    key = _budget_cache_key(payload)
    r.setex(key, _BUDGET_CACHE_TTL_SECONDS, json.dumps(result))

def _budget_cache_clear() -> int:
    """Drop all cached budgets. Used after rate card / prompt changes that
    would otherwise leave stale results pinned to inputs that should now
    produce different output. Returns the number of keys deleted."""
    if not r:
        return 0
    keys = list(r.scan_iter("budget-cache:*"))
    if not keys:
        return 0
    return r.delete(*keys)

# Production : Post-production sanity check. Indian TVCs typically run
# post at 15–25% of production unless heavy VFX is flagged. Anything below
# 10% with no VFX section means the agent under-built post — happens because
# Haiku sometimes drops the 12900/13100 sections altogether under prompt
# pressure. Returns a corrective hint string we feed back to the agent for
# one retry, or None if the budget looks balanced.
def _post_ratio_hint(budget: dict) -> Optional[str]:
    sections = budget.get("sections") or []
    prod_total = 0
    post_total = 0
    has_vfx = False
    for s in sections:
        items_total = sum(float(li.get("amount") or 0) for li in (s.get("items") or []))
        stype = s.get("type", "")
        code = str(s.get("code") or "")
        if stype == "below_the_line" or stype == "above_the_line":
            prod_total += items_total
        elif stype == "post" or code.startswith("129") or code.startswith("131") or code.startswith("133"):
            post_total += items_total
        if code == "13300" or "vfx" in (s.get("name") or "").lower():
            has_vfx = True
    if prod_total <= 0:
        return None
    ratio = post_total / prod_total
    if ratio < 0.12 and not has_vfx:
        return (
            f"POST PRODUCTION IS UNDERWEIGHT: post_total={post_total:.0f} is only "
            f"{ratio*100:.1f}% of production_total={prod_total:.0f}. For Indian TVC/film "
            f"productions without significant VFX, post should be at least 15–25% of "
            f"production. Expand sections 12900 (Editorial), 13100 (Post Sound), and add "
            f"colour grade if missing. Resend the full budget with corrected post."
        )
    return None


async def _flue_call(agent_name: str, run_id: str, payload: dict) -> dict:
    url = f"{_FLUE_BASE_URL}/agents/{agent_name}/{run_id}"
    # Trace every agent call — inputs (truncated), latency, ok/error land in the
    # `mark:traces` ring buffer regardless of whether the call succeeds.
    async with observability.trace(
        f"flue:{agent_name}",
        run_id=run_id,
        model=payload.get("model"),
        input={k: v for k, v in payload.items() if k not in ("project", "crew")},
    ) as span:
        try:
            async with httpx.AsyncClient(timeout=180) as client:
                resp = await client.post(url, json=payload)
        except httpx.TimeoutException:
            raise HTTPException(504, f"flue/{agent_name}: agent timed out")
        except httpx.RequestError as e:
            raise HTTPException(503, f"flue/{agent_name}: agent unavailable ({type(e).__name__})")
        if not resp.is_success:
            try:
                detail = resp.json().get("error") or resp.text
            except Exception:
                detail = f"Flue agent error (HTTP {resp.status_code})"
            span["ok"] = False
            span["error"] = f"HTTP {resp.status_code}: {str(detail)[:200]}"
            raise HTTPException(resp.status_code, f"flue/{agent_name}: {detail}")
        result = resp.json()
        span["output"] = result
        return result

@app.post("/budget/generate")
async def generate_budget(data: BudgetGenerate, _=Depends(require_api_key)):
    # Build the qa array. Prefer the structured `qa` payload; fall back to
    # the legacy flat `answers` map (where the question text is missing).
    qa = data.qa or []
    if not qa and data.answers:
        qa = [{"id": k, "question": k, "answer": str(v)} for k, v in data.answers.items()]

    project = None
    crew: list = []
    if data.project_id:
        project = db_get(f"project:{data.project_id}")
        if not project:
            raise HTTPException(404, "Project not found")
        crew = [db_get(f"crew:{cid}") for cid in db_smembers(f"project:{data.project_id}:crew")]
        crew = [c for c in crew if c]

    # `project_type` is "tvc"/"music_video", NOT a region — never use it as a fallback.
    region = data.region or "india"
    _CURRENCY_SYMBOLS = {"INR": "₹", "USD": "$", "GBP": "£"}
    currency = data.currency
    if not currency and project:
        code = project.get("currency", "INR")
        currency = {"code": code, "symbol": _CURRENCY_SYMBOLS.get(code, code)}
    currency = currency or {"code": "INR", "symbol": "₹"}

    payload = {
        "script": data.script or (project.get("brief", "") if project else ""),
        "region": region,
        "currency": currency,
        "qa": qa,
    }
    if data.breakdown:
        # The per-scene list is for the scheduler, not the costing agent — it is
        # large and adds nothing the aggregates don't already carry.
        payload["breakdown"] = {k: v for k, v in data.breakdown.items()
                                if k not in ("scenes", "scenes_truncated")}
    if data.model:
        payload["model"] = data.model

    # Rate library → the agent. A rate the tenant has verified is binding; the
    # prompt is told to use it verbatim and cite its source rather than
    # estimating. This is the whole point of ratecard.py.
    if data.use_rates:
        pack = ratecard.resolve_pack(region, city=(data.city or "").strip().lower(),
                                     tier=data.tier or "mid",
                                     currency=(currency or {}).get("code"))
        if pack:
            payload["rates"] = pack

    # Cache lookup — same canonical inputs always return the same budget.
    # Project context (project record + crew) doesn't go into the cache key
    # because it would defeat the point: producers want NIKE TVC + same QA
    # to render the same number every time, regardless of project metadata.
    cached = _budget_cache_get(payload)

    if project:
        payload["project"] = project
        payload["crew"] = crew

    run_id = data.project_id or str(uuid.uuid4())

    if cached is not None:
        agent_result = cached
        cache_hit = True
        # Record cache hits too — otherwise the trace stream looks like the
        # agent simply wasn't called, and you can't tell a cache serve from an
        # outage.
        async with observability.trace("budget:cache-hit", run_id=run_id) as span:
            span["cache_hit"] = True
            span["output"] = {"title": agent_result.get("title") if isinstance(agent_result, dict) else None}
    else:
        # Real generation — gate on the tenant's plan quota, then count it.
        # Cache hits above are free and don't consume quota.
        metering.check_quota("budgets")
        agent_result = await _flue_call("generate-budget", run_id, payload)
        metering.record("budgets")
        # Flue wraps `session.prompt({result: schema})` returns in `{result: ...}` on the wire.
        # Unwrap so callers see the flat budget object the schema describes.
        if isinstance(agent_result, dict) and set(agent_result.keys()) == {"result"}:
            agent_result = agent_result["result"]

        # Post:Production ratio guardrail — one retry if the agent
        # under-built post production. We append a corrective hint to the
        # script field so the prompt sees the issue without changing the
        # schema.
        hint = _post_ratio_hint(agent_result) if isinstance(agent_result, dict) else None
        if hint:
            retry_payload = {**payload, "script": (payload.get("script") or "") + "\n\n[CORRECTION]\n" + hint}
            retry_result = await _flue_call("generate-budget", run_id, retry_payload)
            if isinstance(retry_result, dict) and set(retry_result.keys()) == {"result"}:
                retry_result = retry_result["result"]
            # Only accept the retry if it actually fixed the imbalance.
            if isinstance(retry_result, dict) and not _post_ratio_hint(retry_result):
                agent_result = retry_result

        # Persist successful (post-guardrail) results to the cache.
        cache_payload = dict(payload)
        cache_payload.pop("project", None)
        cache_payload.pop("crew", None)
        _budget_cache_set(cache_payload, agent_result)
        cache_hit = False

    if not data.project_id:
        # Standalone (scriptless) flow — return without persisting.
        return {"success": True, "budget": {"budget_data": agent_result, "qa": qa, "source": "flue:generate-budget", "cache_hit": cache_hit}}

    bid = str(uuid.uuid4())
    budget = {
        "id": bid,
        "project_id": data.project_id,
        "version": data.version,
        "budget_data": agent_result,
        "qa": qa,
        "created_at": now(),
        "locked": False,
        "source": "flue:generate-budget",
        "cache_hit": cache_hit,
    }
    db_set(f"budget:{data.project_id}:latest", budget)
    db_set(f"budget:{bid}", budget)
    db_sadd(f"project:{data.project_id}:budgets", bid)
    return {"success": True, "budget_id": bid, "budget": budget}

# ── DURABLE ASYNC BUDGET JOBS ─────────────────────────────────────────────────
# The synchronous /budget/generate is bound by the client-facing edge timeout
# (~60s on Railway) — which is exactly why the agent is pinned to Haiku. The
# async path decouples the client request from the agent run: enqueue → poll →
# collect. Job state lives in Redis so it survives across requests.
#
# Durability caveat: execution here is an in-process asyncio task. If the web
# process restarts mid-run, the job stays "running". The stored `request` is the
# hook for the production upgrade — an external worker consuming a Redis queue
# (RQ/Celery/Railway cron) — without changing this API. Interrupted jobs are
# reaped to "error" on next startup (see _reap_stale_jobs).
_JOB_TTL_SECONDS = int(os.getenv("JOB_TTL_SECONDS", str(24 * 3600)))

def _job_key(jid: str) -> str:
    return f"job:{jid}"

def _job_set(job: dict) -> None:
    job["updated_at"] = now()
    if r:
        r.setex(_job_key(job["id"]), _JOB_TTL_SECONDS, json.dumps(job))
        r.sadd("jobs:all", job["id"])
    else:
        _mem[_job_key(job["id"])] = job

def _job_get(jid: str) -> Optional[dict]:
    return _raw_get(_job_key(jid))  # global: jobs are uuid-keyed, tenant-immune

async def _run_budget_job(jid: str, data: "BudgetGenerate") -> None:
    job = _job_get(jid) or {"id": jid}
    job.update(status="running")
    _job_set(job)
    try:
        # Reuse the exact sync pipeline (cache, guardrail retry, persistence).
        # Depends() is inert on a direct call, so no auth double-check here.
        result = await generate_budget(data)
        job.update(status="done", result=result)
    except HTTPException as e:
        job.update(status="error", error=f"HTTP {e.status_code}: {e.detail}")
    except Exception as e:  # never let a background task die silently
        job.update(status="error", error=f"{type(e).__name__}: {e}")
    _job_set(job)

def _reap_stale_jobs() -> int:
    """On startup, flip any job left 'running'/'queued' by a previous process to
    'error' so pollers don't wait forever. Best-effort; Redis only."""
    if not r:
        return 0
    reaped = 0
    try:
        for jid in list(r.smembers("jobs:all")):
            job = _job_get(jid)
            if not job:
                r.srem("jobs:all", jid)
                continue
            if job.get("status") in ("queued", "running"):
                job.update(status="error", error="interrupted by server restart")
                _job_set(job)
                reaped += 1
    except Exception:
        pass
    return reaped

@app.on_event("startup")
async def _on_startup():
    n = _reap_stale_jobs()
    if n:
        print(f"⚠️  reaped {n} stale budget job(s) from a previous process")

@app.post("/budget/generate/async")
async def generate_budget_async(data: BudgetGenerate, _=Depends(require_api_key)):
    """Enqueue a budget generation and return immediately with a job_id. Poll
    /jobs/get for status/result. Because the client no longer waits on the agent,
    this path can request a stronger model than the sync path's Haiku."""
    jid = str(uuid.uuid4())
    if not data.model:
        # Not bound by the client edge timeout here → default to Sonnet. (This
        # still assumes the Flue service can hold the connection for a longer
        # generation; raising its proxy timeout / streaming is the next step.)
        data.model = os.getenv("ASYNC_BUDGET_MODEL", "anthropic/claude-sonnet-4-6")
    job = {
        "id": jid,
        "kind": "budget_generate",
        "status": "queued",
        "model": data.model,
        "project_id": data.project_id,
        "created_at": now(),
    }
    _job_set(job)
    asyncio.create_task(_run_budget_job(jid, data))
    return JSONResponse(
        status_code=202,
        content={"success": True, "job_id": jid, "status": "queued", "poll_endpoint": "/jobs/get"},
    )

class JobGet(BaseModel):
    job_id: str

@app.post("/jobs/get")
async def get_job(data: JobGet, _=Depends(require_api_key)):
    """Poll a job. status ∈ queued|running|done|error. When done, `result` holds
    the same body /budget/generate would have returned."""
    job = _job_get(data.job_id)
    if not job:
        raise HTTPException(404, "Job not found or expired")
    return {"success": True, "job": job}

@app.post("/budget/refine")
async def refine_budget(data: BudgetRefine, _=Depends(require_api_key)):
    """Apply a producer's free-text instruction to an existing budget.

    Two modes:
    - **Project mode** — pass `project_id`; we load the latest stored budget,
      run the refine agent, and persist a new revision.
    - **Standalone mode** — pass the `budget` JSON directly (no persistence).
      Used by the demo flow where there's no project record.
    """
    if not (data.instruction or "").strip():
        raise HTTPException(400, "instruction is required")

    current_budget = None
    project = None
    if data.project_id:
        project = db_get(f"project:{data.project_id}")
        if not project:
            raise HTTPException(404, "Project not found")
        latest = db_get(f"budget:{data.project_id}:latest")
        if not latest or not latest.get("budget_data"):
            raise HTTPException(404, "No budget exists for this project yet — generate one first")
        current_budget = latest["budget_data"]
    else:
        if not data.budget:
            raise HTTPException(400, "Either project_id or budget is required")
        current_budget = data.budget

    _CURRENCY_SYMBOLS = {"INR": "₹", "USD": "$", "GBP": "£"}
    currency = data.currency
    if not currency and project:
        code = project.get("currency", "INR")
        currency = {"code": code, "symbol": _CURRENCY_SYMBOLS.get(code, code)}
    currency = currency or {"code": "INR", "symbol": "₹"}

    payload = {
        "budget": current_budget,
        "instruction": data.instruction.strip(),
        "currency": currency,
    }
    if data.region:
        payload["region"] = data.region

    run_id = data.project_id or str(uuid.uuid4())
    agent_result = await _flue_call("refine-budget", run_id, payload)
    if isinstance(agent_result, dict) and set(agent_result.keys()) == {"result"}:
        agent_result = agent_result["result"]

    if not data.project_id:
        return {"success": True, "budget": {"budget_data": agent_result, "source": "flue:refine-budget"}}

    bid = str(uuid.uuid4())
    budget = {
        "id": bid,
        "project_id": data.project_id,
        "version": data.version,
        "budget_data": agent_result,
        "instruction": data.instruction.strip(),
        "previous_budget_id": (db_get(f"budget:{data.project_id}:latest") or {}).get("id"),
        "created_at": now(),
        "locked": False,
        "source": "flue:refine-budget",
    }
    db_set(f"budget:{data.project_id}:latest", budget)
    db_set(f"budget:{bid}", budget)
    db_sadd(f"project:{data.project_id}:budgets", bid)
    return {"success": True, "budget_id": bid, "budget": budget}

@app.post("/callsheet/refine")
async def refine_callsheet(data: CallSheetRefine, _=Depends(require_api_key)):
    """Apply a producer's free-text instruction to a call sheet via the
    refine-callsheet Flue agent. Standalone — no persistence by default;
    the frontend holds state. /callsheet/save is the persistence path."""
    if not (data.instruction or "").strip():
        raise HTTPException(400, "instruction is required")
    if not data.callsheet:
        raise HTTPException(400, "callsheet is required")

    payload = {"callsheet": data.callsheet, "instruction": data.instruction.strip()}
    run_id = str(uuid.uuid4())
    agent_result = await _flue_call("refine-callsheet", run_id, payload)
    if isinstance(agent_result, dict) and set(agent_result.keys()) == {"result"}:
        agent_result = agent_result["result"]
    return {
        "success": True,
        "callsheet": agent_result.get("callsheet", data.callsheet),
        "revision_notes": agent_result.get("revision_notes", []),
        "source": "flue:refine-callsheet",
    }

@app.post("/callsheet/render-template")
async def render_callsheet_template(data: CallSheetRenderTemplate, _=Depends(require_api_key)):
    """Render the call sheet in the producer's own uploaded template format via
    the render-callsheet-template Flue agent. Returns a self-contained HTML
    document the frontend drops straight into the preview. The frontend falls
    back to the standard format if this fails."""
    if not (data.template_text or "").strip():
        raise HTTPException(400, "template_text is required")
    if not data.callsheet:
        raise HTTPException(400, "callsheet is required")

    payload = {"callsheet": data.callsheet, "template_text": data.template_text}
    run_id = str(uuid.uuid4())
    agent_result = await _flue_call("render-callsheet-template", run_id, payload)
    if isinstance(agent_result, dict) and set(agent_result.keys()) == {"result"}:
        agent_result = agent_result["result"]
    return {
        "success": True,
        "html": agent_result.get("html", ""),
        "source": "flue:render-callsheet-template",
    }

@app.post("/callsheet/save")
async def save_callsheet(data: CallSheetSave, _=Depends(require_api_key)):
    """Persist a call-sheet snapshot under a stable id. Used by the frontend
    when the producer wants to come back to it later. Project linkage is
    optional — V1 does not require a Project record."""
    csid = str(uuid.uuid4())
    record = {
        "id": csid,
        "project_id": data.project_id,
        "callsheet": data.callsheet,
        "created_at": now(),
    }
    db_set(f"callsheet:{csid}", record)
    if data.project_id:
        db_sadd(f"project:{data.project_id}:callsheets", csid)
    return {"success": True, "callsheet_id": csid}

@app.post("/callsheet/get")
async def get_callsheet(data: dict = None, _=Depends(require_api_key)):
    csid = (data or {}).get("callsheet_id")
    if not csid:
        raise HTTPException(400, "callsheet_id is required")
    record = db_get(f"callsheet:{csid}")
    if not record:
        raise HTTPException(404, "Call sheet not found")
    return {"success": True, "record": record}

# ── HUMAN-IN-THE-LOOP SEND APPROVAL ───────────────────────────────────────────
# Sending a call sheet contacts real crew — it's irreversible. This turns "send"
# into a propose → approve → execute flow. Legacy POST /callsheet/send still
# works, but when REQUIRE_SEND_APPROVAL=1 it returns a proposal instead of firing.
_REQUIRE_SEND_APPROVAL = os.getenv("REQUIRE_SEND_APPROVAL", "").strip().lower() in ("1", "true", "yes")
_SEND_PROPOSAL_TTL_SECONDS = int(os.getenv("SEND_PROPOSAL_TTL_SECONDS", str(3600)))

def _send_preview(cs: dict, channels: list) -> dict:
    """Who would receive what, without sending anything — the thing a human
    actually approves."""
    crew = cs.get("crew") or []
    field_for = {"email": "email", "whatsapp": "phone", "linkedin": "linkedin",
                 "instagram": "instagram", "telegram": "telegram"}
    by_channel = {}
    for ch in channels:
        field = field_for.get(ch, ch)
        recips = [{"name": c.get("name"), "to": (c.get(field) or "").strip()}
                  for c in crew if (c.get(field) or "").strip()]
        by_channel[ch] = {"count": len(recips), "recipients": recips}
    return {
        "project_title": cs.get("project_title"),
        "shoot": cs.get("shoot") or {},
        "channels": channels,
        "crew_total": len(crew),
        "by_channel": by_channel,
    }

def _store_send_proposal(cs, channels, project_id, pdf_base64, pdf_filename) -> dict:
    """Stash a full send payload under a short-TTL key and return the caller a
    proposal_id + preview. The raw payload is never echoed back — only the
    preview, which is what gets approved."""
    pid = str(uuid.uuid4())
    proposal = {
        "id": pid,
        "kind": "callsheet_send",
        "status": "pending",
        "payload": {
            "callsheet": cs, "channels": channels, "project_id": project_id,
            "pdf_base64": pdf_base64, "pdf_filename": pdf_filename,
        },
        "preview": _send_preview(cs, channels),
        "created_at": now(),
    }
    _raw_set(f"send-proposal:{pid}", proposal, ttl=_SEND_PROPOSAL_TTL_SECONDS)
    return {"proposal_id": pid, "status": "pending", "preview": proposal["preview"],
            "expires_in_seconds": _SEND_PROPOSAL_TTL_SECONDS,
            "confirm_endpoint": "/callsheet/send/confirm"}

async def _execute_callsheet_send(cs: dict, channels: list, project_id, pdf_base64, pdf_filename) -> dict:
    """Actually dispatch the call sheet via Unipile (real Gmail / WhatsApp /
    LinkedIn / Instagram / Telegram). Falls back to the legacy mock when the
    Unipile env vars are unset, so local dev keeps working. Shared by the legacy
    endpoint and the approval-gated confirm path.

    Per-recipient errors do not fail the whole request — we collect them and
    return a `results` array so the producer can see which sends landed and
    which need retry."""
    crew = cs.get("crew") or []
    if not crew:
        raise HTTPException(400, "callsheet has no crew to send to")
    if not channels:
        raise HTTPException(400, "at least one send channel is required")

    sid = str(uuid.uuid4())
    base_record = {
        "id": sid,
        "project_id": project_id,
        "channels": channels,
        "shoot_date": (cs.get("shoot") or {}).get("date"),
        "shoot_day": (cs.get("shoot") or {}).get("day_number"),
        "unit_call": (cs.get("shoot") or {}).get("unit_call"),
        "wrap_time": (cs.get("shoot") or {}).get("wrap_time"),
        "crew_total": len(crew),
        "created_at": now(),
    }

    # ── MOCK FALLBACK ──────────────────────────────────────────────────────────
    # Local dev path. Mirrors the v1 mocked behaviour so the UI flow still
    # works without Unipile credentials.
    if not _unipile_configured():
        email_recipients = [c for c in crew if (c.get("email") or "").strip()] if "email" in channels else []
        whatsapp_recipients = [c for c in crew if (c.get("phone") or "").strip()] if "whatsapp" in channels else []
        record = {
            **base_record,
            "email_count": len(email_recipients),
            "whatsapp_count": len(whatsapp_recipients),
            "status": "mocked",
        }
        db_set(f"callsheet-send:{sid}", record)
        db_sadd("callsheet-sends:all", sid)
        # The delivery board is ours, not Unipile's — it opens on the mocked path
        # too, so the confirmation flow is exercisable in dev and the board is
        # never a surprise the first time real credentials are added.
        try:
            board = delivery.open_board(sid, crew, channels=channels,
                                        shoot_day=cs.get("shoot_day", ""),
                                        date=cs.get("date", ""), project_id=project_id or "")
        except Exception as e:  # noqa: BLE001
            board = None
            print(f"⚠️  delivery board not opened for mocked send {sid}: {type(e).__name__}: {e}")
        return {
            "success": True,
            "send_id": sid,
            "status": "mocked",
            "delivery": {"total": board["total"], "confirmed": 0} if board else None,
            "message": f"Unipile not configured — would send to {len(email_recipients)} via email and {len(whatsapp_recipients)} via WhatsApp.",
            "recipients": {
                "email": [r.get("email") for r in email_recipients],
                "whatsapp": [r.get("phone") for r in whatsapp_recipients],
            },
            "results": [],
        }

    # ── REAL SEND VIA UNIPILE ──────────────────────────────────────────────────
    # Gate on the tenant's send quota before dispatching (cost = crew size).
    metering.check_quota("sends", cost=len(crew))
    # Fetch the producer's connected accounts so we can choose the right
    # account_id per channel. One Unipile workspace can hold many accounts;
    # we pick the first OK one per provider type. If a producer needs to
    # pick a specific account in future we'll add an account_id param here.
    accounts_resp = await _unipile_request("GET", "/api/v1/accounts")
    if not accounts_resp.is_success:
        raise HTTPException(accounts_resp.status_code, f"Unipile accounts fetch failed: {accounts_resp.text[:200]}")
    accounts_payload = accounts_resp.json() if accounts_resp.content else {}
    accounts = accounts_payload.get("items") or accounts_payload.get("accounts") or accounts_payload.get("data") or []

    email_account = _pick_account(accounts, _UNIPILE_EMAIL_PROVIDERS) if "email" in channels else None
    whatsapp_account = _pick_account(accounts, _UNIPILE_WHATSAPP_PROVIDERS) if "whatsapp" in channels else None
    linkedin_account = _pick_account(accounts, _UNIPILE_LINKEDIN_PROVIDERS) if "linkedin" in channels else None
    instagram_account = _pick_account(accounts, _UNIPILE_INSTAGRAM_PROVIDERS) if "instagram" in channels else None
    telegram_account = _pick_account(accounts, _UNIPILE_TELEGRAM_PROVIDERS) if "telegram" in channels else None

    results = []  # per-recipient log

    # Decode the rendered call sheet PDF once (if the client sent one) so we can
    # attach it to every email without re-decoding per recipient.
    pdf_bytes = None
    pdf_name = (pdf_filename or "call-sheet.pdf").strip() or "call-sheet.pdf"
    if not pdf_name.lower().endswith(".pdf"):
        pdf_name += ".pdf"
    if pdf_base64:
        try:
            b64 = pdf_base64.split(",", 1)[-1]  # tolerate a data-URI prefix
            pdf_bytes = base64.b64decode(b64)
        except Exception:
            pdf_bytes = None

    async def _send_email(recipient: dict, account_id: str):
        addr = (recipient.get("email") or "").strip()
        if not addr:
            return {"channel": "email", "name": recipient.get("name"), "ok": False, "error": "no email on record"}
        subject = f"Call Sheet — Day {(cs.get('shoot') or {}).get('day_number') or '—'} · {(cs.get('shoot') or {}).get('date') or ''}".strip()
        body_html = _render_callsheet_html(cs, recipient)
        if pdf_bytes:
            # Attachments require multipart/form-data. `to` is a JSON-encoded
            # array of recipients; `is_html` is passed as a string flag.
            form = {
                "account_id": account_id,
                "to": json.dumps([{"display_name": recipient.get("name") or addr, "identifier": addr}]),
                "subject": subject,
                "body": body_html,
                "is_html": "true",
            }
            files = [("attachments", (pdf_name, pdf_bytes, "application/pdf"))]
            r = await _unipile_request("POST", "/api/v1/emails", data=form, files=files, timeout=60)
        else:
            payload = {
                "account_id": account_id,
                "to": [{"display_name": recipient.get("name") or addr, "identifier": addr}],
                "subject": subject,
                "body": body_html,
                "is_html": True,
            }
            r = await _unipile_request("POST", "/api/v1/emails", json_body=payload, timeout=45)
        if r.is_success:
            return {"channel": "email", "name": recipient.get("name"), "ok": True, "to": addr,
                    "attached": bool(pdf_bytes), "message_id": _provider_message_id(r)}
        return {"channel": "email", "name": recipient.get("name"), "ok": False, "to": addr, "error": (r.text or '')[:300], "status": r.status_code}

    async def _send_chat(recipient: dict, account_id: str, channel: str, identifier_field: str):
        ident = (recipient.get(identifier_field) or "").strip()
        if not ident:
            return {"channel": channel, "name": recipient.get("name"), "ok": False, "error": f"no {identifier_field} on record"}
        text = _render_callsheet_text(cs, recipient)
        # Unipile chat creation accepts multipart form data; this is the
        # documented shape for /api/v1/chats. attendees_ids is repeated for
        # multi-recipient chats; we send 1-1 here.
        form = {
            "account_id": account_id,
            "attendees_ids": ident,
            "text": text,
        }
        r = await _unipile_request("POST", "/api/v1/chats", data=form, timeout=45)
        if r.is_success:
            return {"channel": channel, "name": recipient.get("name"), "ok": True, "to": ident,
                    "message_id": _provider_message_id(r)}
        return {"channel": channel, "name": recipient.get("name"), "ok": False, "to": ident, "error": (r.text or '')[:300], "status": r.status_code}

    # EMAIL
    if email_account:
        acct_id = email_account.get("id") or email_account.get("account_id")
        for c in crew:
            if not (c.get("email") or "").strip():
                continue
            results.append(await _send_email(c, acct_id))
    elif "email" in channels:
        results.append({"channel": "email", "ok": False, "error": "no Gmail / Outlook account connected on Unipile"})

    # WHATSAPP
    if whatsapp_account:
        acct_id = whatsapp_account.get("id") or whatsapp_account.get("account_id")
        for c in crew:
            if not (c.get("phone") or "").strip():
                continue
            results.append(await _send_chat(c, acct_id, "whatsapp", "phone"))
    elif "whatsapp" in channels:
        results.append({"channel": "whatsapp", "ok": False, "error": "no WhatsApp account connected on Unipile"})

    # LINKEDIN / INSTAGRAM / TELEGRAM — best-effort, requires a provider_id
    # on the crew record (we don't yet collect this in the UI; treat absence
    # as a "skipped — no handle" rather than an error).
    for channel_name, account, field in [
        ("linkedin", linkedin_account, "linkedin"),
        ("instagram", instagram_account, "instagram"),
        ("telegram", telegram_account, "telegram"),
    ]:
        if not account:
            if channel_name in channels:
                results.append({"channel": channel_name, "ok": False, "error": f"no {channel_name.title()} account connected on Unipile"})
            continue
        acct_id = account.get("id") or account.get("account_id")
        for c in crew:
            if (c.get(field) or "").strip():
                results.append(await _send_chat(c, acct_id, channel_name, field))

    ok_count = sum(1 for r in results if r.get("ok"))
    fail_count = len(results) - ok_count
    if ok_count:
        metering.record("sends", ok_count)  # count only messages that actually landed
    record = {
        **base_record,
        "results": results,
        "ok_count": ok_count,
        "fail_count": fail_count,
        "status": "sent" if fail_count == 0 and ok_count > 0 else ("partial" if ok_count else "failed"),
    }
    db_set(f"callsheet-send:{sid}", record)
    db_sadd("callsheet-sends:all", sid)

    # Delivery board. Sending was never the hard part — knowing who has it at
    # 11pm is. Opening the board here means every send is tracked without the
    # caller having to remember to ask for it. A failure here must never fail a
    # send that actually went out.
    try:
        delivery.open_board(sid, crew, channels=channels,
                            shoot_day=cs.get("shoot_day", ""), date=cs.get("date", ""),
                            project_id=project_id or "")
        board = delivery.record_send(sid, results)
        record["delivery"] = {"confirmed": board["confirmed"], "total": board["total"]}
    except Exception as e:  # noqa: BLE001
        print(f"⚠️  delivery board not updated for send {sid}: {type(e).__name__}: {e}")

    if ok_count and not fail_count:
        msg = f"Sent to {ok_count} recipient(s) via Unipile."
    elif ok_count:
        msg = f"Partial send — {ok_count} delivered, {fail_count} failed. See results for detail."
    else:
        msg = f"All sends failed. See results for detail."

    return {
        "success": True,
        "send_id": sid,
        "status": record["status"],
        "message": msg,
        "ok_count": ok_count,
        "fail_count": fail_count,
        "results": results,
    }

@app.post("/callsheet/send")
async def send_callsheet(data: CallSheetSend, _=Depends(require_api_key)):
    """Legacy one-shot send. When REQUIRE_SEND_APPROVAL=1 this returns a proposal
    (requires_approval=True) instead of sending — the caller must then POST
    /callsheet/send/confirm. With approval off, it sends immediately as before."""
    cs = data.callsheet or {}
    channels = data.channels or []
    if not (cs.get("crew") or []):
        raise HTTPException(400, "callsheet has no crew to send to")
    if not channels:
        raise HTTPException(400, "at least one send channel is required")
    if _REQUIRE_SEND_APPROVAL:
        return {"success": True, "requires_approval": True,
                **_store_send_proposal(cs, channels, data.project_id, data.pdf_base64, data.pdf_filename)}
    return await _execute_callsheet_send(cs, channels, data.project_id, data.pdf_base64, data.pdf_filename)

@app.post("/callsheet/send/propose")
async def propose_callsheet_send(data: CallSheetSend, _=Depends(require_api_key)):
    """Stage a send for human approval. Returns a proposal_id and a preview of
    exactly who would be contacted on which channel. Nothing is dispatched."""
    cs = data.callsheet or {}
    channels = data.channels or []
    if not (cs.get("crew") or []):
        raise HTTPException(400, "callsheet has no crew to send to")
    if not channels:
        raise HTTPException(400, "at least one send channel is required")
    return {"success": True, **_store_send_proposal(cs, channels, data.project_id, data.pdf_base64, data.pdf_filename)}

class SendConfirm(BaseModel):
    proposal_id: str

@app.post("/callsheet/send/confirm")
async def confirm_callsheet_send(data: SendConfirm, _=Depends(require_api_key)):
    """Execute a previously-proposed send. Idempotent — re-confirming a spent
    proposal returns the original result rather than sending twice."""
    key = f"send-proposal:{data.proposal_id}"
    proposal = _raw_get(key)  # global: proposals are uuid-keyed, tenant-immune
    if not proposal:
        raise HTTPException(404, "Proposal not found or expired")
    if proposal.get("status") == "executed":
        return {"success": True, "already_executed": True, **(proposal.get("result") or {})}
    p = proposal.get("payload") or {}
    result = await _execute_callsheet_send(
        p.get("callsheet") or {}, p.get("channels") or [],
        p.get("project_id"), p.get("pdf_base64"), p.get("pdf_filename"),
    )
    proposal["status"] = "executed"
    proposal["result"] = result
    proposal["executed_at"] = now()
    _raw_set(key, proposal, ttl=_SEND_PROPOSAL_TTL_SECONDS)
    return {"success": True, **result}

# ── CREW & VENDOR ROSTER ──────────────────────────────────────────────────────
# What this company paid this person, last time. See roster.py.

class RosterUpsert(BaseModel):
    entry: dict

class RosterRef(BaseModel):
    id: str

class RosterSearch(BaseModel):
    query: Optional[str] = ""
    kind: Optional[str] = ""
    tag: Optional[str] = ""

class EngagementCreate(BaseModel):
    roster_id: str
    engagement: dict

class RosterImport(BaseModel):
    project_id: Optional[str] = None
    crew: Optional[list] = None
    production: Optional[str] = ""

class RosterFromLedger(BaseModel):
    ledger_id: Optional[str] = None
    ledger: Optional[dict] = None
    production: Optional[str] = ""

class RosterProposeRates(BaseModel):
    region: Optional[str] = "india"
    city: Optional[str] = ""
    tier: Optional[str] = "mid"
    min_engagements: Optional[int] = 2

@app.post("/roster/search")
def roster_search(data: RosterSearch = None, _=Depends(require_api_key)):
    data = data or RosterSearch()
    rows = roster.search(data.query or "", kind=data.kind or "", tag=data.tag or "")
    return {"success": True, "entries": rows, "count": len(rows)}

@app.post("/roster/upsert")
def roster_upsert(data: RosterUpsert, _=Depends(require_api_key)):
    try:
        return {"success": True, "entry": roster.upsert(data.entry)}
    except ValueError as e:
        raise HTTPException(422, str(e))

@app.post("/roster/get")
def roster_get(data: RosterRef, _=Depends(require_api_key)):
    entry = roster.get(data.id)
    if not entry:
        raise HTTPException(404, "Not on the roster")
    return {"success": True, "entry": entry}

@app.post("/roster/delete")
def roster_delete(data: RosterRef, _=Depends(require_api_key)):
    return {"success": True, "deleted": roster.delete(data.id)}

@app.post("/roster/engagement")
def roster_engagement(data: EngagementCreate, _=Depends(require_api_key)):
    """Record one job at one rate. This is what a rate history is made of."""
    try:
        return {"success": True, "engagement": roster.record_engagement(data.roster_id, data.engagement)}
    except KeyError:
        raise HTTPException(404, "Not on the roster")
    except ValueError as e:
        raise HTTPException(422, str(e))

@app.post("/roster/history")
def roster_history(data: RosterRef, _=Depends(require_api_key)):
    """What we have actually paid them — median, spread, and the direction of
    travel, with every figure traceable to one engagement."""
    try:
        return {"success": True, "history": roster.rate_history(data.id)}
    except KeyError:
        raise HTTPException(404, "Not on the roster")

@app.post("/roster/import-crew")
def roster_import_crew(data: RosterImport, _=Depends(require_api_key)):
    """Pull a project's crew into the roster. Idempotent — run it after every job."""
    crew = data.crew or []
    if not crew and data.project_id:
        crew = [c for c in (db_get(f"crew:{cid}")
                            for cid in db_smembers(f"project:{data.project_id}:crew")) if c]
    if not crew:
        raise HTTPException(422, "Pass `crew`, or a project_id that has crew on it")
    return {"success": True, **roster.import_crew(crew, production=data.production or "")}

@app.post("/roster/from-ledger")
def roster_from_ledger(data: RosterFromLedger, _=Depends(require_api_key)):
    """Record what vendors were actually paid, from a variance ledger. The join
    that makes a teardown populate the vendor history, not just the rate card."""
    ledger = data.ledger
    if not ledger and data.ledger_id:
        ledger = db_get(f"ledger:{data.ledger_id}")
    if not ledger:
        raise HTTPException(422, "Pass `ledger` or a stored `ledger_id`")
    return {"success": True, **roster.ingest_ledger(ledger, production=data.production or "")}

@app.post("/roster/propose-rates")
def roster_propose_rates(data: RosterProposeRates = None, _=Depends(require_api_key)):
    """Roster history → rate-card proposals, on the median of at least two jobs.
    Feed the accepted ones to /rates/apply-proposals."""
    data = data or RosterProposeRates()
    return {"success": True, "proposals": roster.propose_rates(
        region=data.region or "india", city=data.city or "", tier=data.tier or "mid",
        min_engagements=data.min_engagements or 2)}

# ── CALL-SHEET DELIVERY STATE ─────────────────────────────────────────────────
# Sending existed; knowing who has it did not. See delivery.py.

class DeliveryRef(BaseModel):
    send_id: str

class ConfirmRequest(BaseModel):
    send_id: str
    recipient_id: str
    token: str
    declined: Optional[bool] = False
    note: Optional[str] = ""

@app.post("/callsheet/delivery/board")
def delivery_board(data: DeliveryRef, _=Depends(require_api_key)):
    """Who has the call sheet, who has read it, who has said they'll be there —
    and, ordered by how worried to be, who to ring."""
    board = delivery.get_board(data.send_id)
    if not board:
        raise HTTPException(404, "No delivery board for that send id")
    return {"success": True, "board": board}

@app.post("/callsheet/delivery/webhook")
async def delivery_webhook(request: Request):
    """Delivery and read receipts from the messaging provider.

    Deliberately not behind the normal API key — a provider cannot send one. It
    is behind a shared secret instead, and when that secret is not configured the
    endpoint refuses rather than accepting anonymous writes: an open webhook that
    can mark arbitrary crew as having read a call sheet is not a small hole.
    """
    secret = os.getenv("WEBHOOK_SECRET", "").strip()
    if not secret:
        raise HTTPException(503, "WEBHOOK_SECRET is not configured — refusing to accept "
                                 "unauthenticated delivery events")
    supplied = request.headers.get("X-Mark-Webhook-Secret", "")
    if not hmac.compare_digest(supplied, secret):
        raise HTTPException(401, "bad webhook secret")
    try:
        event = await request.json()
    except Exception:
        raise HTTPException(400, "expected a JSON body")
    applied = delivery.apply_provider_event(event if isinstance(event, dict) else {})
    # 200 either way: a provider that gets a 4xx for an event we simply don't
    # care about will retry it forever.
    return {"success": True, "applied": applied}

@app.post("/callsheet/confirm")
def callsheet_confirm(data: ConfirmRequest):
    """A crew member's answer. No API key: the signed token in the link IS the
    credential, and it is scoped to one person on one call sheet."""
    try:
        return {"success": True, **delivery.confirm(
            data.send_id, data.recipient_id, data.token,
            declined=bool(data.declined), note=data.note or "")}
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except KeyError as e:
        raise HTTPException(404, str(e).strip("'"))

@app.get("/c/{send_id}/{recipient_id}/{token}", response_class=HTMLResponse)
def confirm_page(send_id: str, recipient_id: str, token: str):
    """The page a crew member opens from WhatsApp.

    One screen, two buttons, no login, no app. It is read on a phone at 6am by
    someone who is already late, so it carries only what they need to answer:
    which day, what date, their call time.
    """
    board = delivery.get_board(send_id)
    rec = None
    if board:
        rec = next((r for r in board["recipients"] if r["id"] == recipient_id), None)
    if not board or not rec or not delivery.token_matches(token, send_id, recipient_id):
        return HTMLResponse(_confirm_html(None, None, None, error="This link isn't valid any more."),
                            status_code=404)
    return HTMLResponse(_confirm_html(board, rec, token))

def _confirm_html(board, rec, token, *, error: str = "") -> str:
    import html as _html
    e = lambda v: _html.escape(str(v or ""))
    if error:
        body = f'<p class="err">{e(error)}</p><p class="sub">Ask the production office to resend it.</p>'
    else:
        already = rec["state"] in ("confirmed", "declined")
        call = rec.get("call_time")
        # Built outside the f-string: an apostrophe inside an f-string
        # expression is a syntax error, and "can't" is unavoidable here.
        CONFIRMED_MSG = "You're confirmed. See you there."
        DECLINED_MSG = "You said you can't make it. Production has been told."
        done_msg = DECLINED_MSG if rec["state"] == "declined" else CONFIRMED_MSG
        body = f"""
      <p class="kicker">Call sheet</p>
      <h1>{e(board.get('shoot_day') or 'Shoot day')}</h1>
      <p class="date">{e(board.get('date') or '')}</p>
      <p class="who">{e(rec['name'])}{' · ' + e(rec['role']) if rec.get('role') else ''}</p>
      {f'<p class="call">Your call: <strong>{e(call)}</strong></p>' if call else ''}
      <div id="done" class="done" {'' if already else 'hidden'}>
        <p class="ok">{e(done_msg)}</p>
      </div>
      <div id="ask" {'hidden' if already else ''}>
        <button class="yes" onclick="answer(false)">I'll be there</button>
        <button class="no" onclick="answer(true)">I can't make it</button>
        <textarea id="note" rows="2" placeholder="Anything production should know (optional)"></textarea>
      </div>
      <p id="err" class="err" hidden></p>
      <script>
      async function answer(declined) {{
        document.querySelectorAll('button').forEach(b => b.disabled = true);
        try {{
          const r = await fetch('/callsheet/confirm', {{
            method: 'POST', headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{ send_id: {json.dumps(board['sheet_id'])},
              recipient_id: {json.dumps(rec['id'])}, token: {json.dumps(token)},
              declined, note: document.getElementById('note').value }})
          }});
          if (!r.ok) throw new Error((await r.json()).detail || 'Could not save that.');
          document.getElementById('ask').hidden = true;
          const done = document.getElementById('done');
          done.querySelector('.ok').textContent = declined
            ? {json.dumps(DECLINED_MSG)} : {json.dumps(CONFIRMED_MSG)};
          done.hidden = false;
        }} catch (err) {{
          const el = document.getElementById('err');
          el.textContent = err.message + ' Try again, or ring the production office.';
          el.hidden = false;
          document.querySelectorAll('button').forEach(b => b.disabled = false);
        }}
      }}
      </script>"""
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"/>
<title>Call sheet — confirm</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#171714;color:#D5D6CE;font-family:system-ui,-apple-system,'Segoe UI',sans-serif;
  min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px;line-height:1.5}}
main{{width:100%;max-width:420px}}
.kicker{{font-size:11px;letter-spacing:.24em;text-transform:uppercase;color:#8A8B83;font-weight:700}}
h1{{font-size:34px;line-height:1.05;margin:10px 0 4px;font-weight:800;letter-spacing:-.01em}}
.date{{color:#B3B3B3;font-size:17px}}
.who{{margin-top:22px;font-size:19px;font-weight:600}}
.call{{margin-top:6px;color:#B3B3B3;font-size:17px}}
.call strong{{color:#D5D6CE;font-size:22px}}
button{{display:block;width:100%;margin-top:14px;padding:20px;font-size:18px;font-weight:700;
  font-family:inherit;border:1px solid #D5D6CE;background:#D5D6CE;color:#171714;cursor:pointer;
  border-radius:2px}}
button.no{{background:transparent;color:#D5D6CE}}
button:disabled{{opacity:.5}}
#ask{{margin-top:26px}}
textarea{{width:100%;margin-top:14px;padding:12px;font-family:inherit;font-size:16px;
  background:#1D1D19;color:#D5D6CE;border:1px solid #2E2E29;border-radius:2px}}
.done{{margin-top:26px;padding:18px;border-left:2px solid #D5D6CE;background:#1D1D19}}
.ok{{font-size:18px;font-weight:600}}
.err{{margin-top:18px;color:#E8724F;font-size:16px}}
.sub{{margin-top:8px;color:#8A8B83;font-size:15px}}
[hidden]{{display:none!important}}
</style></head><body><main>{body}</main></body></html>"""

@app.post("/crew/enrich")
async def enrich_crew_member(data: CrewEnrich, _=Depends(require_api_key)):
    member = db_get(f"crew:{data.crew_id}")
    if not member:
        raise HTTPException(404, "Crew member not found")
    project = db_get(f"project:{member['project_id']}")
    if not project:
        raise HTTPException(404, "Project not found for this crew member")

    enrichment = await _flue_call(
        "enrich-crew",
        data.crew_id,
        {"crew_member": member, "project": project},
    )
    if isinstance(enrichment, dict) and set(enrichment.keys()) == {"result"}:
        enrichment = enrichment["result"]
    return {"success": True, "crew_id": data.crew_id, "enrichment": enrichment}

# ── CLAUDE PROXY ──────────────────────────────────────────────────────────────

class ClaudeRequest(BaseModel):
    system: str
    user: str
    max_tokens: Optional[int] = 3000

@app.post("/claude")
async def claude_proxy(data: ClaudeRequest, request: Request, _=Depends(require_api_key)):
    check_rate_limit(request.client.host)
    # Prefer the tenant's own Anthropic connection; fall back to the process env.
    _anthropic_conn = connections.get_connection("anthropic") or {}
    api_key = _anthropic_conn.get("api_key") or os.getenv("ANTHROPIC_API_KEY")
    if not api_key or api_key == "your-api-key-here":
        raise HTTPException(500, "No Anthropic key — connect one via /connections/set or set ANTHROPIC_API_KEY")
    _model = "claude-sonnet-4-6"
    async with observability.trace("claude:proxy", model=_model, input={"system": data.system, "user": data.user}) as span:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": _model,
                    "max_tokens": data.max_tokens,
                    "system": data.system,
                    "messages": [{"role": "user", "content": data.user}],
                },
            )
        if not resp.is_success:
            try:
                detail = resp.json().get("error", {}).get("message", "Claude API error")
            except Exception:
                detail = f"Claude API error (HTTP {resp.status_code})"
            span["ok"] = False
            span["error"] = str(detail)[:200]
            raise HTTPException(resp.status_code, detail)
        body = resp.json()
        # The Anthropic Messages API surfaces token counts — capture them so the
        # trace carries real cost signal (the Flue path can't; usage isn't
        # forwarded through session.prompt()).
        span["usage"] = body.get("usage")
        span["output"] = "".join(b.get("text", "") for b in body.get("content", []) if isinstance(b, dict))
        return body

# ── SERVE FRONTEND ────────────────────────────────────────────────────────────
_frontend_dir = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'frontend')
)

if os.path.exists(_frontend_dir):
    app.mount("/static", StaticFiles(directory=_frontend_dir), name="static")

    @app.get("/")
    def serve_landing():
        return FileResponse(os.path.join(_frontend_dir, "index.html"))

    @app.get("/app")
    @app.get("/app.html")
    def serve_app():
        return FileResponse(os.path.join(_frontend_dir, "app.html"))

    @app.get("/budget.html")
    def serve_budget():
        return FileResponse(os.path.join(_frontend_dir, "budget.html"))
