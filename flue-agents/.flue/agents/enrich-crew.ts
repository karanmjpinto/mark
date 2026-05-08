import type { FlueContext } from '@flue/sdk/client';
import * as v from 'valibot';

export const triggers = { webhook: true };

const EnrichmentResult = v.object({
  suggested_day_rate: v.number(),
  rate_rationale: v.string(),
  role_fit_notes: v.string(),
  flags: v.array(v.string()),
});

const Payload = v.object({
  crew_member: v.looseObject({
    id: v.string(),
    name: v.string(),
  }),
  project: v.looseObject({
    id: v.string(),
    currency: v.optional(v.string(), 'INR'),
  }),
});

const PROMPT_TEMPLATE = `You are an expert film/TVC line producer. Enrich a crew member's record. Return ONLY valid JSON.

INPUTS
\`\`\`json
{ARGS_JSON}
\`\`\`

Fill in:
- \`suggested_day_rate\` (number) — market rate for this role on this kind of project, in \`project.currency\`. Single number, not a range. Don't overwrite \`crew_member.day_rate\` if it's set and plausible — use it as anchor.
- \`rate_rationale\` (string) — one sentence explaining the rate (e.g. "Mid-tier DP day rate for INR-market TVC, 2026").
- \`role_fit_notes\` (string) — one or two sentences on whether the declared role/department fits the brief, and any gaps.
- \`flags\` (array of strings) — anything the producer should double-check (e.g. "Day rate 60% below market — confirm scope", "No dietary info — required for catering"). Empty array if no concerns.

Output shape:
{ "suggested_day_rate": <number>, "rate_rationale": "<string>", "role_fit_notes": "<string>", "flags": ["..."] }

Be honest. If the role doesn't fit the project, say so in \`flags\`. Do not fabricate credits or experience.`;

export default async function ({ init, payload }: FlueContext) {
  const input = v.parse(Payload, payload);
  const agent = await init({ model: 'anthropic/claude-haiku-4-5-20251001' });
  const session = await agent.session();

  const args = { crew_member: input.crew_member, project: input.project };
  const prompt = PROMPT_TEMPLATE.replace('{ARGS_JSON}', JSON.stringify(args, null, 2));

  const result = await session.prompt(prompt, { result: EnrichmentResult });
  return result;
}
