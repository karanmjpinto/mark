"""
screenplay.py — parser output → the breakdown Mark reasons about.

Split out of `main.py` for one reason: it is pure, and the scheduler is built
directly on its `scenes[]` output, so it needs to be testable in CI without
FastAPI or a PDF. `/script/parse` still owns the file handling and the
`screenplay-pdf-to-json` call; everything downstream of the parsed pages lives
here.

Two outputs from one traversal:

  * the compact **summary** — aggregate counts plus top-N locations and
    characters, sized to keep the budget agent's input tokens bounded;
  * the per-scene **list** — number, INT/EXT, location, time, cast and length in
    eighths, which is what `schedule.py` turns into strips.

Scene length is estimated from text volume rather than measured, because the
parser gives us page indices and not page geometry. It is a scheduling unit, not
a claim about the script's formatting.
"""

from __future__ import annotations

from typing import Optional


# Uses SMASH-CUT/screenplay-pdf-to-json to turn a PDF screenplay into structured
# scene/location/INT-EXT/character data. The agent uses this as ground truth so
# it doesn't have to re-extract structure from prose every call.

def iter_scenes(pages: list):
    """
    Yield (scene_info, snippets) for every scene block in a parsed screenplay.
    The actual parser shape is:
        [{ "page": int, "content": [{ "scene_info": {...}|None, "scene": [...] }, ...], "type"?: "FIRST_PAGES" }]
    The README documents a flatter shape — it is wrong. Verified against parser output.
    """
    for page in pages or []:
        if page.get("type") == "FIRST_PAGES":
            continue
        for block in page.get("content", []) or []:
            if not isinstance(block, dict):
                continue
            yield block.get("scene_info") or None, block.get("scene") or []

# Caps on summary lists — top-N by scene_count keeps Claude input-token cost bounded.
# Long-tail one-line characters and one-off locations don't drive budget decisions.
MAX_LOCATIONS = 25
MAX_CHARACTERS = 30
# Per-scene list feeds the scheduler (schedule.py). Capped for the same reason as
# the lists above; a 500-scene cap covers any feature and every TVC.
MAX_SCENES = 500
# Rough screenplay page ≈ 55 lines. Used only to convert a scene's text length
# into eighths — a scheduling unit, not a claim about the script's formatting.
CHARS_PER_PAGE = 1500

DAY_WORDS = ("DAY", "MORNING", "AFTERNOON", "DAWN", "SUNRISE", "MIDDAY")
NIGHT_WORDS = ("NIGHT", "EVENING", "DUSK", "SUNSET", "MIDNIGHT")


def _time_bucket(times, previous: str = "DAY") -> str:
    """Resolve a scene heading's time slug to DAY or NIGHT.

    Everything downstream — lighting package, catering meal counts, turnaround
    between shooting days — is a function of this one bit, so a scene is never
    left without it."""
    text = " ".join(str(t or "") for t in (times or [])).upper()
    if any(k in text for k in NIGHT_WORDS):
        return "NIGHT"
    if any(k in text for k in DAY_WORDS):
        return "DAY"
    return previous or "DAY"


def close_scene(scene: Optional[dict]) -> Optional[dict]:
    """Finalise a scene record: convert accumulated text length to eighths."""
    if scene is None:
        return None
    chars = scene.pop("_chars", 0)
    scene["eighths"] = max(1, min(64, int(round(chars / CHARS_PER_PAGE * 8)) or 1))
    return scene

def process_pages(pages: list) -> tuple[dict, str]:
    """
    Single pass over the parser output: builds the compact summary, the
    per-scene list the scheduler needs, AND reconstructs plain script text.
    Avoids three traversals of a large structure.
    """
    int_count = ext_count = day_count = night_count = total_scenes = 0
    locations: dict = {}
    characters: dict = {}
    text_chunks: list = []
    scenes: list = []
    current: Optional[dict] = None
    last_bucket = "DAY"

    for scene_info, snippets in iter_scenes(pages):
        if scene_info:
            total_scenes += 1
            region = (scene_info.get("region") or "").upper()
            if "INT" in region:
                int_count += 1
            if "EXT" in region:
                ext_count += 1
            # CONTINUOUS, SAME, LATER and a blank time slug all inherit the
            # previous scene's time of day — that is what they mean on the page,
            # and the scheduler has to place the scene somewhere regardless. A
            # script that opens on CONTINUOUS falls back to DAY.
            bucket = _time_bucket(scene_info.get("time"), last_bucket)
            last_bucket = bucket
            if bucket == "NIGHT":
                night_count += 1
            else:
                day_count += 1
            loc = scene_info.get("location")
            if loc:
                locations[loc] = locations.get(loc, 0) + 1
            heading = (
                f"{scene_info.get('region','')} {scene_info.get('location','') or ''}"
                f" - {' / '.join(scene_info.get('time') or [])}"
            ).strip(" -")
            if heading:
                text_chunks.append(heading)

            closed = close_scene(current)
            if closed and len(scenes) < MAX_SCENES:
                scenes.append(closed)
            current = {
                "number": scene_info.get("scene_number") or total_scenes,
                "heading": heading,
                "region": region,
                "location": loc or "UNKNOWN",
                # Both the slug as written and the resolved bucket: the slug is
                # what a producer expects to read on a strip, the bucket is what
                # the scheduler sorts on.
                "time_slug": " / ".join(scene_info.get("time") or []),
                "time": bucket,
                "characters": [],
                "_chars": len(heading),
            }
        for snippet in snippets:
            content = snippet.get("content")
            if snippet.get("type") == "CHARACTER" and isinstance(content, dict):
                name = content.get("character")
                if name:
                    characters[name] = characters.get(name, 0) + 1
                    if current is not None and name not in current["characters"]:
                        current["characters"].append(name)
            mark = len(text_chunks)
            if isinstance(content, str):
                text_chunks.append(content)
            elif isinstance(content, dict):
                for v in content.values():
                    if isinstance(v, str):
                        text_chunks.append(v)
                    elif isinstance(v, list):
                        text_chunks.extend(x for x in v if isinstance(x, str))
            elif isinstance(content, list):
                text_chunks.extend(x for x in content if isinstance(x, str))
            if current is not None and len(text_chunks) > mark:
                current["_chars"] += sum(len(c) for c in text_chunks[mark:])

    closed = close_scene(current)
    if closed and len(scenes) < MAX_SCENES:
        scenes.append(closed)

    summary = {
        "total_scenes": total_scenes,
        "int_count": int_count,
        "ext_count": ext_count,
        "day_count": day_count,
        "night_count": night_count,
        "unique_locations": [
            {"name": k, "scene_count": v}
            for k, v in sorted(locations.items(), key=lambda kv: kv[1], reverse=True)[:MAX_LOCATIONS]
        ],
        "characters": [
            {"name": k, "scene_count": v}
            for k, v in sorted(characters.items(), key=lambda kv: kv[1], reverse=True)[:MAX_CHARACTERS]
        ],
        # Per-scene detail for the scheduler. Additive — every existing consumer
        # of this summary keeps working and simply ignores it.
        "scenes": scenes,
        "scenes_truncated": total_scenes > len(scenes),
    }
    return summary, "\n".join(c for c in text_chunks if c)
