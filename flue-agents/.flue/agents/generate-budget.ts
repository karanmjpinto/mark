import type { FlueContext } from '@flue/sdk/client';
import * as v from 'valibot';

export const triggers = { webhook: true };

const QA = v.object({
  id: v.string(),
  question: v.string(),
  answer: v.string(),
  options: v.optional(v.array(v.string())),
});

const Currency = v.object({
  code: v.string(),
  symbol: v.string(),
});

const LineItem = v.object({
  code: v.string(),
  desc: v.string(),
  sub: v.optional(v.string(), ''),
  amount: v.number(),
  gst_rate: v.number(),
  conf: v.picklist(['green', 'amber', 'red']),
  note: v.optional(v.string()),
});

const Section = v.object({
  code: v.string(),
  name: v.string(),
  type: v.picklist(['above_the_line', 'below_the_line', 'post', 'other']),
  items: v.array(LineItem),
});

const BudgetResult = v.object({
  title: v.string(),
  production_type: v.string(),
  shoot_days: v.number(),
  scale_tier: v.picklist(['low', 'mid', 'high']),
  locations: v.array(v.string()),
  comparable_note: v.string(),
  confidence_note: v.string(),
  sections: v.array(Section),
  excluded: v.array(v.string()),
  flags: v.array(v.string()),
});

const Breakdown = v.object({
  total_scenes: v.number(),
  int_count: v.number(),
  ext_count: v.number(),
  day_count: v.number(),
  night_count: v.number(),
  unique_locations: v.array(v.object({ name: v.string(), scene_count: v.number() })),
  characters: v.array(v.object({ name: v.string(), scene_count: v.number() })),
});

const Payload = v.object({
  script: v.string(),
  region: v.picklist(['india', 'uk', 'usa', 'hollywood', 'other']),
  currency: Currency,
  qa: v.array(QA),
  breakdown: v.optional(Breakdown),
  project: v.optional(v.looseObject({})),
  crew: v.optional(v.array(v.looseObject({})), []),
});

// Skill body inlined as a prompt template. Skill files in .agents/skills/ require
// either a local sandbox mount (which Railway containers don't permit) or a
// pre-seeded virtual sandbox; inlining sidesteps both.
const PROMPT_TEMPLATE = `You are an expert film/TVC line producer. Build a complete itemised production budget from the inputs below. Return ONLY valid JSON, no markdown fences.

INPUTS
\`\`\`json
{ARGS_JSON}
\`\`\`

GROUND TRUTH
- Every \`qa\` answer is the producer's word. Do not contradict it. Each answer must map to one or more line items: locations → location hire / transport / permits; shoot_days → day-rate multiplier; scale_tier → rate band; DOP attached → camera day-rate band; stunts/VFX/choreography flagged → those sections must appear.
- When \`breakdown\` is present, use it as canonical: \`total_scenes\` is the scene count, \`unique_locations\` drives Location Hire (12400) sizing, \`int_count\`/\`ext_count\` informs lighting/grip, \`night_count\` informs lighting package + catering, lead \`characters\` (high scene_count) drive talent fees.
- If qa and breakdown conflict, prefer qa for scheduling and breakdown for content; explain the assumption in \`flags\`.
- If \`crew\` is non-empty, prefer their declared \`day_rate\` for matching departments; if a declared rate is >30% off market, add a brief \`note\`.

NEVER INVENT QUANTITIES (anti-hallucination rule)
- Crew sizes, day rates, talent fees, unit counts MUST come from \`qa\`, \`breakdown\`, or \`crew\`. If the producer didn't say it, you don't know it.
- Do NOT write specific numbers in \`sub\` or \`note\` that the inputs don't support. "Catering for 35 crew" is forbidden if no input mentioned 35; write "Catering for full crew + cast" instead, OR mark the line \`conf: "red"\` with \`note: "Confirm crew size before locking"\`.
- "Standard TVC crew rate" / "mid-tier 2026 market" / "industry default" are acceptable phrasings. Inventing precise headcounts or rates from thin air is not.

PRODUCTION : POST-PRODUCTION RATIO
- For India / TVC / music video / feature work without heavy VFX, post production should land at 15–25% of production (sections 12900 + 13100 vs everything below-the-line + above-the-line excluding contingency).
- If you don't include 12900 (Editorial) or 13100 (Post Sound) you are under-building post. Always include both for any production with edit + sound deliverables.

OUTPUT SHAPE — return exactly this structure
{
  "title": "<project title>",
  "production_type": "TVC | Music Video | OTT | Feature Film | Short | Documentary",
  "shoot_days": <number>,
  "scale_tier": "low" | "mid" | "high",
  "locations": ["..."],
  "comparable_note": "<1 sentence on what similar productions at this scale cost>",
  "confidence_note": "<1 sentence on what to verify before locking>",
  "sections": [
    {
      "code": "10000",
      "name": "<SECTION NAME>",
      "type": "above_the_line" | "below_the_line" | "post" | "other",
      "items": [
        { "code": "10001", "desc": "<line item>", "sub": "<basis>", "amount": <number>, "gst_rate": <number>, "conf": "green"|"amber"|"red", "note": "<optional>" }
      ]
    }
  ],
  "excluded": ["..."],
  "flags": ["..."]
}

SECTION CODES (use these exact codes; include where applicable):
10000 Development · 10300 Director · 10600 Director Team · 10700 Extras · 10800 Production Staff
11000 Art Dept · 11300 Camera · 11400 Sound · 11500 Lighting · 11800 Wardrobe
12000 MUA/Hair · 12300 Transport · 12400 Location Hire · 12600 Catering
12900 Editorial · 13100 Post Sound · 13700 Insurance · 14000 Contingency
Conditionally (only if qa/script imply): 11200 Stunts · 11900 Choreography · 12800 Travel · 13300 VFX

TAX
- india: GST 18% crew/equipment, 5% catering, 12% transport, 0% contingency.
- uk: VAT 20% where applicable; many film crew services VAT-exempt → \`gst_rate: 0\`.
- usa / hollywood / other: \`gst_rate: 0\` for all items.

CONFIDENCE
- green = confident, amber = estimate (one-line note), red = needs producer input (one-line note).

Aim for 8–12 sections, 3–6 items each. All amounts as plain numbers — no symbols, no commas.`;

export default async function ({ init, payload }: FlueContext) {
  const input = v.parse(Payload, payload);
  // Haiku, not Sonnet: Railway's edge closes connections after ~60s without
  // response headers. session.prompt() doesn't stream — it returns only after
  // Claude finishes, so total latency must fit under that ceiling.
  const agent = await init({ model: 'anthropic/claude-haiku-4-5-20251001' });
  const session = await agent.session();

  const args = {
    script: input.script,
    region: input.region,
    currency: input.currency,
    qa: input.qa,
    breakdown: input.breakdown ?? null,
    project: input.project ?? null,
    crew: input.crew,
  };
  const prompt = PROMPT_TEMPLATE.replace('{ARGS_JSON}', JSON.stringify(args, null, 2));

  const result = await session.prompt(prompt, { result: BudgetResult });
  return result;
}
