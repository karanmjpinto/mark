"""
test_screenplay.py — parser output → breakdown, asserted.

Run: python3 evals/test_screenplay.py

`schedule.py` is built directly on the `scenes[]` list this module produces, so
its shape is load-bearing: a scene that loses its cast list silently produces a
Day-out-of-Days with nobody in it. These tests use synthetic parser output, which
is why the docstring on `screenplay.iter_scenes` about the real (non-README)
parser shape matters — this fixture mirrors it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import screenplay  # noqa: E402


def action(text: str) -> dict:
    return {"type": "ACTION", "content": text}


def dialogue(name: str, line: str) -> dict:
    return {"type": "CHARACTER", "content": {"character": name, "dialogue": [line]}}


def scene_block(region, location, time, snippets, number=None) -> dict:
    info = {"region": region, "location": location, "time": [time]}
    if number is not None:
        info["scene_number"] = number
    return {"scene_info": info, "scene": snippets}


PAGES = [
    {"page": 0, "type": "FIRST_PAGES", "content": [{"scene_info": None, "scene": [action("TITLE PAGE")]}]},
    {"page": 1, "content": [
        scene_block("INT.", "OFFICE", "DAY", [action("A" * 1500), dialogue("RAVI", "Hello.")], number=1),
        scene_block("EXT.", "PARKING LOT", "NIGHT",
                    [action("B" * 750), dialogue("MEERA", "Wait."), dialogue("RAVI", "No.")], number=2),
    ]},
    {"page": 2, "content": [
        # A continuation block with no scene_info — its text belongs to scene 2.
        {"scene_info": None, "scene": [action("C" * 750)]},
        scene_block("INT./EXT.", "CAR", "CONTINUOUS", [dialogue("SAM", "Drive.")], number=3),
    ]},
]


def summary():
    s, _ = screenplay.process_pages(PAGES)
    return s


def test_first_pages_are_skipped():
    s = summary()
    assert s["total_scenes"] == 3, s["total_scenes"]


def test_aggregate_counts():
    s = summary()
    assert s["int_count"] == 2 and s["ext_count"] == 2  # INT./EXT. counts as both
    # Every scene lands in exactly one bucket — CONTINUOUS inherits the previous
    # scene's time rather than falling out of the counts entirely.
    assert s["day_count"] + s["night_count"] == s["total_scenes"]
    assert s["day_count"] == 1 and s["night_count"] == 2


def test_continuous_inherits_the_previous_scene_time():
    sc = {s["location"]: s for s in summary()["scenes"]}
    assert sc["PARKING LOT"]["time"] == "NIGHT"
    # Scene 3 is CONTINUOUS, following the night exterior.
    assert sc["CAR"]["time_slug"] == "CONTINUOUS"
    assert sc["CAR"]["time"] == "NIGHT"


def test_a_script_opening_on_continuous_falls_back_to_day():
    pages = [{"page": 1, "content": [scene_block("INT.", "CAR", "CONTINUOUS", [action("x" * 100)])]}]
    s, _ = screenplay.process_pages(pages)
    assert s["scenes"][0]["time"] == "DAY" and s["day_count"] == 1


def test_scenes_carry_the_fields_the_scheduler_needs():
    sc = summary()["scenes"]
    assert len(sc) == 3
    for s in sc:
        assert set(("number", "heading", "region", "location", "time", "characters", "eighths")) <= set(s)
    assert [s["location"] for s in sc] == ["OFFICE", "PARKING LOT", "CAR"]
    assert [s["number"] for s in sc] == [1, 2, 3]


def test_cast_is_attributed_to_the_right_scene():
    sc = {s["location"]: s for s in summary()["scenes"]}
    assert sc["OFFICE"]["characters"] == ["RAVI"]
    assert sorted(sc["PARKING LOT"]["characters"]) == ["MEERA", "RAVI"]
    assert sc["CAR"]["characters"] == ["SAM"]


def test_continuation_text_lands_on_the_open_scene():
    # Scene 2 has 750 chars of its own plus 750 from the page-2 continuation
    # block → roughly one page → 8 eighths. Scene 1 has ~1500 chars → 8 eighths.
    sc = {s["location"]: s for s in summary()["scenes"]}
    assert sc["PARKING LOT"]["eighths"] >= sc["CAR"]["eighths"]
    assert sc["OFFICE"]["eighths"] >= 7


def test_eighths_are_bounded():
    pages = [{"page": 1, "content": [scene_block("INT.", "VOID", "DAY", [action("X" * 500000)])]}]
    s, _ = screenplay.process_pages(pages)
    assert s["scenes"][0]["eighths"] == 64, "a runaway scene must not blow up the schedule"
    pages = [{"page": 1, "content": [scene_block("INT.", "VOID", "DAY", [])]}]
    s, _ = screenplay.process_pages(pages)
    assert s["scenes"][0]["eighths"] >= 1, "every scene occupies at least an eighth"


def test_scene_cap_is_reported():
    many = [{"page": 1, "content": [
        scene_block("INT.", f"ROOM {i}", "DAY", [action("x" * 100)]) for i in range(screenplay.MAX_SCENES + 20)]}]
    s, _ = screenplay.process_pages(many)
    assert len(s["scenes"]) == screenplay.MAX_SCENES
    assert s["scenes_truncated"] is True
    assert summary()["scenes_truncated"] is False


def test_extracted_text_still_returned():
    s, text = screenplay.process_pages(PAGES)
    assert "Hello." in text and "INT. OFFICE - DAY" in text
    # FIRST_PAGES (title page, contact block) is excluded by design — it is not
    # script and would otherwise be costed as if it were.
    assert "TITLE PAGE" not in text
    assert text.count("\n") > 3


def test_empty_input():
    s, text = screenplay.process_pages([])
    assert s["total_scenes"] == 0 and s["scenes"] == [] and text == ""


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ✅ {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  ❌ {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ❌ {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n screenplay: {len(tests) - failed} passed · {failed} failed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run() else 0)
