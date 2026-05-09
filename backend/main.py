"""
AskMark Backend — Complete FastAPI Server
Run with: uvicorn main:app --reload --port 8000
"""

from fastapi import FastAPI, HTTPException, Request, Depends, UploadFile, File
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from typing import Optional
import uuid
import json
import os
import io
import redis as redis_lib
from datetime import datetime, timezone
import httpx
import time
import hashlib
import collections
from dotenv import load_dotenv

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

_mem = {}

def db_set(key, value):
    if r:
        r.set(key, json.dumps(value))
    else:
        _mem[key] = value

def db_get(key):
    if r:
        raw = r.get(key)
        return json.loads(raw) if raw else None
    return _mem.get(key)

def db_delete(key):
    if r: r.delete(key)
    elif key in _mem: del _mem[key]

def db_sadd(key, val):
    if r: r.sadd(key, val)
    else: _mem.setdefault(key, set()).add(val)

def db_srem(key, val):
    if r: r.srem(key, val)
    elif key in _mem: _mem[key].discard(val)

def db_smembers(key):
    if r: return r.smembers(key)
    return _mem.get(key, set())

def now():
    return datetime.now(timezone.utc).isoformat()

# ── AUTH ──────────────────────────────────────────────────────────────────────
# Set API_KEY env var to enforce a shared secret on all endpoints.
# If unset, auth is disabled (open access — suitable for local dev only).
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

async def require_api_key(key: Optional[str] = Depends(_api_key_header)):
    server_key = os.getenv("API_KEY")
    if server_key and key != server_key:
        raise HTTPException(401, "Invalid or missing API key")

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
    version: Optional[str] = "1.0"

class CallSheetRefine(BaseModel):
    callsheet: dict
    instruction: str

class CallSheetSave(BaseModel):
    callsheet: dict
    project_id: Optional[str] = None

class CallSheetSend(BaseModel):
    callsheet: dict
    channels: list  # ["email", "whatsapp"]
    project_id: Optional[str] = None

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
# Uses SMASH-CUT/screenplay-pdf-to-json to turn a PDF screenplay into structured
# scene/location/INT-EXT/character data. The agent uses this as ground truth so
# it doesn't have to re-extract structure from prose every call.

def _iter_scenes(pages: list):
    """
    Yield (scene_info, snippets) for every scene block in a parsed screenplay.
    The actual parser shape is:
        [{ "page": int, "content": [{ "scene_info": {...}|None, "scene": [...] }, ...], "type"?: "FIRST_PAGES" }]
    The README documents a flatter shape — it is wrong. Verified against parser output.
    """
    for page in pages or []:
        if page.get("type") == "FIRST_PAGES":
            continue
        for block in page.get("content", []) or []:
            if not isinstance(block, dict):
                continue
            yield block.get("scene_info") or None, block.get("scene") or []

# Caps on summary lists — top-N by scene_count keeps Claude input-token cost bounded.
# Long-tail one-line characters and one-off locations don't drive budget decisions.
_MAX_LOCATIONS = 25
_MAX_CHARACTERS = 30

def _process_pages(pages: list) -> tuple[dict, str]:
    """
    Single pass over the parser output: builds both the compact summary
    AND reconstructs plain script text. Avoids two traversals of a large structure.
    """
    int_count = ext_count = day_count = night_count = total_scenes = 0
    locations: dict = {}
    characters: dict = {}
    text_chunks: list = []

    for scene_info, snippets in _iter_scenes(pages):
        if scene_info:
            total_scenes += 1
            region = (scene_info.get("region") or "").upper()
            if "INT" in region:
                int_count += 1
            if "EXT" in region:
                ext_count += 1
            for t in scene_info.get("time") or []:
                tu = (t or "").upper()
                if any(k in tu for k in ("DAY", "MORNING", "AFTERNOON")):
                    day_count += 1
                if any(k in tu for k in ("NIGHT", "EVENING", "DUSK")):
                    night_count += 1
            loc = scene_info.get("location")
            if loc:
                locations[loc] = locations.get(loc, 0) + 1
            heading = (
                f"{scene_info.get('region','')} {scene_info.get('location','') or ''}"
                f" - {' / '.join(scene_info.get('time') or [])}"
            ).strip(" -")
            if heading:
                text_chunks.append(heading)
        for snippet in snippets:
            content = snippet.get("content")
            if snippet.get("type") == "CHARACTER" and isinstance(content, dict):
                name = content.get("character")
                if name:
                    characters[name] = characters.get(name, 0) + 1
            if isinstance(content, str):
                text_chunks.append(content)
            elif isinstance(content, dict):
                for v in content.values():
                    if isinstance(v, str):
                        text_chunks.append(v)
                    elif isinstance(v, list):
                        text_chunks.extend(x for x in v if isinstance(x, str))
            elif isinstance(content, list):
                text_chunks.extend(x for x in content if isinstance(x, str))

    summary = {
        "total_scenes": total_scenes,
        "int_count": int_count,
        "ext_count": ext_count,
        "day_count": day_count,
        "night_count": night_count,
        "unique_locations": [
            {"name": k, "scene_count": v}
            for k, v in sorted(locations.items(), key=lambda kv: kv[1], reverse=True)[:_MAX_LOCATIONS]
        ],
        "characters": [
            {"name": k, "scene_count": v}
            for k, v in sorted(characters.items(), key=lambda kv: kv[1], reverse=True)[:_MAX_CHARACTERS]
        ],
    }
    return summary, "\n".join(c for c in text_chunks if c)

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

# ── FLUE AGENT PROXY ──────────────────────────────────────────────────────────
# Forwards questionnaire answers + project context to the Flue sidecar so the
# agent can produce a typed budget. Set FLUE_BASE_URL to the running Flue server
# (default http://localhost:3583 in dev, or the deployed Node URL in prod).

_FLUE_BASE_URL = os.getenv("FLUE_BASE_URL", "http://localhost:3583")

# ── BUDGET ENGINE GUARDRAILS ──────────────────────────────────────────────────
# Same input → same output. Krish's biggest complaint from the test runs was
# that running NIKE TVC twice with identical answers produced ₹1.95Cr vs
# ₹3.21Cr. LLM determinism is impossible at temp>0, so we hash the inputs and
# cache the result. TTL = 7 days (long enough for a producer to share a link
# with their team and have it look identical, short enough that a tweaked
# prompt or rate card eventually invalidates).
_BUDGET_CACHE_TTL_SECONDS = 7 * 24 * 3600

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
        raise HTTPException(resp.status_code, f"flue/{agent_name}: {detail}")
    return resp.json()

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
        payload["breakdown"] = data.breakdown

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
    else:
        agent_result = await _flue_call("generate-budget", run_id, payload)
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

@app.post("/callsheet/send")
async def send_callsheet(data: CallSheetSend, _=Depends(require_api_key)):
    """V1 mocked send. Logs intent + recipients to Redis so we can audit who
    *would* have received what. Real Gmail OAuth + WhatsApp Business API
    wiring is a Phase-2 lift; the UI surfaces the mocked status."""
    cs = data.callsheet or {}
    crew = cs.get("crew") or []
    channels = data.channels or []
    if not crew:
        raise HTTPException(400, "callsheet has no crew to send to")
    if not channels:
        raise HTTPException(400, "at least one send channel is required")

    # Filter recipients by channel — email needs an email; WhatsApp needs a phone.
    email_recipients = [c for c in crew if (c.get("email") or "").strip()] if "email" in channels else []
    whatsapp_recipients = [c for c in crew if (c.get("phone") or "").strip()] if "whatsapp" in channels else []

    sid = str(uuid.uuid4())
    record = {
        "id": sid,
        "project_id": data.project_id,
        "channels": channels,
        "shoot_date": (cs.get("shoot") or {}).get("date"),
        "shoot_day": (cs.get("shoot") or {}).get("day_number"),
        "unit_call": (cs.get("shoot") or {}).get("unit_call"),
        "wrap_time": (cs.get("shoot") or {}).get("wrap_time"),
        "email_count": len(email_recipients),
        "whatsapp_count": len(whatsapp_recipients),
        "crew_total": len(crew),
        "status": "mocked",   # flips to "sent" once real integrations land
        "created_at": now(),
    }
    db_set(f"callsheet-send:{sid}", record)
    db_sadd("callsheet-sends:all", sid)

    parts = []
    if email_recipients:
        parts.append(f"{len(email_recipients)} via email")
    if whatsapp_recipients:
        parts.append(f"{len(whatsapp_recipients)} via WhatsApp")
    msg = "Mocked send logged: " + ", ".join(parts) + ". Gmail / WhatsApp integration is the next deploy."

    return {
        "success": True,
        "send_id": sid,
        "status": "mocked",
        "message": msg,
        "recipients": {
            "email": [r.get("email") for r in email_recipients],
            "whatsapp": [r.get("phone") for r in whatsapp_recipients],
        },
    }

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
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key or api_key == "your-api-key-here":
        raise HTTPException(500, "API key not configured on server")
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-6",
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
        raise HTTPException(resp.status_code, detail)
    return resp.json()

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
