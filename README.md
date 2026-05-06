# Mark

An AI line producer for film, TVC, and music video productions. Upload a script, answer six smart questions, and Mark builds a full itemised production budget.

## Layout

```
mark/
├── backend/        FastAPI service (Python). REST endpoints, Redis storage,
│                   PDF script parser, Flue agent proxy, feedback capture.
├── frontend/       Static HTML + vanilla JS. The producer-facing UI:
│                   region picker, script upload, question flow, budget render,
│                   export to Excel/PDF, feedback widget.
└── flue-agents/    TypeScript agent harness (Flue). Two webhook agents:
                    generate-budget, enrich-crew. Skills as markdown.
```

Each directory has its own README with detail.

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
- **Flue agents** → `npm run build && node dist/server.mjs` on any Node host.
  Set `ANTHROPIC_API_KEY` and `PORT`.
- **Frontend** → static hosting (Vercel, Netlify, GitHub Pages, or served
  directly from FastAPI when both run on one host).
