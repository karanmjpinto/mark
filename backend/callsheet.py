"""
callsheet.py — a call sheet built from facts, with no script.

`schedule.callsheet_from_day()` seeds a call sheet from a parsed screenplay.
That is the feature film path, and it is the wrong one for most of the work:
a commercial rarely has a script at all. The producer has a location, a date, a
call time and a crew list, and nothing else — and that is already enough to
send a call sheet to twenty people.

So this module builds the same shape from plain facts, and answers the only
question that matters next: **what is still missing before this can go out.**
`needs` is computed from the content rather than hard-coded, because a
commercial with no scenes is complete without them, while a call sheet with no
nearest hospital is not — that is a safety item on every real sheet.

`merge()` exists because the facts arrive one WhatsApp message at a time
("call is 6.30", then "hospital is St Mary's Paddington"). Each message updates
the fields it names and leaves the rest alone, so nothing has to be retyped and
no agent has to rewrite the whole object to change one time.

Pure logic, no FastAPI: covered offline in `evals/test_callsheet.py`.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

# What a call sheet must carry before it is safe to send to crew. Scenes are
# deliberately absent: a commercial has none, and demanding them is what made
# the script path feel mandatory.
REQUIRED = ("date", "locations", "general_call_time", "crew", "nearest_hospital")

_FIELD_LABELS = {
    "date": "the shoot date",
    "locations": "the location, with an address",
    "general_call_time": "the general call time",
    "crew": "the crew, with a phone number or an email each",
    "nearest_hospital": "the nearest hospital",
    "cast_call_times": "a call time for each cast member",
    "wrap_time": "the estimated wrap",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── times ─────────────────────────────────────────────────────────────────────
# A producer writes "6.30am", "0630", "6:30 AM" and "18:00" for two distinct
# times. One parser, one stored form (24-hour), so a call sheet cannot carry
# two spellings of the same minute.

_TIME = re.compile(r"^\s*(\d{1,2})\s*[:.\s]?\s*(\d{2})?\s*([ap]\.?m\.?)?\s*$", re.I)


def clock(value: Any) -> str:
    """"6.30am" → "06:30". Returns "" when it is not a time, never a guess."""
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    match = _TIME.match(text)
    if not match:
        return ""
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    suffix = (match.group(3) or "").lower().replace(".", "")
    if suffix == "pm" and hour < 12:
        hour += 12
    elif suffix == "am" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return ""
    return f"{hour:02d}:{minute:02d}"


# ── people ────────────────────────────────────────────────────────────────────

def _crew_member(entry: Any) -> Optional[dict]:
    """A crew line from whatever the producer sent: a dict, or "Name, Role, phone"."""
    if isinstance(entry, dict):
        name = str(entry.get("name") or entry.get("full_name") or "").strip()
        row = {
            "name": name,
            "role": str(entry.get("role") or entry.get("department") or "").strip(),
            "phone": str(entry.get("phone") or "").strip(),
            "email": str(entry.get("email") or "").strip(),
            "call_time": clock(entry.get("call_time")),
        }
    else:
        parts = [p.strip() for p in str(entry or "").split(",")]
        if not parts or not parts[0]:
            return None
        row = {"name": parts[0], "role": parts[1] if len(parts) > 1 else "",
               "phone": "", "email": "", "call_time": ""}
        for p in parts[2:]:
            if "@" in p:
                row["email"] = p
            elif clock(p):
                row["call_time"] = clock(p)
            elif any(ch.isdigit() for ch in p):
                row["phone"] = p
    return row if row["name"] else None


def _cast_member(entry: Any) -> Optional[dict]:
    if isinstance(entry, dict):
        row = {
            "character": str(entry.get("character") or entry.get("role") or "").strip(),
            "artist": str(entry.get("artist") or entry.get("name") or "").strip(),
            "call_time": clock(entry.get("call_time")),
            "on_set": clock(entry.get("on_set")),
            "phone": str(entry.get("phone") or "").strip(),
        }
    else:
        parts = [p.strip() for p in str(entry or "").split(",")]
        if not parts or not parts[0]:
            return None
        row = {"character": "", "artist": parts[0], "call_time": "", "on_set": "", "phone": ""}
        for p in parts[1:]:
            if clock(p):
                row["call_time"] = clock(p)
            elif any(ch.isdigit() for ch in p):
                row["phone"] = p
            elif not row["character"]:
                row["character"] = p
    return row if (row["artist"] or row["character"]) else None


def _location(entry: Any) -> Optional[dict]:
    if isinstance(entry, dict):
        row = {"name": str(entry.get("name") or "").strip(),
               "address": str(entry.get("address") or "").strip(),
               "notes": str(entry.get("notes") or "").strip()}
    else:
        text = str(entry or "").strip()
        if not text:
            return None
        name, _, address = text.partition(",")
        row = {"name": name.strip(), "address": address.strip(), "notes": ""}
    return row if (row["name"] or row["address"]) else None


# ── the sheet ─────────────────────────────────────────────────────────────────

def from_facts(**facts: Any) -> dict:
    """The facts a producer has on a commercial → a call sheet, plus `needs`."""
    sheet = {
        "project_title": str(facts.get("project_title") or facts.get("title") or "").strip(),
        "client": str(facts.get("client") or "").strip(),
        "shoot_day": str(facts.get("shoot_day") or "").strip(),
        "date": str(facts.get("date") or "").strip(),
        "unit": str(facts.get("unit") or "Main Unit").strip(),
        "general_call_time": clock(facts.get("general_call_time") or facts.get("call_time")),
        "wrap_time": clock(facts.get("wrap_time") or facts.get("wrap")),
        "locations": [],
        "scenes": [],
        "cast": [],
        "crew": [],
        "nearest_hospital": str(facts.get("nearest_hospital") or facts.get("hospital") or "").strip(),
        "weather": str(facts.get("weather") or "").strip(),
        "parking": str(facts.get("parking") or "").strip(),
        "notes": [n for n in (facts.get("notes") or []) if str(n).strip()],
        "source": "facts",
        "created_at": _now(),
    }
    return merge(sheet, facts)


def merge(sheet: dict, facts: Any) -> dict:
    """Apply the fields this message names; leave every other field alone.

    A list that is supplied replaces that list, because "the crew is X and Y"
    is a correction and not an addition. `add_crew` / `add_cast` append instead,
    for "put the gaffer on it too".
    """
    sheet = dict(sheet or {})
    facts = dict(facts or {})
    sheet.setdefault("source", "facts")

    for key, alias in (("project_title", "title"), ("client", None), ("shoot_day", None),
                       ("date", None), ("unit", None), ("nearest_hospital", "hospital"),
                       ("weather", None), ("parking", None)):
        for name in (key, alias):
            if name and name in facts and str(facts[name] or "").strip():
                sheet[key] = str(facts[name]).strip()

    for key, alias in (("general_call_time", "call_time"), ("wrap_time", "wrap")):
        for name in (key, alias):
            if name and name in facts and clock(facts[name]):
                sheet[key] = clock(facts[name])

    if "locations" in facts:
        sheet["locations"] = [l for l in (_location(x) for x in (facts["locations"] or [])) if l]
    if "location" in facts and str(facts["location"] or "").strip():
        one = _location(facts["location"])
        sheet["locations"] = [one] if one else sheet.get("locations", [])
    if "crew" in facts:
        sheet["crew"] = [c for c in (_crew_member(x) for x in (facts["crew"] or [])) if c]
    if "cast" in facts:
        sheet["cast"] = [c for c in (_cast_member(x) for x in (facts["cast"] or [])) if c]
    for key, fn in (("add_crew", _crew_member), ("add_cast", _cast_member)):
        target = "crew" if key == "add_crew" else "cast"
        for item in (facts.get(key) or []):
            row = fn(item)
            if row:
                sheet.setdefault(target, []).append(row)
    if "notes" in facts:
        sheet["notes"] = [str(n).strip() for n in (facts["notes"] or []) if str(n).strip()]
    for note in (facts.get("add_notes") or []):
        if str(note).strip():
            sheet.setdefault("notes", []).append(str(note).strip())

    sheet.setdefault("scenes", [])
    sheet["needs"] = needs_of(sheet)
    sheet["ready_to_send"] = not [n for n in sheet["needs"] if n["field"] in REQUIRED]
    sheet["updated_at"] = _now()
    return sheet


def needs_of(sheet: dict) -> list[dict]:
    """What is still missing, named in the words to ask the producer for it.

    Required fields block a send. Cast call times and the wrap are listed as
    advisory, because a sheet can go out without them and often does.
    """
    out: list[dict] = []
    for field in REQUIRED:
        value = sheet.get(field)
        missing = not value
        if field == "locations" and value:
            missing = not any((l.get("address") or "").strip() for l in value)
        if field == "crew" and value:
            missing = not any((c.get("phone") or c.get("email")) for c in value)
        if missing:
            out.append({"field": field, "ask": _FIELD_LABELS[field], "blocking": True})
    if sheet.get("cast") and not all(c.get("call_time") for c in sheet["cast"]):
        out.append({"field": "cast_call_times", "ask": _FIELD_LABELS["cast_call_times"],
                    "blocking": False})
    if not sheet.get("wrap_time"):
        out.append({"field": "wrap_time", "ask": _FIELD_LABELS["wrap_time"], "blocking": False})
    return out


def summary(sheet: dict) -> dict:
    """The one-screen view: who, when, where, and what is outstanding."""
    needs = sheet.get("needs") or needs_of(sheet)
    return {
        "project_title": sheet.get("project_title") or "Untitled",
        "date": sheet.get("date") or "",
        "shoot_day": sheet.get("shoot_day") or "",
        "general_call_time": sheet.get("general_call_time") or "",
        "wrap_time": sheet.get("wrap_time") or "",
        "location": (sheet.get("locations") or [{}])[0].get("name") or "",
        "crew_count": len(sheet.get("crew") or []),
        "cast_count": len(sheet.get("cast") or []),
        "contactable": sum(1 for c in (sheet.get("crew") or []) if c.get("phone") or c.get("email")),
        "needs": [n["ask"] for n in needs if n["blocking"]],
        "advisory": [n["ask"] for n in needs if not n["blocking"]],
        "ready_to_send": not [n for n in needs if n["blocking"]],
    }
