---
name: generate-budget
description: Build a complete itemised production budget from script, region, currency, and question/answer pairs. Use whenever the user wants a budget generated.
---

You are generating an itemised production budget. You will be given these inputs in `args`:

- `script` — the script, treatment, or brief text the producer uploaded.
- `region` — `"india"`, `"uk"`, `"usa"` / `"hollywood"` (treat both as North America), or `"other"`. Drives market rates.
- `currency` — `{ code, symbol }`. Use this currency for every amount.
- `qa` — an array of `{ id, question, answer, options? }`. **Every Q/A pair is ground truth.**
- `breakdown` (optional) — structured screenplay breakdown when the script was parsed:
  `{ total_scenes, int_count, ext_count, day_count, night_count, unique_locations: [{name, scene_count}], characters: [{name, scene_count}] }`. **When present, this is also ground truth** — overrides any rough scene/location counts you might infer from the script prose.
- `project` (optional) — existing project record. May be `null`.
- `crew` (optional) — existing crew roster. May be empty.

## When `breakdown` is present

Use it as the canonical scene/location/character data:
- `total_scenes` is the scene count. Do not invent more.
- `unique_locations` drives the Location Hire (12400) line items — one per location, sized by `scene_count`.
- `int_count` / `ext_count` informs lighting and crew sizing (EXT-heavy productions need more grip/electric on weather days).
- `day_count` / `night_count` informs catering meal counts and lighting package size (night work = more lighting).
- `characters` with high `scene_count` are leads → talent fees scale accordingly.

If `qa` answers conflict with `breakdown` (e.g. answer says "5 shoot days" but `total_scenes` is 80), prefer `qa` for *scheduling* and `breakdown` for *content*, then explain the implied scene-density assumption in `flags`.

## Honor every answer

This is the most important rule. Each `qa` entry is something the producer told you. Before you write a single line item:

1. Read every `qa` entry. Each `answer` is ground truth — do not contradict it.
2. For each answer, identify what it implies for the budget:
   - Locations → location hire, transport, accommodation, permits.
   - Shoot days → multiplier on every day-rate line item.
   - Scale tier (low/mid/high) → which rate band you use throughout.
   - DOP attached vs open market → camera dept day-rate band.
   - Stunts/VFX/choreography flagged → those sections must appear.
3. If an answer is ambiguous, list the interpretation you chose under `flags`.
4. If two answers conflict, prefer the more specific one and note the conflict in `flags`.

A budget that ignores or contradicts a `qa` answer is a failure of this skill. If you find yourself producing a line item that doesn't reconcile with the answers, stop and reconcile first.

### Budget tier is binding — never under- or over-shoot

When the producer states a budget tier or explicit range (e.g. "₹15 to ₹30 Crore", "₹50L to ₹1Cr", "$3M") in any `qa` answer or in the free-text "Other information" entry (id `q_other`):

- The grand total (incl. tax + contingency) **must land inside that range**. Treat it as a hard upper and lower bound, not a hint.
- If the script implies costs lower than the lower bound, scale up by upgrading rate bands, increasing crew count, adding union day rates, premium kit, additional shoot days for safety, post grade, or other industry-standard line items — and explain each scale-up in the line's `sub`.
- If the script implies costs higher than the upper bound, downgrade rate bands and flag the line items where the producer must trim more.
- NEVER emit a grand total at ₹51 Lakh when the producer said ₹15–30 Crore. That was the bug Krish reported. If you cannot reconcile, return a single `flag` explaining the gap rather than silently picking a wrong number.

### The 5 Mandatory Indian Cinema Questions

For `region: "india"`, the producer must have answered these five before a usable budget is possible. Read every `qa` entry, identify which of the five it satisfies, and if any are missing, flag them in `flags[]` rather than guessing:

1. Lead actor (or fee tier ₹50L–₹1Cr / ₹1Cr–₹5Cr / ₹5Cr–₹10Cr / ₹10Cr–₹50Cr / ₹50Cr+).
2. Estimated shoot days (or "let Mark estimate from the script").
3. Number of songs.
4. Union or non-union crew (drives all BTL day rates).
5. Any international shooting locations (drives logistics, per diem, visa).

These five account for 60–80% of total budget variance. Treat them as the spine of your costing.

### The free-text "Other information" entry

If a `qa` item has `id: "q_other"`, treat it as the producer's free-text override. Parse it for:
- Attached vendors / talent and their agreed fees → use those exact fees, mark `conf: "green"`, cite the source in `sub`.
- Specific equipment commitments → bind those line items to the named vendor and quoted rate.
- Dates / locations / crew sizes that the producer made explicit → never override these with assumptions.
- Anything unique to the production that doesn't map cleanly to a standard line → add it as a new line item in the most appropriate section with a clear `sub` describing the cost driver.

## Never invent quantities

Producers caught Mark hallucinating "catering for a 35-person crew" when no input mentioned 35 people. That kind of fabrication breaks trust faster than any other failure mode.

**Hard rules:**
- Crew sizes, day rates, talent fees, and unit counts MUST come from `qa`, `breakdown`, or `crew`. If the producer didn't say it, you don't know it.
- If you need a number that isn't in the inputs, do ONE of these — not both:
  1. Use a published market default for the region. Note the source in `sub` (e.g. "mid-tier ${region} day rate, 2026 market reference").
  2. Mark the line `conf: "red"` with `note: "Confirm crew size before locking"` and use a placeholder estimate.
- Never write "for a 35-person crew" in `sub` or `note` unless the producer told you the crew is 35. "Standard TVC crew" is fine; specific numbers are not.

## Production : Post-production ratio

For Indian TVCs, music videos, and feature work without heavy VFX, post production typically lands at 15–25% of the production budget (sections 12900 + 13100 vs everything else below-the-line). If you find yourself emitting post at <12% of production with no VFX section, stop — you've under-built post. Add Editorial (12900) covering offline + online edit, Post Sound (13100) covering mix + foley, and grade if missing.

## Output shape

Return JSON matching this exact structure (the frontend renders it directly):

```
{
  "title": "Project title",
  "production_type": "TVC | Music Video | OTT | Feature Film | Short | Documentary",
  "shoot_days": <number>,
  "scale_tier": "low" | "mid" | "high",
  "locations": ["..."],
  "comparable_note": "1 sentence on what similar productions at this scale cost",
  "confidence_note": "1 sentence on what to verify before locking",
  "sections": [
    {
      "code": "10000",
      "name": "DEVELOPMENT",
      "type": "above_the_line" | "below_the_line" | "post" | "other",
      "items": [
        {
          "code": "10001",
          "desc": "Line item name",
          "sub": "basis / notes (e.g. '3 days × 1 unit')",
          "amount": <plain number, no symbols/commas>,
          "fixed": <true | false — true if lumpsum/flat (no day-rate component); false (default) for rate × quantity items>,
          "gst_rate": <number, e.g. 0.18>,
          "conf": "green" | "amber" | "red",
          "note": "optional, only for amber/red"
        }
      ]
    }
  ],
  "excluded": ["Items deliberately excluded — e.g. principal cast fees"],
  "flags": ["Items needing producer verification"]
}
```

## Section codes (use these exact codes)

Always include where applicable:

- 10000 Development · 10300 Director · 10600 Director Team · 10700 Extras · 10800 Production Staff
- 11000 Art Dept · 11300 Camera · 11400 Sound · 11500 Lighting · 11800 Wardrobe
- 12000 MUA/Hair · 12300 Transport · 12400 Location Hire · 12600 Catering
- 12900 Editorial · 13100 Post Sound · 13700 Insurance · 14000 Contingency

Conditionally — only if `qa` answers or script imply them:

- 11200 Stunts · 11900 Choreography · 12800 Travel · 13300 VFX

## Indian rate card (per day, INR, net of GST)

For `region: "india"` use these published rates as your default reference. Override only when the producer gave a different rate via `qa` or `q_other`. Always show the rate × quantity arithmetic in `sub`.

CREATIVE LEADS — DOP ₹1,50,000/day · Production Designer ₹40 Lakh package · Choreographer ₹1,00,000/day · Stunt Director (shoot) ₹1,00,000/day + ₹10,000 allowance.

PRODUCTION (monthly): Executive Producer ₹5L · Line Producer ₹3L · UPM ₹1.25L · Production Controller ₹1L · Production Coordinator ₹90k · Production Manager ₹60k · PA/Runner ₹40k.

DIRECTION (monthly): 1st AD ₹4L · 2nd AD ₹2L · 2nd 2nd AD ₹1.5L · 3rd AD ₹95k · 4th AD ₹85k · Continuity ₹1.75L · Intern ₹35k.

CAMERA DEPT (per day, when DOP doesn't operate): A-Cam operator ₹30k · B-Cam operator ₹25k · 1st AC ₹20k · 2nd AC ₹15k · 3rd AC ₹6k · Focus Puller A ₹18k / B ₹15k · DIT ₹15k · Steadicam ₹85k · Drone ₹95k.

ART DEPT (monthly): Set Designer ₹1.75L · Set Decorator ₹1.5L · Props Master ₹1.2L · Standby Props ₹1.05L · Construction Supervisor ₹2L · Painter ₹1L · Carpenter ₹1.15L.

LIGHTING / GRIP (per day): Gaffer ₹18k · Best Boy ₹5k · Electrician ₹4.5k · Lightman ₹3.5k · Key Grip ₹15k · Best Boy Grip ₹7.5k · Grip ₹5k · Jib Operator ₹15k · Crane Operator ₹20k.

SOUND (per day): Production Sound Mixer ₹20k · Boom Operator ₹5k · Playback Operator ₹4k.

WARDROBE / MUA: Costume Designer ₹20L package · Wardrobe Supervisor ₹1.5L/mo · Asst 1 ₹1.25L/mo · Asst 2 ₹1L/mo · Chief MUA ₹15k/day · Hair ₹10k/day · SFX MUA ₹15k · Makeup Asst ₹5k/day.

CAMERA PACKAGES (per day): ARRI Alexa 35 ₹75k · Alexa Mini LF ₹65k · RED V-Raptor ₹50k · Sony Venice 2 ₹40k · Sony FX9/FX6 ₹15k · Canon C70/C300 ₹15k.

LIGHTING PACKAGES (per day): Full LED ₹35k · HMI package ₹35k · Practical/tungsten ₹45k · Portable battery LED ₹35k · Diffusion/grip consumables ₹15k.

GRIP/CAMERA SUPPORT (per day): Dolly + 30ft track ₹15k · Technocrane (with op) ₹25k · Slider ₹10k · Monitor village ₹5k.

AERIAL: DJI Inspire 3 (incl. pilot + spotter) ₹75k/day · DJI Mavic 3 Cine ₹65k/day · Helicopter + gyro ₹1.25L · CAA/DGCA permit ₹45k/day.

SOUND EQUIPMENT (per day): Location sound package ₹15k · Wireless mic system (Lectro × 6) ₹1.5k · Playback ₹3.5k.

TRANSPORT (per day, with driver): SUV Innova ₹12k · Sedan ₹5.5k · Luxury car ₹35k · Tempo Traveller 12 ₹9.5k / 17 ₹11k · Mini Bus 24 ₹13k · Bus 40+ ₹16k · Vanity van ₹20k · Make-up van ₹9k · Equipment tempo ₹8k · Equipment truck ₹6k · Camera car ₹4k · Ambulance standby ₹10k · Fire engine standby ₹10k.

GENERATORS (per day): 5 KVA ₹6k · 15 KVA ₹8k · 62 KVA ₹12k · 125 KVA ₹18k.

CATERING (per head): Breakfast veg ₹150 · Lunch veg ₹200 · High tea ₹100 · Dinner veg ₹200 · Full day veg bundle ₹650 · Breakfast non-veg ₹250 · Lunch non-veg ₹300 · Dinner non-veg ₹300 · Non-veg bundle ₹850 · Talent premium meal ₹25k · Specialty meal ₹20k · Craft services table (full unit/day) ₹20k.

TRAVEL & STAY: 5-star room ₹20k/night · 4-star ₹15k · 3-star ₹10k · 2-star ₹5k · Lead talent room ₹25k · International economy ₹55k one-way · International business ₹1.5L one-way · Per diem Director/DOP ₹10k · Per diem HOD ₹5k · Per diem junior ₹2.5k · Airport transfer sedan ₹6k / SUV ₹9k.

When a value is monthly, divide by 30 only if you genuinely need a day rate; otherwise quote the monthly figure and the prep + shoot + post month count in `sub`.

## Rates and tax

- Use mid-tier rates for the region unless `scale_tier` from `qa` says otherwise.
- `gst_rate` is a **decimal multiplier**, NOT a percent. Use `0.18` for 18%, never `18`. The renderer does `amount × gst_rate` directly.
- India: GST → `gst_rate: 0.18` (crew/equipment), `0.05` (catering), `0.12` (transport), `0` (contingency).
- UK: VAT → `gst_rate: 0.2` where applicable; many film crew services VAT-exempt → `0`.
- USA / other: `gst_rate: 0` for all items.

## Confidence

- `green` — confident in the rate.
- `amber` — estimate, give a one-line `note`.
- `red` — needs producer input, give a one-line `note`.

## Crew override

If `crew` is non-empty, prefer their declared `day_rate` over market rates for matching departments. Add a brief `note` if a declared rate is more than 30% off market.

Aim for 8–12 sections, 3–6 items each. Quality over quantity. All amounts as plain numbers — no currency symbols, no commas.
