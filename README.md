# Mark

An AI line producer for film, TVC, and music video productions. Upload a script, answer six smart questions, and Mark builds a full itemised production budget.

## Layout

```
mark/
├── backend/        FastAPI service (Python). REST endpoints, Redis storage,
│                   PDF script parser, Flue agent proxy, feedback capture.
│                   observability.py — agent-run tracing (Langfuse/Redis/stdout).
│                   screenplay.py— parser output → summary + per-scene list.
│                   ratecard.py  — the per-tenant rate library (+ seeds/).
│                   schedule.py  — scenes → shooting schedule + Day-out-of-Days.
│                   variance.py  — budget vs actuals → the teardown ledger.
│                   compliance.py— India GST + TDS per line, payment schedule.
│                   budgetdiff.py— what moved between two budget versions.
│                   exporters.py — xlsx out, Movie Magic interchange, xlsx in.
│                   teardown_report.py — the Stage 0 client document.
│                   delivery.py  — who has the call sheet, and who confirmed.
│                   roster.py    — crew & vendors, and what we actually paid them.
│                   ca_review_pack.py — prints every tax assumption for a CA.
├── frontend/       Static HTML + vanilla JS. The producer-facing UI:
│                   budget.html    — region picker, script upload, question flow,
│                                    budget render, export, feedback widget.
│                   schedule.html  — stripboard, drag between days, DooD.
│                   rates.html     — the rate library, editable in place.
│                   teardown.html  — budget vs actuals, ledger, Stage 0 report.
│                   delivery.html  — who hasn't confirmed, with tap-to-call.
│                   callsheet.html — call sheet builder and send.
│                   assets/mark-api.js + mark-tool.css — shared client + chrome.
├── flue-agents/    TypeScript agent harness (Flue). Webhook agents:
│                   generate-budget, refine-budget, refine-callsheet,
│                   render-callsheet-template, enrich-crew. Skills as markdown.
├── evals/          Budget regression gate — invariants + golden fixtures + CI,
│                   plus offline unit suites for the four modules above.
│                   `python evals/run_evals.py && python evals/run_unit_tests.py`
└── mcp/            MCP server — exposes Mark's tools to Claude/Cursor/agents.
```

Each directory has its own README with detail.

## The compounding layer

The three builds that turn a service into a system. Each is pure logic in its own
module with a thin endpoint, and each is covered offline in `evals/`.

- **Rate library** (`ratecard.py`) — rates by `region · city · tier · item_key`,
  per tenant. `resolve_pack()` hands the budget agent the rates it must use
  verbatim and cite; a verified rate is binding, an unverified seed is marked
  amber. A rate for a *different* named city is never substituted. Endpoints:
  `/rates/list|upsert|delete|seed|pack|apply-proposals`.
  Seeds in `backend/seeds/` are market-reference placeholders — they load with
  `verified_at: null` and must be replaced from a teardown before a number
  reaches a client.
- **Schedule** (`schedule.py`) — `/script/parse` now also returns a per-scene
  list (built in `screenplay.py`, which was split out of `main.py` so the parser
  is covered offline too), which becomes strips, shooting days (locations kept contiguous, night
  work pushed later), a Day-out-of-Days with hold days, and a company-move count.
  `shoot_days` stops being a typed answer and becomes a computed number that can
  be reconciled against the budget. Endpoints: `/schedule/generate|get|reconcile`
  and `/callsheet/from-schedule`, which seeds a call sheet from one day and
  leaves call times, weather and hospital under `needs` rather than inventing them.
- **Variance ledger** (`variance.py`) — the Stage 0 teardown as a feature.
  Approved budget + a CSV/xlsx cost report → every line beyond the threshold,
  classified (estimate error · scope change · vendor variance · unrecorded cost)
  with the evidence for each call. Across two or more productions it finds the
  lines that are wrong in the same direction every time and annualises them,
  stating its method. Endpoints: `/actuals/parse`, `/variance/compute`,
  `/teardown/compute`.
  The output feeds `ratecard.propose_from_variance()`, which closes the loop:
  actuals → proposed rates → a better next budget.
- **Versions, diff and interop** (`budgetdiff.py`, `exporters.py`) — budgets were
  already versioned; `/budget/versions` lists them and `/budget/diff` says what
  moved (repriced · added · removed · reworded at the same price) with the money
  on each. `/budget/export` writes a real .xlsx with nothing but `zipfile`, or a
  **Movie Magic interchange file** — a delimited account/detail import, *not* a
  `.mmb`: that format is proprietary and undocumented, and a wrong one opens with
  wrong numbers. `/budget/import` reads a client's own spreadsheet back in and
  reports every row it skipped.
- **The Stage 0 report** (`teardown_report.py`) — `/teardown/report` renders
  D1/D3/D4 of the SOW as printable HTML from the ledgers. Every figure traces to a
  ledger line, and the document states what it cannot support instead of filling
  the gap. Print → Save as PDF; no PDF library, no headless browser.
- **Call-sheet delivery** (`delivery.py`) — sending existed; knowing who has it
  did not. Every send opens a board: per-recipient state (queued → sent →
  delivered → read → confirmed), a chase list ordered by how worried to be, and a
  one-tap confirmation page at `/c/{send_id}/{recipient}/{token}` that needs no
  login and works over any channel that carries a URL. Delivery and read state
  come from the provider and are marked unverified until a real webhook has been
  seen; confirmation is ours and is reliable. Endpoints:
  `/callsheet/delivery/board|webhook`, `/callsheet/confirm`.
- **Crew & vendor roster** (`roster.py`) — the answer to "what did we pay him
  last time". People and vendors at tenant level, deduplicated on phone then
  email then exact name, with an engagement per job. Rate history reports the
  median rather than the mean and says when it is one observation.
  `import_crew()` is idempotent, `ingest_ledger()` records what vendors were
  actually paid from a teardown, and `propose_rates()` feeds the rate library off
  at least two engagements. Endpoints under `/roster/*`.
- **India compliance** (`compliance.py`) — the local equivalent of the US fringe
  engine. Per line: gross, GST, TDS section and rate, deduction (on the pre-GST
  value), net payable; plus blocked GST input credit and an advance/balance
  payment schedule. Endpoints: `/compliance/compute|payment-schedule`.
  **Indicative only, and gated.** Every response carries `reviewed: false` until
  `TAX_RULES_REVIEWED=1` is set after a chartered accountant signs off the tables.
  `python3 backend/ca_review_pack.py > docs/ca-review-pack.md` generates the
  review document from the code, so the pack and the rules cannot drift apart.

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
  MCP tools so any agent can drive Mark, plus the compounding layer:
  `list_rates`, `upsert_rate`, `rate_pack`, `generate_schedule`,
  `callsheet_from_schedule`, `variance_ledger`, `teardown`, `india_compliance`
  and `payment_schedule`.

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
