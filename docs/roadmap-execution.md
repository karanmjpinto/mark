# Execution plan — closing the gaps

Working document. Derived from `docs/competitive-review.md` §7. Status is
updated as things land, so this doubles as the tracker.

**The thesis in one line:** three builds change the business (rate library,
schedule, variance ledger) because each one converts a service Mark performs by
hand into an asset that compounds. Everything else on the list is catch-up and
can wait.

---

## Sequencing logic

The order is not "biggest first". It is dependency-first:

```
        ┌──────────────────┐
        │  1. Rate library │◄──── corrections from every engagement
        └────────┬─────────┘
                 │ feeds priced line items
        ┌────────▼─────────┐      ┌─────────────────────┐
        │  2. Schedule     ├──────► 4. Call sheet from   │
        │     + DooD       │      │    a shooting day    │
        └────────┬─────────┘      └─────────────────────┘
                 │ computed shoot_days
        ┌────────▼─────────┐
        │  3. Variance     ├──────► proposes new rates ──┐
        │     ledger       │                             │
        └──────────────────┘                             │
                 ▲                                       │
                 └───────────────────────────────────────┘
```

The loop is the point. A teardown produces actuals; actuals produce verified
rates; verified rates make the next budget right; the next budget is the thing
the client pays for. Today that loop runs through a human with a spreadsheet and
nothing is retained.

---

## Phase 1 — the loop (this session)

Backend, agent and test coverage. No UI yet, deliberately: every one of these is
pure logic with a thin endpoint, and shipping the logic with tests first means
the UI is a rendering job rather than a design-and-debug job.

| # | Build | Files | Acceptance |
|---|---|---|---|
| 1.1 | **Rate library** | `backend/ratecard.py`, `backend/seeds/*.json` | Tenant-scoped CRUD; resolver returns a compact rate pack for (region, city, tier); seeds load; unverified seeds are marked as such |
| 1.2 | **Rates into the budget** | `flue-agents/.flue/agents/generate-budget.ts` | Agent receives `rates[]` and is instructed to use a supplied rate verbatim and cite its source; anti-hallucination rule extended |
| 1.3 | **Per-scene breakdown** | `backend/main.py` `_process_pages` | `/script/parse` also returns `scenes[]` with number, INT/EXT, location, time, cast, eighths — additive, existing keys unchanged |
| 1.4 | **Schedule + DooD** | `backend/schedule.py` | Scenes → strips → shooting days that respect location blocks; DooD matrix per character; company-move count; `shoot_days` computed |
| 1.5 | **Call sheet from a shoot day** | `backend/main.py` | `/callsheet/from-schedule` seeds the existing call-sheet shape from day N of a schedule |
| 1.6 | **Variance ledger** | `backend/variance.py` | Budget + actuals → per-line delta, classification, section rollups, >10% flags, recurring patterns across ≥2 productions, annualised cost |
| 1.7 | **GST + TDS engine** | `backend/compliance.py` | Per-line TDS section and rate, gross/TDS/net payable, vendor rollup, payment schedule, explicit not-tax-advice disclaimer |
| 1.8 | **Tests** | `evals/test_*.py` | Every pure function above covered offline; `python3 evals/run_evals.py` and the unit suite both green |
| 1.9 | **Agent surface** | `mcp/server.py` | New capabilities exposed as MCP tools so any agent can drive them |

## Phase 2 — make it visible

| # | Build | Delivered as |
|---|---|---|
| 2.1 | Schedule UI (stripboard, drag between days) | `frontend/schedule.html` + `/schedule/save` (`schedule.rebuild`) |
| 2.2 | Rate-card editor | `frontend/rates.html` — edit in place, verify/unverify, coverage meter |
| 2.3 | Teardown report generator | `backend/teardown_report.py` + `/teardown/report`, driven from `frontend/teardown.html` |
| 2.4 | Excel round-trip + Movie Magic export | `backend/exporters.py` + `/budget/export`, `/budget/import`, `/variance/export` |
| 2.5 | Budget versioning + diff | `backend/budgetdiff.py` + `/budget/versions`, `/budget/diff` |

**On `.mmb`.** The export is a delimited account/detail interchange file that
Movie Magic Budgeting imports, not a binary `.mmb`. That format is proprietary
and undocumented; a file written blind either fails to open or opens with wrong
numbers, and the second failure is far worse than not shipping it. Revisit only
with the format spec or a licence.

## Phase 3 — distribution and proof

| # | Build | Delivered as |
|---|---|---|
| 3.1 | Call-sheet delivery state + one-tap crew confirmation | `backend/delivery.py`, `frontend/delivery.html`, `/c/{send}/{recipient}/{token}` |
| 3.2 | Crew/vendor roster with rate history, feeding the rate library | `backend/roster.py` + `/roster/*` |
| 3.3 | Public accuracy report generated from `evals/` | not started |
| 3.4 | Hindi/Marathi call sheets | not started |

---

## Non-goals, stated so they don't creep in

- **Not building a generative previs or script-writing feature.** Different
  buyer, no pull-through to the rate library.
- **Not chasing Filmustage feature-for-feature.** Breakdown parity buys nothing;
  §2 of the review is explicit about it.
- **Not putting real client rates in this repo.** The repo is public. Seeds are
  market-reference placeholders, flagged unverified; verified rates live in the
  tenant's own store.
- **Not shipping tax logic as advice.** The compliance engine is indicative and
  says so in every response. A CA signs it off before it is quoted to a client.

---

## Status

Updated as work lands.

| Item | State | Evidence |
|---|---|---|
| 1.1 Rate library | ✅ shipped | `backend/ratecard.py`, `backend/seeds/*.json`, 19 tests |
| 1.2 Rates into the budget | ✅ shipped | `generate-budget.ts` + SKILL.md; `/budget/generate` resolves a pack per region·city·tier |
| 1.3 Per-scene breakdown | ✅ shipped | extracted to `backend/screenplay.py` (pure, so CI covers it); returns `scenes[]` + `scenes_truncated`, additive. 11 tests |
| 1.4 Schedule + DooD | ✅ shipped | `backend/schedule.py`, 14 tests |
| 1.5 Call sheet from a shoot day | ✅ shipped | `/callsheet/from-schedule`, partial by design |
| 1.6 Variance ledger | ✅ shipped | `backend/variance.py`, 20 tests, CSV + xlsx readers |
| 1.7 GST + TDS engine | ✅ shipped | `backend/compliance.py`, 18 tests — **awaiting CA sign-off before client use** |
| 1.8 Tests | ✅ shipped | `evals/run_unit_tests.py`, 82 unit tests + the 20 existing invariant checks, wired into CI |
| 1.9 MCP tools | ✅ shipped | 9 new tools in `mcp/server.py` |
| 2.1 Stripboard UI | ✅ shipped | `frontend/schedule.html`; drag → `/schedule/save` → server recomputes; 20 schedule tests |
| 2.2 Rate editor | ✅ shipped | `frontend/rates.html`; inline edit, verify toggle, coverage meter |
| 2.3 Stage 0 report | ✅ shipped | `backend/teardown_report.py`, 11 tests; renders inline, prints to PDF |
| 2.4 Interop | ✅ shipped | `backend/exporters.py`, 13 tests; xlsx out/in + Movie Magic interchange |
| 2.5 Versioning + diff | ✅ shipped | `backend/budgetdiff.py`, 11 tests |
| 3.1 Delivery state + confirmation | ✅ shipped | `backend/delivery.py` (16 tests), `frontend/delivery.html`, crew page verified on a 375px viewport |
| 3.2 Crew/vendor roster | ✅ shipped | `backend/roster.py` (20 tests); `ingest_ledger` joins a teardown to vendor history |
| 3.3 / 3.4 | not started | |

### Phase 3 — verified how

The crew-facing confirmation page was driven in a real browser at phone size: tap
"I'll be there" with a note, and the board moved to 1 of 3 confirmed with the note
attached. The webhook endpoint refuses without the shared secret (401) and refuses
to exist at all when `WEBHOOK_SECRET` is unset (503) — an open endpoint that can
mark arbitrary crew as having read a call sheet is not a small hole. A guessed
confirmation token returns 403.

Two defects the smoke test found in the roster, both of the kind that corrupt data
quietly rather than crashing:

1. **A later call sheet overwrote a fuller name.** "Ravi Kulkarni" became "Ravi K"
   on the second import, because the latest write won. The roster would have got
   worse every time it was used. The fuller name now wins.
2. **Crew imports could never reach the rate card.** A crew list carries roles,
   not rate-card keys, so every imported person was skipped by `propose_rates()`.
   The key is now derived from the role through the same normalisation the rate
   card uses — and every proposal says whether its key was given or derived,
   because a derived key can create a rate-card row nobody chose the name of.

### Phase 2 — verified how

Every module has offline tests (`python3 evals/run_unit_tests.py`, 118 checks
across nine suites, no dependencies). The three new pages were then driven in a
real browser against a running backend: build a schedule from the demo scenes,
drag a strip between days and watch the server recompute, seed 46 rates and
correct one to verified, then run two productions through the teardown bench to
the rendered Stage 0 report.

Three defects that only the browser found:

1. **Hand-editing a schedule silently emptied the Day-out-of-Days.** The day
   record dropped each scene's `characters`, so the first drag destroyed the
   cast, the hold days and the DooD. Day records now carry the cast, and a
   round-trip test pins it.
2. **A blank cell in a spreadsheet shifted every column after it.** The .xlsx
   reader matched only `<c …>…</c>` and not the self-closing `<c … />` that every
   real sheet is full of — so an amount could be read out of the wrong column.
   Fixed, with XML entity decoding alongside it (a vendor called "Sound & Vision"
   was arriving as `Sound &amp; Vision`).
3. **The Stage 0 report opened in a blocked pop-up.** The single document a
   client pays for cannot depend on the pop-up blocker; it renders inline in an
   iframe now, with print and download.

And one found while writing the report generator: `recurring_patterns()` dropped
unbudgeted spend because it has no percentage to be material against. A cost with
no budget line, spent on every production, is the most systemic finding a teardown
produces — it is now kept, on money rather than percentage.

### Verified how

Every module is covered offline (`python3 evals/run_unit_tests.py`, no
dependencies). The endpoints were additionally smoke-tested against a running
backend on the in-memory store: seed → rate pack → schedule → call sheet →
variance ledger → teardown → rate proposals → apply → coverage, plus the GST/TDS
computation, the payment schedule and a CSV cost-report upload.

That smoke test earned its keep: it caught a real defect in
`propose_from_variance()`, which divided actual spend by the *budgeted* quantity.
A location that ran three days against two budgeted came back as a ₹2,32,500 day
rate instead of ₹1,55,000 — a bad rate written straight into the library the
whole system is supposed to compound. Ledger lines now carry `actual_quantity`,
proposals record their `quantity_basis`, and a proposal derived from a budgeted
quantity is flagged `needs_review`. Two regression tests pin it.

Writing the parser tests turned up a third: a `CONTINUOUS` scene heading was
counted as neither day nor night in the summary, while the scheduler assumed day.
Time of day now inherits from the previous scene, which is what the slug means on
the page, and every scene lands in exactly one bucket — it drives the lighting
package, the catering meal count and the turnaround between shooting days, so a
scene without one is a costing hole.

A second defect came out of review rather than the smoke test: the budget
determinism cache is global by design (two producers pricing the same TVC should
see the same number) but rate cards are per-tenant, so without a rate fingerprint
in the key, tenant A's rate-priced budget would have been served to tenant B —
and a corrected rate would have kept returning the pre-correction budget until
the cache expired. `_rate_fingerprint()` now folds item, rate, unit and verified
status into the key; re-wording a rate's description does not bust the cache,
correcting its number does.

### Known gaps in what shipped

- **The .xlsx was never opened in Excel.** It is verified against the OOXML spec
  (every part well-formed, all required parts present) and round-trips through
  Mark's own reader. Open one in Excel and Numbers before sending a client a file.
- **The new pages have no auth.** They talk to the backend with whatever
  `AUTH_MODE` allows, same as `budget.html`. Fine for the demo, not for a tenant.
  The crew confirmation page is deliberately different: it is public by design and
  authenticated by a signed per-recipient token.
- **Delivery and read state have never seen a real Unipile webhook.** The event
  mapping is written from the documented shapes and is tolerant, but until a real
  payload arrives the board says so in `note` and `read_state_verified` is false.
  Confirmation does not depend on it.
- **Tax rules are unsigned.** `compliance.RULES` and `THRESHOLDS` are defaults.
  Correct before quoting.
- **The rate seeds are placeholders.** 46 India, 15 UK, 12 US rows, all
  unverified. They exist so a cold tenant gets a sane budget, not so anyone
  quotes them.
- **The frontend does not send `city` yet**, so India budgets resolve rates
  city-agnostically (Mumbai rows come through flagged as indicative). Passing the
  shoot city through the question flow is a Phase 2 line item and it materially
  improves accuracy.
