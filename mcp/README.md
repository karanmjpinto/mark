# Mark MCP server

Exposes Mark's line-producer capabilities as [MCP](https://modelcontextprotocol.io)
tools, so Mark is callable from Claude Desktop, Claude Code, Cursor, or any other
agent — not just the web UI. This is the "agent-native, inbound" half: today
Mark *sends* to agents/humans; this lets agents *drive* Mark.

Each tool is a thin, typed wrapper over an existing Mark REST endpoint. The
backend stays the single source of truth — no logic is duplicated here.

## Tools

| Tool | Wraps | Notes |
|---|---|---|
| `generate_budget` | `/budget/generate` | Sync (Haiku). Returns the itemised budget. |
| `generate_budget_async` + `get_job` | `/budget/generate/async`, `/jobs/get` | Queue a Sonnet budget, poll for the result. |
| `refine_budget` | `/budget/refine` | Plain-English edits to a budget. |
| `render_callsheet` | `/callsheet/render-template` | Render a call sheet in a producer's own layout. |
| `refine_callsheet` | `/callsheet/refine` | Plain-English edits to a call sheet. |
| `propose_callsheet_send` | `/callsheet/send/propose` | **Stages** a send; returns a preview. Sends nothing. |
| `confirm_callsheet_send` | `/callsheet/send/confirm` | Executes an approved send. Idempotent. |
| `enrich_crew` | `/crew/enrich` | Contact/role lookup for a stored crew member. |
| `recent_traces` | `/admin/traces` | Recent agent-run traces, for debugging. |

The send flow is split on purpose: `propose` → (human approves the preview) →
`confirm`. Human-in-the-loop is expressed to the calling agent as two tools, so
an irreversible action can't happen in one unattended step.

## Run

```bash
cd mark/mcp
pip install -r requirements.txt
MARK_API_BASE=https://your-backend MARK_API_KEY=your-key python server.py
```

Environment:

| Var | Default | Purpose |
|---|---|---|
| `MARK_API_BASE` | `http://localhost:8000` | Backend base URL. |
| `MARK_API_KEY` | — | The `X-API-Key` secret, if the backend enforces one. |
| `MARK_MCP_TIMEOUT` | `240` | Per-request timeout (seconds). |

## Client config

Claude Desktop / Claude Code (`claude_desktop_config.json` or `.mcp.json`):

```json
{
  "mcpServers": {
    "mark": {
      "command": "python",
      "args": ["/absolute/path/to/mark/mcp/server.py"],
      "env": {
        "MARK_API_BASE": "https://your-backend",
        "MARK_API_KEY": "your-key"
      }
    }
  }
}
```

Then ask the client, e.g.: *"Generate a budget for a 2-day Mumbai TVC, mid-tier,
DOP attached, no VFX"* — it will call `generate_budget` with the QA it gathers.
