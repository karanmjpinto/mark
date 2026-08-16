# Mark

An AI line producer for film, TVC, and music video productions. Upload a script, answer six smart questions, and Mark builds a full itemised production budget.

## Layout

```
mark/
├── backend/        FastAPI service (Python). REST endpoints, Redis storage,
│                   PDF script parser, Flue agent proxy, feedback capture.
│                   observability.py — agent-run tracing (Langfuse/Redis/stdout).
├── frontend/       Static HTML + vanilla JS. The producer-facing UI:
│                   region picker, script upload, question flow, budget render,
│                   export to Excel/PDF, feedback widget.
├── flue-agents/    TypeScript agent harness (Flue). Webhook agents:
│                   generate-budget, refine-budget, refine-callsheet,
│                   render-callsheet-template, enrich-crew. Skills as markdown.
├── evals/          Budget regression gate — invariants + golden fixtures + CI.
│                   `python evals/run_evals.py`
└── mcp/            MCP server — exposes Mark's tools to Claude/Cursor/agents.
```

Each directory has its own README with detail.

## Robustness & agent-native layer

Added on top of the core pipeline (see `architecture.html` for the diagram):

- **Tracing** — every agent call (Flue + `/claude`) is a span with model,
  latency, tokens (Claude path), cache-hit, and errors. Sinks: Langfuse if
  `LANGFUSE_*` set, else a Redis ring buffer + stdout. Read via `POST /admin/traces`.
- **Evals** — `evals/` runs invariant checks (gst is decimal, required post
  sections, post:production ratio, reconciliation) against golden budgets. Runs
  offline in CI (`.github/workflows/evals.yml`); `--live` validates real agent
  output after a prompt/rate-card change.
- **Send approval** — `/callsheet/send/propose` → (human approves preview) →
  `/callsheet/send/confirm`. Set `REQUIRE_SEND_APPROVAL=1` to gate the legacy
  `/callsheet/send` too. Confirm is idempotent.
- **Durable async budgets** — `/budget/generate/async` returns a `job_id`
  (202); poll `/jobs/get`. Job state lives in Redis; the async path requests
  Sonnet (`ASYNC_BUDGET_MODEL`) since it isn't bound by the client edge timeout.
- **MCP** — `mcp/server.py` exposes generate/refine/render/propose/confirm as
  MCP tools so any agent can drive Mark.

## Scale layer (multi-tenant)

All backward compatible and env-gated — `AUTH_MODE=off` (default) keeps the
single-tenant demo running with zero config.

- **Multi-tenancy** (`tenancy.py`) — `AUTH_MODE`:
  - `off` — single "public" tenant (today's behaviour; legacy `API_KEY` still honored).
  - `apikey` — per-tenant keys via `TENANT_REGISTRY` (env JSON:
    `{"<key>": {"tenant": "...", "plan": "..."}}`). Caller sends `X-Tenant-Key`.
  - `jwt` — verifies a bearer JWT against `AUTH_JWKS_URL` (Clerk/Auth0/OIDC),
    tenant from `AUTH_TENANT_CLAIM` (default `org_id`). Needs `pyjwt[crypto]`.
  Every `db_*` key is namespaced `t:{tenant}:…`, so per-tenant data is isolated
  with no per-endpoint changes. Global data (determinism cache, uuid-keyed jobs
  and send proposals) stays shared by design.
- **Connections** (`connections.py`) — per-tenant provider credentials
  (`/connections/set|list|status|delete`), encrypted at rest with Fernet when
  `CONNECTIONS_SECRET` is set. Secrets are never returned to the client; the
  Unipile send path and `/claude` proxy prefer the tenant's connection, falling
  back to env.
- **Metering** (`metering.py`) — per-tenant, per-month counters for `budgets` and
  `sends`, with plan quotas (`PLAN_QUOTAS` env JSON, or defaults). Enforcement is
  gated by `METERING_ENABLED` (off by default, so the demo is never blocked);
  a tenant checks their usage via `POST /usage`.
- **Datastore** — tenant-namespaced Redis today (deployable now). The `db_*`
  layer is the single choke point; a Postgres migration reimplements `db_*`/`tkey`
  against `(tenant_id, …)` rows with no endpoint changes. That migration is a
  separate project, not done here.

## Run end-to-end (local)

Three processes, three terminals:

```bash
# 1. Flue agents (port 3583)
cd flue-agents
cp .env.example .env          # fill ANTHROPIC_API_KEY
npm install
npm run dev

# 2. FastAPI backend (port 8000)
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=…
export FLUE_BASE_URL=http://localhost:3583
uvicorn main:app --reload --port 8000

# 3. Frontend — open with the API pointed at localhost:
#    http://localhost:8000/budget.html
#    or, if serving the HTML elsewhere: ?api=http://localhost:8000
```

## Pipeline

1. **Upload script** → `POST /script/parse` runs `screenplay-pdf-to-json`,
   returns scene counts, INT/EXT/day/night, top locations and characters.
2. **Generate questions** → AI generates six contextual questions from the
   parsed breakdown.
3. **Answer questions** → frontend captures `qa: [{id, question, answer, options}]`
   pairs (full question text preserved, not just `q1: "Mumbai"`).
4. **Generate budget** → `POST /budget/generate` forwards `{script, region,
   currency, qa, breakdown}` to the Flue `generate-budget` agent. Agent returns
   a section-coded budget (10000–14000 codes) with line items, GST, confidence
   markers, and flags.
5. **Feedback** → `POST /feedback/create` captures thumbs + comment + the
   full budget snapshot for quality review.

## Deploy

- **Backend** → Railway (Dockerfile included). Set `ANTHROPIC_API_KEY`,
  `FLUE_BASE_URL`, `API_KEY` (optional shared secret), `ALLOWED_ORIGINS`,
  `REDIS_HOST`/`REDIS_PORT`.
  Optional new toggles: `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` (richer
  tracing), `REQUIRE_SEND_APPROVAL=1` (gate call-sheet sends),
  `ASYNC_BUDGET_MODEL` (default `anthropic/claude-sonnet-4-6`),
  `JOB_TTL_SECONDS`, `SEND_PROPOSAL_TTL_SECONDS`.
  Scale layer: `AUTH_MODE` (off|apikey|jwt), `TENANT_REGISTRY`,
  `AUTH_JWKS_URL`/`AUTH_TENANT_CLAIM`/`AUTH_AUDIENCE` (jwt mode),
  `CONNECTIONS_SECRET` (encrypt connection creds), `METERING_ENABLED`,
  `PLAN_QUOTAS`, `DEFAULT_PLAN`.
- **Flue agents** → `npm run build && node dist/server.mjs` on any Node host.
  Set `ANTHROPIC_API_KEY` and `PORT`.
- **Frontend** → static hosting (Vercel, Netlify, GitHub Pages, or served
  directly from FastAPI when both run on one host).
