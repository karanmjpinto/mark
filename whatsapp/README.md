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

### What Krish asked for, and where it went

Six voice notes on 7 Oct 2026, transcribed locally with `whisper-cli`:

| He said | Where it landed |
|---|---|
| "To make a call sheet you don't need a script… usually you won't get a script for commercials." | `backend/callsheet.py`, `/callsheet/new` and `/callsheet/update`, and the agent's `start_callsheet` / `update_callsheet`. `needs` is computed, so scenes are never asked for. |
| "It needs to read Excel files and Word documents… right now it can only do PDFs." | `POST /document/read` routes PDF, Word, Excel, CSV and text; `documents.write_docx()` writes real Word. The agent's `read_document`. |
| "Edit it and change it when I give it messages on WhatsApp." | `make_document` + `send_file`: a call sheet goes back as Word, a budget as Excel, delivered into the thread. Each WhatsApp message is one `update_callsheet` call, so nothing is retyped. |
| "Link to my email and send email confirmations… then give me updates based on what they reply." | `draft_email` → read back → `send_approved_email` (Gmail, via the account's existing Composio connection), then the thread is watched. |
| "The email that comes back should come back to me on WhatsApp saying they replied, and this is what they said." | `POST /inbox`, on a 30-minute schedule. Replies on threads Mark started are always pushed; other inbox mail has to match a production word, and the message says which word. |
| "Any messages that come in should be fed and filtered into the chatbot… detect that it has something to do with the production." | The `PRODUCTION_WORDS` filter in the agent. A manual `/inbox` run is a dry run and sends nothing, so the sweep can be tested without messaging anybody. |

**What this deliberately does not do.** It does not edit an arbitrary formatted
document in place: it reads the content out and writes a Mark document back. A
producer's own layout is still `/callsheet/render-template`. And no email leaves
without a yes typed in the thread — `draft_email` stages, it never sends.

Its own endpoints:

| Endpoint | Purpose |
|---|---|
| `POST /webhook` | Inbound WhatsApp. Gates in order: event → text or document → group → own message (self-chat only) → duplicate → echo → stale → allowlist. |
| `POST /set-access` | Who may drive Mark from WhatsApp, as `{numbers:[{phone, role}]}`. |
| `POST /enrol` | Pushes this agent's operator list into Mark's own roster, so the backend refuses a stranger even if the agent is bypassed. |
| `POST /inbox` | The email sweep, on a 30-minute schedule. A manual run reports what it would say and sends nothing. |
| `POST /` | A self-test: is Mark reachable, are the `/chat` endpoints deployed, which device sends, who is enrolled, what is still missing. |

Live wiring, 7 Oct 2026:

| Thing | Value |
|---|---|
| Agent service | `mark_whatsapp_agent_0584417b` |
| Sender device | `+44 7472 960640` (`phn_f91d7b5b...`), subscribed to `mark_whatsapp_agent_0584417b/webhook` |
| Operators | `+447472960640` and `+447472174253`, both `producer` |
| Model | `claude-sonnet-4-6` |
| Public base | `MARK_PUBLIC_URL=https://backend-production-6ea4.up.railway.app` — the **backend's** host, not the site's |

The sender number is also an operator, so the normal path is the owner's own
"Message yourself" chat. The webhook therefore accepts two shapes: an inbound DM
from an enrolled number, and the owner typing in the self-chat (`is_from_me`
true, `chat_id` equal to `device_id`). `is_from_me` gates the chat and is never
the echo guard — that is a Redis set of the ids this agent has sent, so the
agent does not answer its own replies. The owner's DMs with anybody else are
refused.

### Why the share host is the Railway one

`MARK_PUBLIC_URL` must name the host that serves `/s/{id}/{token}`, and that is
the **backend**. `askmark.filmsbykp.com` is GitHub Pages: it serves the frontend
and knows nothing about that path, so pointing the variable at it produced share
links that resolved to a Pages 404 — the message was right and the link was dead.
Verified both ways before and after the correction.

A prettier link needs a custom domain on the Railway *service* (`api.filmsbykp.com`,
say): add it in Railway, point a CNAME at the target Railway gives you, then set
`MARK_PUBLIC_URL` to it. The backend has no custom domain today.

### How the API key works

`tenancy.py` runs `AUTH_MODE=off` by default: when the backend's `API_KEY` env
var is empty every request is allowed, and when it holds a value every request
must carry the same value in `X-API-Key`. Three callers hold that key and each
omits the header while its own copy is empty, so the key can be rolled out
caller-first and switched on last:

| Caller | Where the key lives |
|---|---|
| The web app | `localStorage.mark_api_key_header`, set once per browser by opening a `…/budget.html#key=<key>` link. The logic lives in [`frontend/assets/mark-api.js`](../frontend/assets/mark-api.js) and, because `budget.html` and `callsheet.html` predate the shared client and still carry their own copy of the wrapper, in those two files as well — three copies, each covered by the same offline test. A fragment is never sent to a server, so the key stays out of access logs and out of the `Referer` header; the page stores it and strips it from the address bar. `#key=` with nothing after it forgets it. A 401 tells the producer to open that link rather than quoting FastAPI at them. |
| The WhatsApp agent | the CodeWords secret `MARK_API_KEY` |
| The MCP server | the `MARK_API_KEY` env var ([`mcp/README.md`](../mcp/README.md)) |

Set the three callers first, then `API_KEY` on Railway. The reverse order locks
the web app out until the browser copy is in place. The login-free pages —
`/s/{id}/{token}` and `/c/{send_id}/...` — take no key in either case, by
design: they are opened by crew who will never have an account.

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
- **A screenplay reaches Mark as a PDF only.** A producer can attach the script
  in the thread: the webhook pulls a public URL for it through the device
  manager (the `chat_id` from that same webhook is required, and cannot be
  recovered later), parks it for two hours, and the `parse_script` tool uploads
  it to `/script/parse`. The tool hands back a `breakdown` handle for
  `generate_budget` and a `scenes` handle for `generate_schedule`, so the budget
  rests on the real scene list rather than on a filename. `.fdx` and `.docx` are
  refused with that sentence; a photo of a page is refused too.
- **A hand-assembled ledger will not render a report.** Pass cost reports
  through `/variance/compute` so the ledger has `material_lines` and each line's
  `basis`; a ledger the agent builds itself loses the link (`share_error` says
  so) even though the message still comes back.
