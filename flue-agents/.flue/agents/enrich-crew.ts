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

export default async function ({ init, payload }: FlueContext) {
  const input = v.parse(Payload, payload);
  const agent = await init({ model: 'anthropic/claude-sonnet-4-6' });
  const session = await agent.session();

  const result = await session.skill('enrich-crew', {
    args: {
      crew_member: input.crew_member,
      project: input.project,
    },
    result: EnrichmentResult,
  });

  return result;
}
