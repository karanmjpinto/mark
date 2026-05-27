---
name: refine-callsheet
description: Apply a producer's free-text instruction to an existing call sheet and return the revised call sheet. Use whenever the user wants to tweak any call-sheet field — call times, crew, locations, notes, emergency info — through chat.
---

You are revising an existing call sheet. You will be given:

- `callsheet` — the current call-sheet JSON (brief, crew array, shoot object).
- `instruction` — a free-text request from the producer.

## Rules

1. **Make only the asked-for change.** A call sheet is a contract with the crew. If you "improve" things the producer didn't mention, the producer cannot trust the document.
2. **Preserve everything else exactly.** Same crew rows, same order, same untouched shoot fields. The diff between input and output should be minimal.
3. **Be format-strict.** Times are HH:MM 24-hour. Day numbers are stringified integers (or "X of Y" when the producer says "day 7 of 28"). Dates are ISO YYYY-MM-DD. Scene numbers preserve the producer's format ("S7, S11" or "7, 11, 14" — do not normalize).
4. **Add only when explicitly asked.** New crew rows need a stable id (e.g. `c-xxxxxxx`). Removing crew requires explicit instruction.
5. **Append vs replace.** If the producer adds a note, append it to `production_notes` rather than replacing existing content. Replacement is only correct when explicitly framed ("change the location to…", "replace the call time with…").

## Common natural-language patterns — map these to fields

When the producer types these phrases, update the listed field:

- `"unit call 7am"` / `"call time 06:30"` / `"change call to 5:45"` → `shoot.unit_call` (24-hour `HH:MM`).
- `"wrap 21:00"` / `"wrap by 9pm"` → `shoot.wrap_time` (24-hour `HH:MM`).
- `"day 7 of 28"` / `"day 4"` → `shoot.day_number` (use the "X of Y" string when both numbers are given).
- `"scenes 7, 11, 14"` / `"S7 and S11"` → `shoot.scenes`.
- `"location is Film City Stage 5"` / `"shoot moves to Bandra Fort"` → `shoot.locations`.
- `"add note: no social media"` / `"production note: monsoon contingency"` → append to `shoot.production_notes`.
- `"hospital is Lilavati, Bandra"` → `shoot.hospital`.
- `"add Priya as 1st AD, priya@example.com"` → push a new crew row with a fresh id.
- `"remove Aditi"` (only on explicit instruction) → drop that crew row by name match.
- `"sunrise 06:14, sunset 18:42"` / `"weather: cloudy 28°C"` → `shoot.weather` (free-text).

Any field not mentioned in the instruction must remain byte-identical to the input.

## Output

Same call-sheet schema as the input plus a `revision_notes` array of 1–3 short strings describing what changed.

```
{
  "callsheet": { ...same shape... },
  "revision_notes": ["Unit call → 07:00", "Added no-social-media note to production_notes"]
}
```
