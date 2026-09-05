"""
schedule.py — scenes to a shooting schedule, and a Day-out-of-Days.

Why this exists: Mark could price a production but not sequence one. Without a
schedule there is no computed day count (so `shoot_days` was whatever the
producer typed in an answer), no way to answer "what if we lose a day", and no
upstream for a call sheet — every competitor generates call sheets *from* a
schedule and Mark was generating them from nothing.

The whole module is pure functions over plain dicts. No storage, no network, no
model call. That is deliberate: scheduling is arithmetic and craft rules, not a
place to spend a language model, and it means the logic is testable offline in
CI alongside the budget invariants.

Craft rules encoded here, in the order they matter:

  1. **Company moves are the expensive thing.** Scenes at one location are kept
     contiguous even when that makes a day slightly over- or under-packed.
  2. **Day and night don't mix casually.** Within a location, day work is
     scheduled before night work, and a day boundary prefers to fall on the
     day/night seam so a unit isn't asked to turn around inside one call.
  3. **A day has a capacity, measured in eighths.** Default 40 eighths (5 pages)
     for drama; TVC and music-video work is set by shot count rather than pages,
     so when the producer states a day count that is treated as binding and the
     capacity is derived from it instead.

Everything the scheduler assumes about a scene comes from `/script/parse`, which
now emits a per-scene list. When only the older aggregate summary is available,
`scenes_from_summary()` synthesises a plausible scene list and marks it
`synthetic: true` — a schedule built from it is a shape, not a plan, and the
`warnings` array says so.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Optional

DEFAULT_EIGHTHS_PER_DAY = 40  # 5 pages — mainstream drama default
MIN_EIGHTHS = 1


# ── inputs ────────────────────────────────────────────────────────────────────

def _norm_region(value: str) -> str:
    v = (value or "").upper()
    has_int, has_ext = "INT" in v, "EXT" in v
    if has_int and has_ext:
        return "INT/EXT"
    if has_ext:
        return "EXT"
    if has_int:
        return "INT"
    return "INT"


def _norm_time(value: Any) -> str:
    if isinstance(value, list):
        value = " ".join(str(v) for v in value)
    v = (value or "").upper()
    if any(k in v for k in ("NIGHT", "EVENING", "DUSK")):
        return "NIGHT"
    if any(k in v for k in ("DAWN", "MORNING", "AFTERNOON", "DAY", "CONTINUOUS")):
        return "DAY"
    return "DAY"


def normalise_scene(raw: dict, index: int) -> dict:
    """One scene, in the shape the scheduler works with. Tolerant of the parser's
    shape and of hand-written input, because producers will paste both."""
    location = (raw.get("location") or raw.get("set") or "UNKNOWN").strip() or "UNKNOWN"
    eighths = raw.get("eighths")
    if not isinstance(eighths, (int, float)) or eighths < MIN_EIGHTHS:
        eighths = MIN_EIGHTHS
    return {
        "number": raw.get("number") or raw.get("scene_number") or (index + 1),
        "heading": (raw.get("heading") or "").strip(),
        "region": _norm_region(raw.get("region") or raw.get("int_ext") or ""),
        "location": location,
        "time": _norm_time(raw.get("time")),
        "characters": [str(c).strip() for c in (raw.get("characters") or []) if str(c).strip()],
        "eighths": int(round(eighths)),
        "synthetic": bool(raw.get("synthetic")),
    }


def scenes_from_summary(summary: dict) -> list[dict]:
    """Fallback for the aggregate-only breakdown: distribute the scene count
    across the known locations, honouring the INT/EXT and day/night ratios.

    This is a shape, not a plan. Every scene it emits is marked synthetic and any
    schedule built from it carries a warning — a producer must never be shown a
    synthesised strip as though it came off their script.
    """
    total = int(summary.get("total_scenes") or 0)
    locations = summary.get("unique_locations") or []
    if total <= 0 or not locations:
        return []

    int_c = float(summary.get("int_count") or 0)
    ext_c = float(summary.get("ext_count") or 0)
    day_c = float(summary.get("day_count") or 0)
    night_c = float(summary.get("night_count") or 0)
    ext_share = ext_c / (int_c + ext_c) if (int_c + ext_c) else 0.0
    night_share = night_c / (day_c + night_c) if (day_c + night_c) else 0.0

    named = sum(int(l.get("scene_count") or 0) for l in locations)
    scenes: list[dict] = []
    n = 0
    for loc in locations:
        count = int(loc.get("scene_count") or 0)
        if named and named < total:  # scale up so the synthetic list totals `total`
            count = max(1, round(count * total / named))
        for i in range(count):
            if len(scenes) >= total:
                break
            n += 1
            region = "EXT" if (i / max(count, 1)) < ext_share else "INT"
            tod = "NIGHT" if ((n % 100) / 100.0) < night_share else "DAY"
            scenes.append({
                "number": n,
                "heading": f"{region}. {loc.get('name')} - {tod}",
                "region": region,
                "location": loc.get("name"),
                "time": tod,
                "characters": [],
                "eighths": 8,  # one page apiece — we have nothing better to go on
                "synthetic": True,
            })
    return [normalise_scene(s, i) for i, s in enumerate(scenes[:total])]


# ── the schedule ──────────────────────────────────────────────────────────────

def _blocks(scenes: list[dict]) -> list[dict]:
    """Group scenes into shootable blocks: one location, one time of day.

    Location order follows first appearance in the script rather than size, so a
    schedule stays recognisable to the person who wrote it. Within a location,
    day work precedes night work (rule 2).
    """
    order: list[str] = []
    by_loc: dict[str, list[dict]] = {}
    for sc in scenes:
        loc = sc["location"]
        if loc not in by_loc:
            by_loc[loc] = []
            order.append(loc)
        by_loc[loc].append(sc)

    blocks = []
    for loc in order:
        for tod in ("DAY", "NIGHT"):
            group = [s for s in by_loc[loc] if s["time"] == tod]
            if not group:
                continue
            blocks.append({
                "location": loc,
                "time": tod,
                "scenes": group,
                "eighths": sum(s["eighths"] for s in group),
            })
    return blocks


def _pack(blocks: list[dict], capacity: int, max_days: Optional[int]) -> list[list[dict]]:
    """Fill days to capacity, breaking blocks only when one exceeds a whole day.

    A block is placed whole where it fits. A block larger than a day is split at
    scene boundaries. When `max_days` is set (the producer stated a day count)
    the last day absorbs any overflow rather than inventing a day the producer
    did not budget for — the caller surfaces that as a warning.
    """
    days: list[list[dict]] = []
    current: list[dict] = []
    used = 0

    def flush():
        nonlocal current, used
        if current:
            days.append(current)
            current, used = [], 0

    for block in blocks:
        remaining = list(block["scenes"])
        while remaining:
            if max_days and len(days) >= max_days - 1:
                current.extend(remaining)  # final day takes the rest
                used += sum(s["eighths"] for s in remaining)
                remaining = []
                break
            block_eighths = sum(s["eighths"] for s in remaining)
            if used and used + block_eighths > capacity:
                # Would overflow: start a fresh day unless this block alone can
                # never fit, in which case splitting is unavoidable anyway.
                if block_eighths <= capacity:
                    flush()
                    continue
            take: list[dict] = []
            for sc in remaining:
                if take and used + sum(s["eighths"] for s in take) + sc["eighths"] > capacity:
                    break
                take.append(sc)
            current.extend(take)
            used += sum(s["eighths"] for s in take)
            remaining = remaining[len(take):]
            if remaining:
                flush()
    flush()
    return days


def _order_within_day(scenes: list[dict]) -> list[dict]:
    """Order one day's work: day-only locations first, then locations that carry
    both, then night-only locations.

    Two rules pull against each other here — don't return to a location, and
    don't shoot a day exterior after a night interior. This ordering keeps every
    location's scenes contiguous (the expensive rule) while still pushing night
    work towards the end of the day (the tiring one). Where two locations each
    carry day *and* night work, contiguity wins and the second location's day
    block follows the first's night block; that is rare and a producer would
    re-order it by hand anyway.
    """
    order: list[str] = []
    by_loc: dict[str, dict[str, list[dict]]] = {}
    for sc in scenes:
        loc = sc["location"]
        if loc not in by_loc:
            by_loc[loc] = {"DAY": [], "NIGHT": []}
            order.append(loc)
        by_loc[loc]["NIGHT" if sc["time"] == "NIGHT" else "DAY"].append(sc)

    day_only = [l for l in order if by_loc[l]["DAY"] and not by_loc[l]["NIGHT"]]
    both = [l for l in order if by_loc[l]["DAY"] and by_loc[l]["NIGHT"]]
    night_only = [l for l in order if by_loc[l]["NIGHT"] and not by_loc[l]["DAY"]]

    out: list[dict] = []
    for loc in day_only + both + night_only:
        out.extend(by_loc[loc]["DAY"])
        out.extend(by_loc[loc]["NIGHT"])
    return out


def _day_record(idx: int, scenes: list[dict], start: Optional[date], prev_locations: list[str]) -> dict:
    scenes = _order_within_day(scenes)
    locations = []
    for sc in scenes:
        if sc["location"] not in locations:
            locations.append(sc["location"])
    times = sorted({sc["time"] for sc in scenes})
    cast: list[str] = []
    for sc in scenes:
        for c in sc["characters"]:
            if c not in cast:
                cast.append(c)
    # A move is either arriving somewhere new, or a second location inside a day.
    moved_in = bool(prev_locations) and locations[:1] != prev_locations[-1:]
    return {
        "day": idx + 1,
        "date": (start + timedelta(days=idx)).isoformat() if start else None,
        "unit": "Main Unit",
        # `characters` and `synthetic` travel with the strip, not just the
        # summary: a hand-edited schedule is posted back as these exact records,
        # and dropping the cast here silently empties the Day-out-of-Days on the
        # first drag.
        "scenes": [{k: sc[k] for k in ("number", "heading", "region", "location", "time",
                                       "eighths", "characters", "synthetic")}
                   for sc in scenes],
        "scene_numbers": [sc["number"] for sc in scenes],
        "locations": locations,
        "time_of_day": times,
        "cast": cast,
        "eighths": sum(sc["eighths"] for sc in scenes),
        "pages": round(sum(sc["eighths"] for sc in scenes) / 8, 2),
        "company_moves": max(0, len(locations) - 1) + (1 if moved_in else 0),
        "night_work": "NIGHT" in times,
    }


def build_schedule(
    scenes: list[dict],
    *,
    shoot_days: Optional[int] = None,
    eighths_per_day: int = DEFAULT_EIGHTHS_PER_DAY,
    start_date: Optional[str] = None,
    title: str = "",
) -> dict:
    """Scenes in, shooting schedule out.

    `shoot_days`, when given, is binding — the same rule the budget agent
    follows for a stated budget tier. The scheduler compresses or expands to hit
    it and warns when the result implies an unreasonable day.
    """
    norm = [normalise_scene(s, i) for i, s in enumerate(scenes or [])]
    warnings: list[str] = []
    if not norm:
        return {"days": [], "total_days": 0, "total_eighths": 0, "warnings": ["no scenes supplied"],
                "dood": {"characters": [], "matrix": {}}, "synthetic": False, "title": title}

    if any(s["synthetic"] for s in norm):
        warnings.append("Schedule built from a synthesised scene list (aggregate breakdown only) — "
                        "shape is indicative, scene numbers are not real.")

    total_eighths = sum(s["eighths"] for s in norm)
    if shoot_days and shoot_days > 0:
        capacity = max(MIN_EIGHTHS, math.ceil(total_eighths / shoot_days))
        if capacity > DEFAULT_EIGHTHS_PER_DAY * 2:
            warnings.append(
                f"{shoot_days} days implies {capacity/8:.1f} pages a day — roughly "
                f"{capacity / DEFAULT_EIGHTHS_PER_DAY:.1f}× a normal drama day. Confirm the day count.")
    else:
        capacity = max(MIN_EIGHTHS, int(eighths_per_day))

    start = None
    if start_date:
        try:
            start = date.fromisoformat(start_date)
        except ValueError:
            warnings.append(f"ignored unparseable start_date {start_date!r}")

    packed = _pack(_blocks(norm), capacity, shoot_days)

    days: list[dict] = []
    prev_locations: list[str] = []
    for i, day_scenes in enumerate(packed):
        rec = _day_record(i, day_scenes, start, prev_locations)
        prev_locations = rec["locations"]
        days.append(rec)

    if shoot_days and len(days) != shoot_days:
        warnings.append(f"requested {shoot_days} days, scheduled {len(days)} — "
                        f"blocks could not be packed into the stated count without splitting a location")

    dood = build_dood(norm, days)
    return {
        "title": title,
        "days": days,
        "total_days": len(days),
        "total_scenes": len(norm),
        "total_eighths": total_eighths,
        "total_pages": round(total_eighths / 8, 2),
        "eighths_per_day": capacity,
        "company_moves": sum(d["company_moves"] for d in days),
        "night_days": sum(1 for d in days if d["night_work"]),
        "locations": sorted({s["location"] for s in norm}),
        "dood": dood,
        "synthetic": any(s["synthetic"] for s in norm),
        "warnings": warnings,
    }


def rebuild(day_groups: list[list[dict]], *, start_date: Optional[str] = None,
            title: str = "", eighths_per_day: int = DEFAULT_EIGHTHS_PER_DAY) -> dict:
    """Recompute a schedule from an explicit day → scenes assignment.

    This is what a producer dragging a strip from day 3 to day 4 posts back. The
    assignment is theirs and is honoured exactly — the packer does not get a
    second opinion — but the craft rules that order work *within* a day still
    apply, and every derived figure (pages, moves, cast, DooD, hold days) is
    recomputed here rather than in the browser, so the stored schedule and the
    numbers on screen cannot drift apart.
    """
    warnings: list[str] = []
    start = None
    if start_date:
        try:
            start = date.fromisoformat(start_date)
        except ValueError:
            warnings.append(f"ignored unparseable start_date {start_date!r}")

    norm_groups: list[list[dict]] = []
    all_scenes: list[dict] = []
    for group in day_groups or []:
        scenes = [normalise_scene(s, i) for i, s in enumerate(group or [])]
        norm_groups.append(scenes)
        all_scenes.extend(scenes)

    days: list[dict] = []
    prev_locations: list[str] = []
    for i, scenes in enumerate(norm_groups):
        if not scenes:
            warnings.append(f"day {i + 1} has no scenes on it")
        rec = _day_record(i, scenes, start, prev_locations)
        prev_locations = rec["locations"]
        days.append(rec)

    over = [d["day"] for d in days if d["eighths"] > eighths_per_day * 1.5]
    if over:
        warnings.append(f"day(s) {', '.join(str(d) for d in over)} carry more than 1.5× a normal "
                        f"day's work — check they are shootable")

    total_eighths = sum(s["eighths"] for s in all_scenes)
    return {
        "title": title,
        "days": days,
        "total_days": len(days),
        "total_scenes": len(all_scenes),
        "total_eighths": total_eighths,
        "total_pages": round(total_eighths / 8, 2),
        "eighths_per_day": eighths_per_day,
        "company_moves": sum(d["company_moves"] for d in days),
        "night_days": sum(1 for d in days if d["night_work"]),
        "locations": sorted({s["location"] for s in all_scenes}),
        "dood": build_dood(all_scenes, days),
        "synthetic": any(s["synthetic"] for s in all_scenes),
        "warnings": warnings,
        "hand_edited": True,
    }


# ── Day out of Days ───────────────────────────────────────────────────────────

def build_dood(scenes: list[dict], days: list[dict]) -> dict:
    """Standard DooD codes per character per day.

        S  start        W  work         H  hold (idle between first and last)
        F  finish       SW start+work   WF work+finish   SWF single-day role

    Hold days are the ones that cost money nobody budgeted — an actor carried
    across a gap is paid for the gap. Surfacing them is half the point of the
    document.
    """
    working: dict[str, list[int]] = {}
    for day in days:
        for name in day["cast"]:
            working.setdefault(name, []).append(day["day"])

    matrix: dict[str, dict[int, str]] = {}
    summary = []
    for name, day_numbers in working.items():
        first, last = min(day_numbers), max(day_numbers)
        row: dict[int, str] = {}
        for d in range(first, last + 1):
            works = d in day_numbers
            if d == first and d == last:
                row[d] = "SWF"
            elif d == first:
                row[d] = "SW"
            elif d == last:
                row[d] = "WF"
            elif works:
                row[d] = "W"
            else:
                row[d] = "H"
        matrix[name] = row
        work_days = len(day_numbers)
        hold_days = (last - first + 1) - work_days
        summary.append({
            "character": name,
            "first_day": first,
            "last_day": last,
            "work_days": work_days,
            "hold_days": hold_days,
            "total_days_engaged": last - first + 1,
        })

    summary.sort(key=lambda r: (-r["total_days_engaged"], r["character"]))
    return {
        "characters": summary,
        "matrix": matrix,
        "total_hold_days": sum(r["hold_days"] for r in summary),
    }


# ── links out ─────────────────────────────────────────────────────────────────

def reconcile_with_budget(schedule: dict, budget: dict) -> dict:
    """The check that was impossible before this module existed: does the budget
    pay for the number of days the script actually implies?"""
    sched_days = int(schedule.get("total_days") or 0)
    budget_days = budget.get("shoot_days")
    budget_days = int(budget_days) if isinstance(budget_days, (int, float)) else None
    out = {
        "schedule_days": sched_days,
        "budget_shoot_days": budget_days,
        "matches": budget_days == sched_days if budget_days is not None else None,
        "delta_days": (sched_days - budget_days) if budget_days is not None else None,
        "night_days": schedule.get("night_days", 0),
        "company_moves": schedule.get("company_moves", 0),
        "hold_days": (schedule.get("dood") or {}).get("total_hold_days", 0),
        "notes": [],
    }
    if budget_days is not None and budget_days != sched_days:
        direction = "more" if sched_days > budget_days else "fewer"
        out["notes"].append(
            f"The schedule needs {sched_days} days; the budget pays for {budget_days}. "
            f"Every day-rate line is costed for {abs(sched_days - budget_days)} {direction} day(s) than the shoot requires.")
    if out["hold_days"]:
        out["notes"].append(
            f"{out['hold_days']} cast hold day(s) across the schedule — carried, usually paid, "
            f"and rarely in the budget.")
    if schedule.get("company_moves", 0) > sched_days:
        out["notes"].append(
            f"{schedule['company_moves']} company moves across {sched_days} days — more than one a day. "
            f"Check transport and lost-time allowances.")
    return out


def callsheet_seed(schedule: dict, day_number: int, *, project: Optional[dict] = None,
                   crew: Optional[list] = None) -> dict:
    """Seed the existing call-sheet shape from one day of the schedule.

    Deliberately partial: it fills what the schedule actually knows (date, unit,
    scenes, locations, cast) and leaves call times, weather and hospital to the
    call-sheet agent and the producer. Inventing a 07:00 general call because it
    is the usual answer is exactly the kind of confident wrongness the budget
    prompt already forbids.
    """
    days = schedule.get("days") or []
    day = next((d for d in days if d.get("day") == day_number), None)
    if not day:
        raise ValueError(f"day {day_number} not in schedule (has {len(days)} days)")

    project = project or {}
    return {
        "project_title": project.get("name") or schedule.get("title") or "Untitled",
        "shoot_day": f"Day {day['day']} of {schedule.get('total_days')}",
        "date": day.get("date"),
        "unit": day.get("unit", "Main Unit"),
        "locations": [{"name": loc, "address": "", "notes": ""} for loc in day.get("locations", [])],
        "scenes": [
            {
                "scene": sc["number"],
                "description": sc.get("heading") or "",
                "int_ext": sc.get("region"),
                "day_night": sc.get("time"),
                "location": sc.get("location"),
                "pages": round(sc.get("eighths", 0) / 8, 2),
                "cast": [],
            }
            for sc in day.get("scenes", [])
        ],
        "cast": [{"character": c, "artist": "", "call_time": "", "on_set": ""} for c in day.get("cast", [])],
        "crew": crew or [],
        "schedule_summary": {
            "pages": day.get("pages"),
            "company_moves": day.get("company_moves"),
            "night_work": day.get("night_work"),
        },
        "notes": [],
        "source": "schedule",
        "needs": ["general_call_time", "weather", "nearest_hospital", "cast_call_times"],
    }
