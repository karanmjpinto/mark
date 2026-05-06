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

export default async function ({ init, payload }: FlueContext) {
  const input = v.parse(Payload, payload);
  const agent = await init({ model: 'anthropic/claude-sonnet-4-6' });
  const session = await agent.session();

  const result = await session.skill('generate-budget', {
    args: {
      script: input.script,
      region: input.region,
      currency: input.currency,
      qa: input.qa,
      breakdown: input.breakdown ?? null,
      project: input.project ?? null,
      crew: input.crew,
    },
    result: BudgetResult,
  });

  return result;
}
