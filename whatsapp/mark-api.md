# Mark API — reference for the WhatsApp agent

Paste this whole file to Cody after the brief. It is the only API the agent
needs.

## Conventions

- Base URL: the `MARK_API_BASE` variable. Production:
  `https://backend-production-6ea4.up.railway.app`.
- Auth: header `X-API-Key: {{MARK_API_KEY}}` on **every** call.
- Every endpoint is `POST` with a JSON body, except the file uploads —
  `/script/parse`, `/actuals/parse`, `/budget/import`, `/template/parse` — which
  are multipart with one field, `file`, and `GET /s/{id}/{token}`, which is a
  public page needing no key.
- Every response carries `success`. Errors are standard HTTP with
  `{"detail": "..."}`: `403` not enrolled / wrong role, `404` nothing saved yet,
  `422` the input can't support the answer, `503` a provider isn't connected.
  Read `detail` to the producer in plain words; don't retry a `4xx`.
- A *budget* means the `budget_data` shape: `{title, production_type,
  shoot_days, scale_tier, locations[], sections[{code, name, type,
  items[{code, desc, sub, amount, gst_rate, conf}]}], excluded[], flags[]}`.
  `conf` is `green` (verified rate) / `amber` (market placeholder) / `red` (no
  basis). There is no stored total — it is the sum of every item's `amount`.

---

## Identity and context — call first, every message

### `POST /chat/session`
`{"phone": "<the sender's WhatsApp number, any format>"}`

→ `{"success": true, "session": {"phone": "+919820012345", "name": "Karan",
"role": "producer", "can": ["read","send","write"], "active_project": "<id|null>",
"last_seen": "...", "messages": 41}, "project": {"id", "name", "project_type",
"currency", "status"} | null}`

`403` means this number is not enrolled — refuse and stop. `can` gates
everything: `write` to change anything, `send` to release a call sheet.

### `POST /chat/active-project`
`{"phone", "project_id"}` — pins the thread to one production, so "what's the
budget" is unambiguous. `project_id: null` unpins. Needs `write`.

### `POST /chat/render` — numbers at message size
`{"phone", "kind", "project_id"?, "payload"?, "ledger_id"?, "send_id"?,
"productions_per_year"?, "share"?}`

`kind` is one of `budget` · `schedule` · `variance` · `teardown` · `delivery`.
Reads what is stored for the pinned project; pass `payload` instead for
something just generated and not yet saved. `kind: delivery` requires `send_id`.

→ `{"success": true, "kind": "budget", "text": "<send this verbatim>",
"chars": 463, "share": {"url": "https://…/s/<id>/<token>", "expires_at": "…"} | null,
"share_error": null, "project_id": "…"}`

Send `text` as the message and `share.url` as a link. If `share` is null and
`share_error` is set, say the document couldn't be built — the numbers are still
right.

### Roster (admin, not usually the agent's job)
- `POST /chat/operators/enrol` `{"phone", "name", "role"}` — `viewer` |
  `coordinator` | `producer`.
- `POST /chat/operators/list` `{}` · `POST /chat/operators/revoke` `{"phone"}`.

---

## Projects

- `POST /projects/list` `{}` → `{"projects": [...]}`
- `POST /projects/create` `{"name", "project_type": "tvc"|"music_video"|"film",
  "client_name", "currency": "INR", "shoot_start_date", "brief"}` →
  `{"project": {"id", ...}}`
- `POST /projects/get` · `POST /projects/update` · `POST /projects/dashboard`
  — all `{"project_id"}`.

## Script → budget

### `POST /script/parse` (multipart)
Field `file`, a PDF only, max a few MB. → `{"summary": {...}, "scenes": [...]}`.
`summary` goes to `/budget/generate` as `breakdown`; `scenes` goes to
`/schedule/generate` as `scenes`.

### `POST /budget/generate`
`{"project_id"?, "script"?, "breakdown"?, "region": "india", "city": "mumbai",
"tier": "mid"|"premium"|"low", "qa": [{"id","question","answer"}],
"currency": {"code":"INR","symbol":"₹"}, "use_rates": true}`

→ `{"budget_id"?, "budget": {"budget_data": {...}}}`

**Always pass `city`.** The rate library resolves on region · city · tier, and a
rate for a different named city is never substituted — a missing city costs
verified rates. Takes 30–90s; the agent should say it's working. For the slower,
better model: `POST /budget/generate/async` with the same body →
`{"job_id", "status": "queued", "poll_endpoint": "/jobs/get"}`, then poll
`POST /jobs/get` `{"job_id"}` every ~10s. `job.status` is
`queued|running|done|error`; when `done`, `job.result` holds exactly what
`/budget/generate` would have returned.

### `POST /budget/refine`
`{"project_id"? | "budget", "instruction": "<the producer's words, verbatim>"}`
→ `{"budget": {...}}`. Don't paraphrase the instruction.

### `POST /budget/save`
`{"project_id", "budget_data", "version": "1.1"}` → `{"budget_id"}`.
`version` is a **string**.

### `POST /budget/get` · `POST /budget/history` · `POST /budget/versions`
`{"project_id"}`. `versions` lists what can be diffed.

### `POST /budget/diff`
`{"project_id", "before", "after"}` (version ids from `/budget/versions`) →
`{"diff": {...}}` — repriced · added · removed · reworded, with the money on each.

### `POST /budget/export`
`{"project_id"? | "budget", "currency": "INR", "format": "xlsx"|"mm"|"csv"}`
→ xlsx: `{"filename", "content_type", "base64"}` — send as a WhatsApp document.
`mm` returns `{"filename", "text"}`: a delimited import file for Movie Magic,
**not** a `.mmb`. Say that when you send it.

### `POST /budget/import` (multipart)
Field `file`, `.xlsx` only — a client's own budget spreadsheet read into Mark's
shape. → `{"budget": {...}}`, whose `flags` list every row it skipped. Read the
skipped count out loud; `422` means nothing importable was found.

## Rates

- `POST /rates/list` `{"region","city","tier"}` → `{"rates": [...]}`; each has
  `verified_at` — `null` means placeholder.
- `POST /rates/pack` `{"region","city","tier"}` → `{"pack", "coverage"}`.
  `coverage` is the share of lines with a verified rate; quote it when a
  producer asks how much to trust a budget.
- `POST /rates/upsert` `{"rate": {"region","city","tier","item_key","amount",
  "unit","source"}}` — the same identity tuple corrects in place.
- `POST /rates/apply-proposals` `{"proposals": [...]}` — from a teardown. **Ask
  before calling it**: it changes every future budget.

## Schedule

- `POST /schedule/generate` `{"project_id", "scenes"? | "breakdown"?,
  "shoot_days"?, "start_date"?, "save": true}` → `{"schedule": {...}}`.
  `shoot_days` is binding when supplied. With only a `breakdown`, the schedule
  is synthetic and says so — never present it as a parsed script.
- `POST /schedule/get` · `POST /schedule/save` `{"project_id","days":[[scene,…],…]}`
  (a producer moving strips between days).
- `POST /schedule/reconcile` `{"project_id"}` → does the budget pay for the
  number of days the script implies.

## Call sheets

- `POST /callsheet/from-schedule` `{"project_id","day": 3}` →
  `{"callsheet": {...}}`. It is **partial by design**: whatever sits under
  `needs` (call times, weather, nearest hospital) must be asked for, never
  invented.
- `POST /callsheet/refine` `{"callsheet","instruction"}` →
  `{"callsheet", "revision_notes"}`.
- `POST /callsheet/render-template` `{"callsheet", "template_text"}` — render
  into a producer's own layout. `template_text` comes from
  `POST /template/parse` (multipart `file`: .docx/PDF/txt of their usual sheet).
- `POST /callsheet/save` `{"project_id","callsheet"}` → `{"callsheet_id"}` ·
  `POST /callsheet/get` `{"callsheet_id"}`.

### Sending — two steps, always
1. `POST /callsheet/send/propose` `{"callsheet", "channels": ["whatsapp","email"],
   "project_id"}` → `{"proposal_id", "preview": …}`. **Nothing is sent.** Read
   the preview back: how many people, which channel each, and who has no
   contact detail. The proposal expires — `expires_in_seconds` says when.
2. Wait for the producer to say yes in words.
3. `POST /callsheet/send/confirm` `{"proposal_id"}` → `{"send_id", "status",
   "results"}`. Idempotent — re-confirming returns the original result instead
   of sending twice. Keep `send_id`.

Requires the `send` role. A coordinator gets step 1 and a refusal at step 3.

### `POST /chat/render` `kind: delivery`
`{"phone","kind":"delivery","send_id"}` → who has it, who has confirmed, and the
chase list ordered by how worried to be. Delivery/read state is the provider's
claim; confirmation is Mark's. The text says so — keep that line in.

## What it actually cost

- `POST /actuals/parse` (multipart `file`: CSV or xlsx cost report) →
  `{"rows", "count", "total"}`.
- `POST /variance/compute` `{"project_id", "budget"?, "actuals"? |
  "actuals_csv"?, "threshold": 0.1, "currency": "INR", "production": "...",
  "save": true}` → the ledger: every line beyond the threshold, classified
  (`estimate_error` · `scope_change` · `vendor_variance` · `unrecorded_cost`)
  with the evidence for each call. **Always build the ledger here** — a
  hand-assembled one cannot render the Stage 0 report.
- `POST /chat/render` `kind: variance` → the ledger as a message + the report link.
- `POST /chat/render` `kind: teardown` `{"productions_per_year": 6}` → across
  every saved ledger: the lines wrong in the same direction every time, the
  annualised cost, and a Stage 0 report link. Needs two or more productions to
  say anything about a pattern.

## Crew, vendors and what we paid them

- `POST /roster/search` `{"query","kind","tag"}` → `{"entries", "count"}` with
  engagement counts and median rates.
- `POST /roster/history` `{"id"}` (the roster entry id from `/roster/search`) → `{"history"}`. It reports the **median**, and
  says when it rests on one observation — quote that caveat.
- `POST /roster/upsert` · `/roster/get` · `/roster/engagement` ·
  `/roster/import-crew` `{"crew","production"}` (idempotent) ·
  `/roster/from-ledger` `{"ledger_id"}` (records what vendors were actually paid).
- `POST /roster/propose-rates` `{"id"?}` (omit for every entry) → rate proposals from real engagements.
- `POST /crew/enrich` `{"crew_id"}` — contact/role lookup for a stored crew member.

## India compliance — indicative, and gated

- `POST /compliance/compute` `{"project_id"? | "budget", "payee_types":
  {"<line code>": "individual"|"entity"}}` → per line: gross, GST, TDS section
  and rate, deduction on the pre-GST value, net payable, blocked input credit.
- `POST /compliance/payment-schedule` — advance/balance split with tax resolved
  at each stage.

Both carry `reviewed: false` until a chartered accountant has signed off the
tables. Say "indicative, not yet CA-reviewed" **in the same message** as any
number from here. Never let it be forwarded to a client as advice.

## Housekeeping

- `GET /health` — no key needed.
- `POST /usage` `{}` — this tenant's plan and consumption.
- `POST /feedback/create` `{"context_type": "budget"|"question"|"crew"|"general",
  "rating": "up"|"down", "comment", "context_id"?, "snapshot"?}` — when a
  producer says a number is wrong, log it here before moving on.
