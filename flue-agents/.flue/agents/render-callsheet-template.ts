import type { FlueContext } from '@flue/sdk/client';
import * as v from 'valibot';

export const triggers = { webhook: true };

// Renders a call sheet in the producer's OWN uploaded template format. Given the
// call-sheet data plus the extracted text of the producer's template, the agent
// returns a single self-contained HTML document that mirrors the template's
// structure (sections, field labels, table columns, ordering) populated with the
// real data. This is the "Format of template uploaded" branch — the standard
// AskMark format is rendered client-side and never touches this agent.

const Payload = v.object({
  callsheet: v.looseObject({}),
  template_text: v.string(),
});

const RenderResult = v.object({
  html: v.string(),
});

const PROMPT_TEMPLATE = `You are an expert film/TVC line producer and front-end engineer. The producer has uploaded their OWN call-sheet template and wants this call sheet rendered to match THEIR template's format exactly — not a generic one.

You are given:
1. CALL SHEET DATA — the real production data to place into the document (JSON).
2. PRODUCER TEMPLATE — the extracted text of the producer's own call-sheet template. Treat it as the source of truth for layout: which sections exist, their order, the field labels/wording, the table columns, and the overall structure.

CALL SHEET DATA
\`\`\`json
{CALLSHEET_JSON}
\`\`\`

PRODUCER TEMPLATE (extracted text)
"""
{TEMPLATE_TEXT}
"""

RULES
- Reproduce the PRODUCER TEMPLATE's structure as faithfully as the extracted text allows: same section headings and order, same field labels/wording, same table columns and grouping. The producer chose this format on purpose.
- Populate every field from CALL SHEET DATA. Map the data to the template's labels even when the wording differs (e.g. data "unit_call" → a template label like "Crew Call" or "Unit Call Time"). Group crew by department when the template shows departmental grouping.
- If the template references a field that has no value in the data, leave the label and show an em dash (—). Do NOT invent data — never fabricate names, numbers, addresses, or times that aren't in CALL SHEET DATA.
- If the template clearly omits a section, omit it. Do not add AskMark's standard sections that the producer's template doesn't have.
- Output a SINGLE self-contained HTML fragment. Wrap the whole document in a top-level \`<div class="callsheet-doc">\` so the host app can export it. Inline all styling with a \`<style>\` block scoped under \`.callsheet-doc\` or with inline \`style=\` attributes. No external assets, no <script>, no markdown.
- Use clean print-friendly styling (white background, black text, A4-ish width). Prefer Helvetica/Arial.

OUTPUT SHAPE
{ "html": "<div class=\\"callsheet-doc\\">...full document...</div>" }

Return ONLY valid JSON, no markdown fences.`;

export default async function ({ init, payload }: FlueContext) {
  const input = v.parse(Payload, payload);
  const agent = await init({ model: 'anthropic/claude-sonnet-4-6' });
  const session = await agent.session();

  const prompt = PROMPT_TEMPLATE
    .replace('{CALLSHEET_JSON}', JSON.stringify(input.callsheet, null, 2))
    .replace('{TEMPLATE_TEXT}', input.template_text);

  const result = await session.prompt(prompt, { result: RenderResult });
  return result;
}
