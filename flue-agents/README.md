# mark-flue-agents

Flue sidecar for the AskMark FastAPI backend. Hosts the agents that turn questionnaire answers into budgets and enrich crew records.

## Layout

```
flue-agents/
├── .agents/skills/         # markdown skills (generate-budget, enrich-crew)
├── .flue/agents/           # webhook agents (TS)
├── AGENTS.md               # global agent context
├── package.json
└── tsconfig.json
```

## Run locally

```bash
cd flue-agents
cp .env.example .env       # then fill ANTHROPIC_API_KEY
npm install
npm run dev                # starts Flue on http://localhost:3583
```

The FastAPI backend reads `FLUE_BASE_URL` (defaults to `http://localhost:3583`).

## Endpoints exposed

Every agent with `triggers = { webhook: true }` becomes `POST /agents/<name>/<id>`:

- `POST /agents/generate-budget/<project_id>` — body: `{ project, crew, answers }`
- `POST /agents/enrich-crew/<crew_id>` — body: `{ crew_member, project }`

Don't call these from the frontend directly — call the FastAPI wrappers (`/budget/generate`, `/crew/enrich`) so auth, persistence, and rate-limiting stay in one place.

## Deploy

`flue build --target node` produces `dist/server.mjs`. Run it on Railway/Fly/any Node host. Set `ANTHROPIC_API_KEY` and `PORT` (defaults to 3000 for the built server). Point FastAPI's `FLUE_BASE_URL` at the deployed URL.
