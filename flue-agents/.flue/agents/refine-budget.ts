import type { FlueContext } from '@flue/sdk/client';
import * as v from 'valibot';

export const triggers = { webhook: true };

// Mirrors generate-budget's output schema. Refining returns a budget with the
// same shape so the frontend can re-render with no special-casing.
const Currency = v.object({ code: v.string(), symbol: v.string() });

const LineItem = v.object({
  code: v.string(),
  desc: v.string(),
  sub: v.optional(v.string(), ''),
  amount: v.number(),
  fixed: v.optional(v.boolean()),
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
  // What changed in this revision — short bullets the UI can show as a diff.
  revision_notes: v.optional(v.array(v.string()), []),
});

const Payload = v.object({
  budget: v.looseObject({}),                    // current budget JSON
  instruction: v.string(),                       // free-text producer ask
  currency: v.optional(Currency),
  region: v.optional(v.string()),
});

const PROMPT_TEMPLATE = `You are an expert film/TVC line producer revising an existing budget. The producer will tell you what to change. Return ONLY valid JSON, no markdown fences.

CURRENT BUDGET
\`\`\`json
{BUDGET_JSON}
\`\`\`

PRODUCER INSTRUCTION
"""
{INSTRUCTION}
"""

REVISION RULES
- Make ONLY the changes the producer asked for. Do not silently re-tune unrelated line items, rates, or sections — that's how trust gets broken.
- If the instruction is ambiguous, pick the most conservative interpretation (smaller change, not bigger) and say so in \`revision_notes\`.
- If the instruction conflicts with a structural truth (e.g. "remove all post" — but the production type is OTT and post is required), comply with the producer but flag it in \`flags\`.
- Preserve every untouched field exactly: same section codes, same line item codes, same descriptions, same confidence colors. Only the items the instruction targets should change.
- New line items are allowed only if the instruction explicitly adds something. Use the next free numeric code in the relevant section's range.
- Keep the SAME output schema as the input budget plus a \`revision_notes\` array of 1–4 short strings describing what changed (e.g. "Director fee reduced from ₹15L to ₹12L per producer instruction").

OUTPUT SHAPE — same as the input budget, plus revision_notes:
{ "title": "...", "production_type": "...", "shoot_days": <n>, "scale_tier": "...", "locations": [...], "comparable_note": "...", "confidence_note": "...", "sections": [...], "excluded": [...], "flags": [...], "revision_notes": ["..."] }

All amounts as plain numbers, no symbols, no commas.`;

export default async function ({ init, payload }: FlueContext) {
  const input = v.parse(Payload, payload);
  const agent = await init({ model: 'anthropic/claude-haiku-4-5-20251001' });
  const session = await agent.session();

  const prompt = PROMPT_TEMPLATE
    .replace('{BUDGET_JSON}', JSON.stringify(input.budget, null, 2))
    .replace('{INSTRUCTION}', input.instruction);

  const result = await session.prompt(prompt, { result: BudgetResult });
  return result;
}
