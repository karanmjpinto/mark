# Mark on WhatsApp

Mark already sends to WhatsApp, and already exposes its tools to agents over MCP.
This is the third thing: a producer *driving* Mark from a WhatsApp thread — the
place they already work from at 6am on a location van.

The agent is not built here. It is built on **CodeWords**
([codewords.agemo.ai](https://codewords.agemo.ai) · product site
[codewords.ai](https://www.codewords.ai) · docs
[docs.codewords.ai](https://docs.codewords.ai)), which supplies the part this
repo should not own: a verified WhatsApp number, inbound message routing, media
handling, per-user memory and a hosted agent loop. What this repo owns is the
tools, the identity check and the money.

```
WhatsApp  ──▶  CodeWords agent ("Mark")  ──▶  askmark backend (this repo)
  producer      system prompt + tools          X-API-Key + X-Operator-Phone
  crew                                         /chat/*, /budget/*, /callsheet/*…
                      ▲                                   │
                      └───────── text + share link ◀──────┘
```

## What is built

The agent is deployed on CodeWords as **`mark_whatsapp_agent_0584417b`** ("Mark on
WhatsApp"), built through the CodeWords connector rather than by hand in Cody. It
is a single FastAPI service holding a Claude tool loop over 27 Mark endpoints, an
operator allowlist, per-thread memory in Redis, and a handle stash so a 150-line
budget never enters the model context. [`CODY_BRIEF.md`](CODY_BRIEF.md) remains the
specification — the system prompt, the tool surface and the flows are taken from
it, and it is still what you paste to Cody to rebuild the agent by chat.

Its own endpoints:

| Endpoint | Purpose |
|---|---|
| `POST /webhook` | Inbound WhatsApp. Gates in order: event → own message → text → group → duplicate → stale → allowlist. |
| `POST /set-access` | Who may drive Mark from WhatsApp, as `{numbers:[{phone, role}]}`. |
| `POST /enrol` | Pushes this agent's operator list into Mark's own roster, so the backend refuses a stranger even if the agent is bypassed. |
| `POST /` | A self-test: is Mark reachable, are the `/chat` endpoints deployed, which device sends, who is enrolled, what is still missing. |

Secrets set: `MARK_API_BASE`, `MARK_API_KEY` (placeholder `unset` — the key header
is omitted until a real key is stored), `MARK_WA_PHONE_ID`, `MARK_OPERATORS`
(`447472960640:producer`).

## Setup

1. **Create the agent.** In CodeWords, start a new automation and paste
   [`CODY_BRIEF.md`](CODY_BRIEF.md) to Cody. Then paste
   [`mark-api.md`](mark-api.md) in the same chat — the docs page for external
   APIs says to hand Cody the reference for a proprietary API, and that file is
   written to be that paste.
2. **Store the key.** CodeWords → Settings → API Keys → Add Variable:
   `MARK_API_KEY` (the backend's `API_KEY`, or the tenant's key under
   `AUTH_MODE=apikey`) and `MARK_API_BASE`
   (`https://backend-production-6ea4.up.railway.app`).
3. **Connect a number.** Shared DM bot for a pilot, your own number via the
   pairing code when you want it to come from the production office, Business
   API when it needs the verified check and template sends.
4. **Enrol the humans.** Nothing works until a number is on the roster:

   ```bash
   curl -sX POST "$MARK_API_BASE/chat/operators/enrol" \
     -H "X-API-Key: $MARK_API_KEY" -H 'Content-Type: application/json' \
     -d '{"phone":"+919820012345","name":"Karan","role":"producer"}'
   ```

   Roles: `viewer` (read), `coordinator` (read, write), `producer` (read, write,
   **send**). Only a producer can release a call sheet to crew.
5. **Set the public base.** `MARK_PUBLIC_URL=https://askmark.filmsbykp.com` on
   the backend, so share links go out on the custom domain rather than the
   Railway host.

## What the backend added for this

Three endpoints, because a chat channel needs three things the web UI never did
(all of it in `backend/chatops.py`, covered by `evals/test_chatops.py`):

| Endpoint | Why it exists |
|---|---|
| `POST /chat/session` | A phone number is the only identity a chat has. Resolves it to an enrolled operator, their role, and the production the thread is pinned to. An unenrolled number is `403` before any tool runs — the agent's number is public by construction. |
| `POST /chat/render` | The REST shapes are built for a web table. This returns the same numbers at message size (≤1,400 chars, `₹1.86 Cr` not `18600000`) plus a link to the full document. |
| `GET /s/{id}/{token}` | A budget or a Stage 0 report cannot be read in a thread. An opaque, expiring, login-free URL — the same trick as the call-sheet confirmation page. The HTML always comes from Mark's own renderers; a caller cannot hand us markup to host. |

`POST /chat/active-project` pins the thread, and `/chat/operators/enrol|list|revoke`
manage the roster.

## Honest limits

- **One tenant per agent.** The agent holds one `MARK_API_KEY`, and under
  `AUTH_MODE=apikey` a key *is* a tenant. The phone check authorises a person
  inside that tenant; it does not route between tenants. Two production
  companies means two CodeWords agents, or a phone→key map held on the CodeWords
  side.
- **Delivery and read state are the provider's claim, not ours.** Confirmations
  are ours and are reliable. Every render says which is which.
- **India tax output stays gated.** `/compliance/*` carries `reviewed: false`
  until a CA signs off the tables, and the agent is told to quote it as
  indicative. Do not let it be forwarded to a client as advice.
- **Unverified rates stay amber all the way down the pipe.** The message says
  how many lines sit on market placeholders; the shared document repeats it in
  the legend. A number that has never been teardown-verified must not reach a
  client looking finished.
- **A hand-assembled ledger will not render a report.** Pass cost reports
  through `/variance/compute` so the ledger has `material_lines` and each line's
  `basis`; a ledger the agent builds itself loses the link (`share_error` says
  so) even though the message still comes back.
