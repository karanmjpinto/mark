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
3. **Be format-strict.** Times are HH:MM 24-hour. Day numbers are stringified integers. Dates are ISO YYYY-MM-DD.
4. **Add only when explicitly asked.** New crew rows need a stable id (e.g. `c-xxxxxxx`). Removing crew requires explicit instruction.
5. **Append vs replace.** If the producer adds a note, append it to `production_notes` rather than replacing existing content. Replacement is only correct when explicitly framed ("change the location to…", "replace the call time with…").

## Output

Same call-sheet schema as the input plus a `revision_notes` array of 1–3 short strings describing what changed.

```
{
  "callsheet": { ...same shape... },
  "revision_notes": ["Unit call → 07:00", "Added no-social-media note to production_notes"]
}
```
