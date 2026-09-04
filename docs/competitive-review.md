# Competitive review — AI in film production & pre-production

**August 2026.** Where Mark actually sits against everyone building in this
space, what to change, and why India is the wedge rather than the fallback.

Sources are linked inline. Pricing was read off vendor pricing pages or
vendor-published comparisons in August 2026 and moves often — re-check before
quoting any of it to a client.

---

## 1. The map

Five categories. They are usually lumped together as "AI film production
tools", which is why the category looks crowded and isn't.

### 1.1 The desktop standard (the real incumbent)

| Product | What it is | Why it matters to Mark |
|---|---|---|
| **Movie Magic Budgeting / Scheduling** (Entertainment Partners) | The file format the industry runs on. Import/export, incentive and rebate estimation, multi-location budget comparison, cloud-shared budgets. Integrates into EP SmartAccounting / SmartStart / SmartPO / EP Payroll. | Not an AI competitor — a **format and trust** competitor. A budget that can't leave Mark as `.mmb` or a clean Excel dies at the production accountant's desk. |
| **Showbiz Budgeting, Gorilla** | Cheaper desktop equivalents. | Same point, lower down the market. |

EP has not shipped a headline AI budgeting feature; its moat is the accounting
chain behind the budget, not the estimate. That chain does not exist in India.

### 1.2 AI pre-production platforms (Mark's apparent competitors)

| Product | Base | What it actually does | Price |
|---|---|---|---|
| **Filmustage** | US/EU | Script import → AI Smart Breakdown → AI Smart Scheduling → **AI Budgeting** with "Budget Hints" that explain the logic, sources and assumptions behind each estimate. Model choice (own / Gemini / GPT). "AI Dude" co-pilot that moves strips in the stripboard. Exports PDF, Excel, **MMS/MMB**. | Free (20% of scenes) / **Director's Cut $55 mo** ($45 annual), metered in "coins" / Enterprise |
| **Rivet AI** | Culver City; incubated and funded by production company End Cue; founded 2018; team ex-SpaceX/Pixar/Microsoft | Script analysis, dynamic scheduling, line-item budgeting that re-costs as parameters change. Positioned at **topsheet forecasting for greenlight** — "what does this cost if we shoot it in Georgia with 3 fewer days". HPA Awards 2026 innovation nominee. | Custom |
| **Studiovity** | **Delhi, India** | Screenwriting + AI breakdown + schedule + call sheets + budgeting + storyboard. Web, Android **and iOS**. Aggressively cheap, heavy SEO output. | ~$29/mo reported |
| **Storyflow, Drawstory, Prodslate** | Various | Smaller planning/scheduling tools, mostly indie market. | $ |

**The honest read:** Filmustage is the closest thing to a direct competitor and
it is ahead of Mark on surface area — it has a stripboard, a schedule, an agent
that manipulates the schedule, and export into the incumbent's format. It is
behind Mark on two things that matter: it is a self-serve tool with nobody
deploying it, and its rate reasoning is generic ("industry-standard rates and
open-source financial data"), which in India means wrong.

Studiovity is the one to watch for India. It is Indian, cheap, mobile, and
already ranks for every "film production software India" query. It is a
features-checklist product with no services layer and no evidence of rate
depth — but it will be the price anchor in every Mumbai conversation Mark has.

### 1.3 Production finance and actuals (where the money is)

| Product | What it does | Notes |
|---|---|---|
| **Saturation.io** | "Financial operating system": budget building, **actuals vs budget in real time**, department overrun flags, AI receipt scanning, approval chains, AI-assisted **fringe** calculation (union rates, pension & health, vacation, payroll tax). Claims 8,000+ producers. | Free for one project; paid from **$25/mo** |
| **Wrapbook** | Payroll + production accounting + spend. a16z / Bessemer / WndrCo backed; **$750M** self-declared valuation after a $20M Bessemer round (down from $1B in 2021); 350+ staff; used by Netflix/Paramount. Runs an $800k grant programme for US productions. | Enterprise |
| **GreenSlate, EP SmartAccounting, Cast & Crew** | Same category, older. | Enterprise |

**This is the category that should worry Mark most, and the one it is closest
to in thesis.** Mark's entire Stage 0 sales argument — *budget vs actual, three
productions, a variance ledger* — is what Saturation gives away for free in the
US. The difference: their fringe/union engine, the thing they charge for, is
worthless in India, and none of them operate here. India has no equivalent
product at any price.

### 1.4 Production management and coordination

**StudioBinder** (script → stripboard → auto-filled call sheets with weather,
location, nearest hospital; rated best-overall in most 2026 round-ups),
**Yamdu** (stripboard + auto call sheet, full production suite), **Croogloo**
(studio-scale secure document distribution with per-recipient delivery and open
tracking, plus AI-assisted cast & crew management), **Assemble** (commercial and
agency video teams), **SetHero** (call sheets).

Every one of them auto-generates a call sheet **from a schedule**. Mark
generates a call sheet from nothing, into the client's own template — better
output, no upstream.

### 1.5 Adjacent AI (noise, but it shapes the buyer)

Greenlight/analytics: **Cinelytic**, **Largo.ai**. Generative previs and virtual
production: **LTX Studio**, **Katalist**, **Runway**, **Cuebric**. In India,
**Abundantia Entertainment × InVideo** announced an AI film studio and a slate of
five AI-driven films at the India AI Impact Summit 2026.

None of these compete with Mark. They matter because they are what an Indian
producer means when they say "AI in production", and the first job of any Mumbai
email is to not sound like them.

---

## 2. What everyone is actually doing vs what they claim

Four observations that should drive the roadmap.

**1. Script → breakdown → schedule is solved and commoditised.** Every serious
player does it, several for free. Mark parses a script and generates six
questions; it does not schedule. Building breakdown parity buys nothing.

**2. Budget is the weak link everywhere, and for a structural reason.** Rate
data is proprietary, local and unpublished. Filmustage's answer is to explain
its assumptions (Budget Hints) rather than to be right — a confidence display,
same as Mark's green/amber/red markers. Whoever actually *owns* verified local
rate data wins this category, and nobody owns India's.

**3. Nobody outside the US closes the estimate → actual loop.** Saturation,
Wrapbook, EP and GreenSlate all do, and all are built around US union fringes
and US payroll. Their moat does not travel. India's ₹2.78 trillion M&E industry
has no production-finance product.

**4. Everyone in the AI category is a $25–99/month self-serve tool.** Mark's
$4,500 → $18,000 → $9,000/mo ladder is not competing with them. Mark is
competing with a line producer's fee and a consultant's day rate — which is
exactly how `docs/pricing-basis.md` benchmarks it. **Stop benchmarking the
product against SaaS, and stop letting a client do it either.** The moment a
Mumbai producer compares Mark to Studiovity at ₹2,500/month, the conversation is
lost; the counter is that they are buying a person and a system, not seats.

---

## 3. Where Mark genuinely wins today

Ranked by how hard each is for a competitor to copy.

1. **Region-native cost reasoning with India as a first-class market.** The
   five mandatory Indian questions (lead-actor fee tier, shoot days, number of
   songs, union/non-union, international locations), the binding budget-tier
   rule, GST per line item, section codes in the order an Indian producer reads
   them. No competitor has anything like it — they have a currency dropdown.
2. **Call sheets rendered into the client's own template.** Every competitor
   imposes its layout. In India, where each house and each channel mandates its
   own format, "it comes out looking like yours" removes the single biggest
   reason production teams refuse to adopt tools.
3. **The deployment model.** A person who has done the job, with a system behind
   them. Unattackable at $55/month, and it is what Indian production houses
   actually buy — they hire people, not software.
4. **Evals, invariants and tracing.** A regression gate on budget quality with
   golden fixtures and CI, plus per-agent-run tracing. No competitor advertises
   anything equivalent. This is a sales asset, not just engineering hygiene —
   see §5.
5. **Agent-native (MCP).** Mark's tools can be driven from a client's own Claude
   or Cursor, and — more usefully in India — from a WhatsApp agent.

---

## 4. Where Mark is behind — ranked, with the fix

### 4.1 There is no schedule. This is the biggest gap.

Without a stripboard/DooD layer, Mark cannot reconcile a budget against shoot
days, cannot generate a call sheet from anything, cannot answer "what if we drop
a day" — the one question a producer asks constantly and the exact thing Rivet
sells. Every competitor in §1.2 and §1.4 has this.

**Fix:** a scene-level schedule derived from the existing parsed breakdown
(scenes, INT/EXT, day/night, locations, characters) — strips, day boundaries,
DooD, and a `schedule → budget` link so day counts stop being an answer in `qa`
and start being a computed number. Then call sheets generate from it.

### 4.2 The teardown lives outside the product.

Stage 0 — the whole commercial wedge — is done by hand in a spreadsheet. Every
teardown is five days of labour that produces no reusable asset except a PDF.

**Fix:** an **actuals ingest + variance ledger** in the product. Take an
approved budget and a final cost report in any format, normalise both to
Mark's section codes, and emit the ledger the SOW promises (every line >10%,
classified). This does three things at once: cuts Stage 0 delivery from five
days to one, makes the deliverable interactive rather than a PDF, and feeds
clause 9.2 derived data — which is what turns each engagement into a rate card.

### 4.3 No interop with the incumbent format.

No `.mmb`/`.mms` import or export, no round-trip with the client's existing
budget template. Filmustage exports both.

**Fix:** Excel round-trip first (maps to how Indian production accounts actually
work), MMB export second, and — most important in India — **import the client's
own budget template** so the output arrives in the shape their finance team
already signs off.

### 4.4 Rates live in the model, not in a database.

Mark's regional rate knowledge is prompt-resident. It cannot be corrected by a
client, versioned, audited, or compounded across engagements. That is the exact
asset clause 9.2 was written to accumulate, and there is nowhere to put it.

**Fix:** a per-tenant **rate library** — item, city, tier, unit, rate, source,
last-verified date — that seeds line items before the model reasons, and that a
producer can correct in place. Corrections become the moat. This is the single
highest-leverage build in this document after §4.1.

### 4.5 Missing team mechanics.

No budget versioning and diff ("what changed between v3 and v4"), no approvals,
no comments, no per-user roles beyond the tenancy layer. Producers work in
teams and budgets get renegotiated eight times.

### 4.6 No crew/vendor layer.

`enrich-crew` exists as an agent but there is no roster, no availability, no
vendor list, no rate history per vendor. In India this is the daily pain — who's
free, what did we pay them last time, who has the Alexa on the 14th.

### 4.7 The WhatsApp loop is half-built.

Sending exists — `/callsheet/send` dispatches to email and WhatsApp through
Unipile, with a propose/confirm approval gate. What does not exist is the half
that matters on a set: delivery and read state per recipient, a one-tap crew
confirmation, and anything a unit can open on a phone. Croogloo sells exactly
that state layer to studios.

---

## 5. India: the specific moves

This is where the advantage is real. India is not a discount market for a
US product — it is a market with no incumbent, where Mark's existing rate
work is already ahead of everyone.

**Market, for the pitch deck:** India's M&E sector grew 9% to **₹2.78 trillion
in 2025** and is forecast at ₹3.3 trillion by 2028; advertising rose 13% to
**₹1.5 trillion**; filmed entertainment posted its best-ever year at **₹205
billion**. CBFC certified **2,248 films for theatrical release** in the partial
2025–26 period, and **71,963 films across all formats over five years**. The
production volume is enormous and the paperwork underneath it is unmanaged.

### 5.1 Finish the WhatsApp loop

The send path is already there. What turns it into a product is the state on top:
delivery and read state per recipient, and a one-tap confirm that a producer can
see on a single screen at 11pm. That is Croogloo's studio-grade feature (secure
distribution with open tracking) delivered the way India actually works, and it
makes Mark a **daily-use** product rather than a per-budget one — which is what
generates the data in §4.4.

### 5.2 Build the India compliance engine — this is the local equivalent of US fringes

Saturation charges for automatic union fringes, pension, vacation and payroll
tax. The Indian analogue does not exist in any product:

- **GST** at the correct rate per line (already partly done), plus input credit
  treatment and the distinction between what the production can and cannot claim.
- **TDS** by payee type — 194C for contractors, 194J for professional/technical
  services — computed per line item, so the budget shows **gross, TDS and net
  payable per vendor** rather than a single number a coordinator later has to
  unpick.
- **PF/ESIC** exposure where crew are engaged as employees rather than vendors.
- Advance/`bayana` conventions and payment-terms modelling, because Indian
  productions are cash-timing problems as much as cost problems.

A budget that outputs a payment schedule with TDS already deducted is worth more
to a Mumbai producer than any AI feature on this page. **Get an Indian CA to
sign off the logic before this ships** — wrong TDS treatment in a client's
budget is a liability, not a bug.

### 5.3 Encode the union/association reality

FWICE and the craft associations, the 8-hour shift and overtime conventions,
conveyance, outstation *batta*/per diem, and the difference in BTL rates between
union and non-union crews — which the `generate-budget` skill already asks about
but does not cost from a real card.

### 5.4 City-level rate cards and Indian production physics

Mumbai / Hyderabad / Chennai / Bengaluru / Goa, plus studio-floor rates (Film
City, Madh, Filmistan, Mehboob), permission regimes (Maharashtra single-window,
BMC, Railways, Airports Authority, forest and coastal permissions), and the
**monsoon window** — June–September materially changes contingency on any EXT
schedule and no product models it.

### 5.5 Formats Indian buyers actually commission

Add **per-episode** budgeting and daily-soap economics: a Hindi GEC serial
delivers 22–26 episodes a month with a fixed per-episode licence fee, which
makes it the most paperwork-dense, most margin-sensitive production type in the
country. Also: regional-language features, branded content, and the
songs/choreography/action-director lines that Hollywood templates simply lack.

### 5.6 The credibility details

Lakh/crore formatting and Indian digit grouping (₹1,23,45,678, not ₹12,345,678),
₹ symbol everywhere, INR default, and **Hindi/Marathi call sheets** — crew on
the floor read Hindi. These are small builds that decide whether the first
screenshot reads as "built for us" or "an American tool with a currency
dropdown".

### 5.7 Price in rupees, in bands

$4,500 is ≈ ₹4 lakh. For Applause, Banijay Asia or Balaji that is a rounding
error. For a 17-person ad-film house turning over ₹5–10 crore it is a real
decision that will go to the owner and stall. Recommendation:

| Segment | Stage 0 (teardown) | Stage 1 | Stage 2 |
|---|---|---|---|
| Ad-film / TVC house (10–60 staff) | **₹1,50,000**, 3 completed jobs | ₹6–8 L | ₹1.5–2.5 L/mo |
| Content / branded studio | ₹1,50,000 | ₹6–8 L | ₹1.5–2.5 L/mo |
| OTT / film studio, TV factory | keep the USD ladder ($4,500 / $18,000 / $9,000 mo) | | |

Two mechanical notes: quote in INR to Indian entities (a USD invoice invites an
FX conversation and a finance-team delay), and take **100% upfront** on anything
below ₹2 lakh — Indian production payment cycles run 60–90 days and a 50/50 on
₹1.5 lakh is not worth chasing.

### 5.8 Publish the Mumbai rate card

The strongest marketing asset available to Mark, and it is a by-product of the
work: an anonymised, aggregated **what things actually cost in Mumbai** report —
crew day rates by grade, kit, studio floors, catering, transport. Nobody
publishes Indian rates. It is exactly the tacit knowledge the manifesto says
walks out of the door. It generates inbound, it recruits teardown clients, and
every teardown makes the next edition better. Publish it annually, cite the
sample size, name nobody.

---

## 6. Positioning: what to say in Mumbai

- **Do not lead with AI or with the budget builder.** "AI film production" in
  India in 2026 means AI-generated films (Abundantia × InVideo) or a ₹2,500/month
  breakdown tool. Both are the wrong shelf.
- **Lead with the paperwork and the person.** The manifesto line already works:
  *give the paperwork to the machine, give the time back to the floor.* The
  builders are evidence the person knows the work.
- **The wedge product is the call sheet, not the budget.** Call sheets are daily
  pain, daily usage and daily data. The budget is where the moat is; the call
  sheet is how you get in the door and start accumulating rates.
- **Never let the comparison be a SaaS seat.** If a producer names Studiovity,
  the answer is a comparable, not a discount: a line producer's fee, or the cost
  of the last budget that was wrong.

---

## 7. Build order

| # | Build | Why now |
|---|---|---|
| 1 | **Rate library** (per-tenant, city × item × tier, editable, sourced) | Everything else compounds on it; it is the asset clause 9.2 exists to accumulate |
| 2 | **Schedule / stripboard + DooD**, budget linked to day count | Closes the largest functional gap; unlocks call-sheet generation and "drop a day" |
| 3 | **Actuals ingest + variance ledger** | Turns Stage 0 from five days of labour into a product feature; proves ROI with the client's own numbers |
| 4 | **Delivery + read state and crew confirmation** on the existing WhatsApp send | Daily usage in India; Croogloo's feature, delivered locally |
| 5 | **India compliance engine** (GST + TDS + net-payable schedule) | The local equivalent of the US fringe engine; CA sign-off required |
| 6 | **Excel round-trip + MMB export + client-template import** | Stops the budget dying at the accountant's desk |
| 7 | **Budget versioning + diff + approvals** | Table stakes for team use |
| 8 | **Crew/vendor roster with rate history** | India's daily pain; feeds #1 |
| 9 | **Public accuracy report** from `evals/` | Nobody else can publish one; converts engineering into sales |

Items 1–3 are the ones that change the business. Everything below 5 is catch-up.

---

<sub>Competitor facts gathered August 2026 from vendor sites and published
comparisons. Pricing changes frequently and several vendors publish different
figures in different places (Filmustage in particular) — verify before quoting.
Market figures from the FICCI-EY 2026 report as reported by EY and Variety.</sub>

## Sources

- [Filmustage pricing](https://filmustage.com/pricing/) · [Filmustage budgeting](https://filmustage.com/budgeting/) · [AI agents in pre-production](https://filmustage.com/blog/how-ai-agents-are-rewiring-film-pre-production/)
- [Saturation.io](https://saturation.io/) · [AI film budgeting guide 2026](https://saturation.io/blog/ai-film-budgeting-software) · [Filmustage vs Saturation](https://saturation.io/versus/filmustage)
- [RivetAI](https://rivetai.com/en) · [Deadline: End Cue launches RivetAI](https://deadline.com/2024/04/the-art-of-self-defence-producers-end-cue-launch-ai-powered-production-platform-rivetai-1235895588/) · [Broadcast](https://www.broadcastnow.co.uk/tech/rivetai-launches-ai-production-budgeting-and-scheduling-platform/5192826.article)
- [Studiovity](https://studiovity.com/pricing/) · [Studiovity on 2026 tools](https://blog.studiovity.com/top-film-production-software-in-2026-scheduling-budgeting-planning-tools/)
- [EP Movie Magic Budgeting](https://www.ep.com/movie-magic-budgeting/) · [EP SmartAccounting](https://www.ep.com/smartaccounting/)
- [Wrapbook](https://www.wrapbook.com/) · [THR: Wrapbook raises $20M](https://www.hollywoodreporter.com/business/business-news/wrapbook-fund-bessemer-venture-partners-1236009642/) · [Variety: Wrapbook grant programme](https://variety.com/2026/film/news/ai-platform-wrapbook-lgrant-program-us-film-tv-productions-1236697599/)
- [StudioBinder](https://www.studiobinder.com/) · [Croogloo / Yamdu comparisons](https://storyflow.so/blog/best-call-sheet-software-2026)
- [EY: FICCI-EY 2026 report](https://www.ey.com/en_in/newsroom/2026/03/india-s-media-and-entertainment-sector-grew-9-percent-to-inr-2-point-78-trillion-in-2025-driven-by-digital-and-live-experiences-ficci-ey-report) · [Variety on the same](https://variety.com/2026/film/news/dhurandhar-record-2-18-billion-2025-indian-film-ficci-ey-report-1236697510/)
- [Variety: Abundantia × InVideo AI film studio](https://variety.com/2026/global/news/ai-film-studio-india-abundantia-entertainment-invideo-1236665369/)
- [CBFC certification volumes](https://www.digitalstudioindia.com/production/content-business/film-certification-cbfc-18-day-limit)
