import type { FlueContext } from '@flue/sdk/client';
import * as v from 'valibot';

export const triggers = { webhook: true };

// The agent returns the same call-sheet shape the frontend already holds, plus
// a short revision_notes array describing what changed. Mirrors refine-budget.
const Crew = v.object({
  id: v.optional(v.string()),
  name: v.string(),
  role: v.string(),
  department: v.optional(v.string(), ''),
  email: v.optional(v.string(), ''),
  phone: v.optional(v.string(), ''),
});

const Shoot = v.object({
  date: v.optional(v.string(), ''),
  day_number: v.optional(v.string(), ''),
  unit_call: v.optional(v.string(), ''),
  wrap_time: v.optional(v.string(), ''),
  scenes: v.optional(v.string(), ''),
  locations: v.optional(v.string(), ''),
  hospital: v.optional(v.string(), ''),
  police: v.optional(v.string(), ''),
  weather: v.optional(v.string(), ''),
  production_notes: v.optional(v.string(), ''),
  invoice_address: v.optional(v.string(), ''),
});

const CallSheet = v.object({
  brief: v.optional(v.string(), ''),
  brief_source: v.optional(v.string(), 'type'),
  project_title: v.optional(v.string(), ''),
  crew: v.array(Crew),
  shoot: Shoot,
});

const RefineResult = v.object({
  callsheet: CallSheet,
  revision_notes: v.optional(v.array(v.string()), []),
});

const Payload = v.object({
  callsheet: v.looseObject({}),
  instruction: v.string(),
});

const PROMPT_TEMPLATE = `You are an expert film/TVC line producer revising a call sheet. The producer will tell you what to change. Return ONLY valid JSON, no markdown.

CURRENT CALL SHEET
\`\`\`json
{CALLSHEET_JSON}
\`\`\`

PRODUCER INSTRUCTION
"""
{INSTRUCTION}
"""

REVISION RULES
- Make ONLY the changes the producer asked for. Do not silently re-tune unrelated fields. Producers lose trust the moment Mark "improves" something they didn't ask about.
- If the instruction is ambiguous, pick the most conservative reading and note it in \`revision_notes\`.
- Preserve every untouched field exactly: same crew rows in the same order, same shoot fields, same brief.
- Times are in 24-hour HH:MM format (e.g. "07:00", "20:30"). Day numbers are strings like "3". Dates are ISO YYYY-MM-DD.
- New crew members are allowed only if explicitly added. Generate a stable id like "c-xxxxxxx" (random 7-char) for each new row.
- Removing crew is allowed only when the producer explicitly says so.

ADDING / EDITING SHOOT FIELDS
- "Change the unit call to 07:00" → set shoot.unit_call to "07:00".
- "Wrap at 22:00" → shoot.wrap_time = "22:00".
- "Add the nearest hospital — Barnet Hospital, Wellhouse Lane, EN5 3DL" → shoot.hospital = "Barnet Hospital, Wellhouse Lane, EN5 3DL".
- "Strict no social media" → append a sentence to shoot.production_notes (do not replace existing notes).
- "Change location to Studio 1, Pinewood" → replace shoot.locations.

OUTPUT SHAPE
{
  "callsheet": { ...same shape as input, with the producer's changes applied... },
  "revision_notes": ["1–3 short bullets describing what changed"]
}

Do NOT include the brief, brief_source, project_title fields if they were not in the input. Match the input shape exactly otherwise.`;

export default async function ({ init, payload }: FlueContext) {
  const input = v.parse(Payload, payload);
  const agent = await init({ model: 'anthropic/claude-haiku-4-5-20251001' });
  const session = await agent.session();

  const prompt = PROMPT_TEMPLATE
    .replace('{CALLSHEET_JSON}', JSON.stringify(input.callsheet, null, 2))
    .replace('{INSTRUCTION}', input.instruction);

  const result = await session.prompt(prompt, { result: RefineResult });
  return result;
}
