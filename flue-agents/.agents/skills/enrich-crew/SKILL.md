---
name: enrich-crew
description: Enrich a crew member's record with market-rate day rates, dietary norms, and role-fit notes. Use when the user wants to fill in missing crew details.
---

Given a `crew_member` and the parent `project`, fill in fields that are blank or implausible. Do not overwrite fields the user has already set unless they are clearly wrong (e.g. day_rate of 0).

Inputs in `args`:
- `crew_member` — current record (name, role_title, department, day_rate, etc.)
- `project` — parent project (project_type, currency, shoot dates, brief)

## What to enrich

- `suggested_day_rate` — market rate for this role on this kind of project, in `project.currency`. Give a single number, not a range.
- `rate_rationale` — one sentence explaining the rate (e.g. "Mid-tier DP day rate for INR-market TVC, 2026").
- `role_fit_notes` — one or two sentences on whether the declared role/department is appropriate for the brief, and any gaps.
- `flags` — array of strings for anything the producer should double-check (e.g. "Day rate 60% below market — confirm scope", "No dietary info — required for catering").

## Output

Return:
- `suggested_day_rate` (number)
- `rate_rationale` (string)
- `role_fit_notes` (string)
- `flags` (array of strings)

Be honest. If the role doesn't fit the project, say so in `flags`. Do not fabricate credits or experience.
