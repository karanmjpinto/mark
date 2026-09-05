"""
test_schedule.py — the scheduler's craft rules, asserted.

Run: python3 evals/test_schedule.py   (or via pytest)

These are not "does it return a dict" tests. Each one pins a rule a producer
would notice being broken: location blocks stay together, day and night don't
get mixed carelessly, a stated day count is honoured, and hold days surface.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import schedule as sched  # noqa: E402


def scene(n, loc, region="INT", time="DAY", eighths=8, characters=None):
    return {"number": n, "heading": f"{region}. {loc} - {time}", "region": region,
            "location": loc, "time": time, "eighths": eighths,
            "characters": characters or []}


SCENES = [
    scene(1, "PARKING LOT", "EXT", "DAY", 16, ["RAVI", "MEERA"]),
    scene(2, "OFFICE", "INT", "DAY", 24, ["RAVI"]),
    scene(3, "OFFICE", "INT", "NIGHT", 16, ["RAVI", "SAM"]),
    scene(4, "PARKING LOT", "EXT", "NIGHT", 8, ["MEERA"]),
    scene(5, "ROOFTOP", "EXT", "DAY", 32, ["MEERA", "SAM"]),
    scene(6, "OFFICE", "INT", "DAY", 8, ["SAM"]),
]


def test_locations_stay_contiguous():
    out = sched.build_schedule(SCENES, eighths_per_day=40)
    for day in out["days"]:
        # A day may visit more than one location, but the same location must not
        # be picked up again after moving away within that day.
        seen, order = set(), []
        for s in day["scenes"]:
            if not order or order[-1] != s["location"]:
                order.append(s["location"])
        assert len(order) == len(set(order)), f"day {day['day']} returns to a location: {order}"
        seen.update(order)


def test_day_work_precedes_night_within_a_location():
    out = sched.build_schedule(SCENES, eighths_per_day=200)  # everything in one day
    day = out["days"][0]
    per_loc: dict[str, list[str]] = {}
    for s in day["scenes"]:
        per_loc.setdefault(s["location"], []).append(s["time"])
    for loc, times in per_loc.items():
        first_night = next((i for i, t in enumerate(times) if t == "NIGHT"), len(times))
        assert "DAY" not in times[first_night:], f"{loc} schedules day work after night work: {times}"


def test_night_work_lands_at_the_end_of_a_day_across_locations():
    # OFFICE night + ROOFTOP day in the same shooting day must not put the
    # exterior day work after the night interior.
    scenes = [
        scene(1, "OFFICE", "INT", "NIGHT", 16, ["RAVI"]),
        scene(2, "ROOFTOP", "EXT", "DAY", 16, ["SAM"]),
    ]
    out = sched.build_schedule(scenes, eighths_per_day=40)
    times = [s["time"] for s in out["days"][0]["scenes"]]
    assert times == ["DAY", "NIGHT"], times


def test_stated_shoot_days_is_binding():
    out = sched.build_schedule(SCENES, shoot_days=2)
    assert out["total_days"] == 2, out["total_days"]
    assert sum(len(d["scenes"]) for d in out["days"]) == len(SCENES)


def test_every_scene_is_scheduled_exactly_once():
    out = sched.build_schedule(SCENES, eighths_per_day=24)
    numbers = [n for d in out["days"] for n in d["scene_numbers"]]
    assert sorted(numbers) == [s["number"] for s in SCENES], numbers


def test_eighths_and_pages_reconcile():
    out = sched.build_schedule(SCENES, eighths_per_day=40)
    assert out["total_eighths"] == sum(s["eighths"] for s in SCENES)
    assert sum(d["eighths"] for d in out["days"]) == out["total_eighths"]
    assert out["total_pages"] == round(out["total_eighths"] / 8, 2)


def test_dood_marks_hold_days():
    # SAM works day 1 and day 3 but not day 2 → one hold day.
    scenes = [
        scene(1, "A", eighths=40, characters=["SAM"]),
        scene(2, "B", eighths=40, characters=["RAVI"]),
        scene(3, "C", eighths=40, characters=["SAM"]),
    ]
    out = sched.build_schedule(scenes, eighths_per_day=40)
    assert out["total_days"] == 3
    dood = out["dood"]
    sam = next(c for c in dood["characters"] if c["character"] == "SAM")
    assert sam["work_days"] == 2 and sam["hold_days"] == 1, sam
    assert dood["matrix"]["SAM"][1] == "SW"
    assert dood["matrix"]["SAM"][2] == "H"
    assert dood["matrix"]["SAM"][3] == "WF"
    ravi = next(c for c in dood["characters"] if c["character"] == "RAVI")
    assert dood["matrix"]["RAVI"][2] == "SWF", "single-day role must read SWF"
    assert ravi["hold_days"] == 0


def test_company_moves_counted():
    out = sched.build_schedule(SCENES, eighths_per_day=24)
    assert out["company_moves"] >= 1
    assert all(d["company_moves"] >= 0 for d in out["days"])


def test_night_days_flagged():
    out = sched.build_schedule(SCENES, eighths_per_day=40)
    assert out["night_days"] >= 1
    assert any(d["night_work"] for d in out["days"])


def test_synthetic_scenes_warn():
    summary = {
        "total_scenes": 6, "int_count": 4, "ext_count": 2,
        "day_count": 5, "night_count": 1,
        "unique_locations": [{"name": "OFFICE", "scene_count": 4},
                             {"name": "STREET", "scene_count": 2}],
        "characters": [],
    }
    scenes = sched.scenes_from_summary(summary)
    assert len(scenes) == 6 and all(s["synthetic"] for s in scenes)
    out = sched.build_schedule(scenes)
    assert out["synthetic"] is True
    assert any("synthes" in w.lower() for w in out["warnings"]), out["warnings"]


def test_unreasonable_day_count_warns():
    scenes = [scene(i, "A", eighths=40) for i in range(1, 21)]  # 100 pages
    out = sched.build_schedule(scenes, shoot_days=2)
    assert any("pages a day" in w for w in out["warnings"]), out["warnings"]


def test_reconcile_with_budget_flags_mismatch():
    out = sched.build_schedule(SCENES, eighths_per_day=24)
    rec = sched.reconcile_with_budget(out, {"shoot_days": 1})
    assert rec["matches"] is False
    assert rec["delta_days"] == out["total_days"] - 1
    assert rec["notes"], "a mismatch must produce a note a producer can read"


def test_callsheet_seed_is_partial_not_invented():
    out = sched.build_schedule(SCENES, eighths_per_day=40, start_date="2026-11-03")
    cs = sched.callsheet_seed(out, 1, project={"name": "TEST FILM"})
    assert cs["project_title"] == "TEST FILM"
    assert cs["date"] == "2026-11-03"
    assert cs["scenes"], "seed must carry the day's scenes"
    # The things it must NOT invent:
    assert all(c["call_time"] == "" for c in cs["cast"])
    assert "general_call_time" in cs["needs"] and "weather" in cs["needs"]


def test_rebuild_honours_the_producers_assignment():
    # Two scenes at the same location, deliberately split across two days — the
    # packer would never do this, and a hand edit must survive anyway.
    days = [[scene(1, "OFFICE", eighths=8)], [scene(2, "OFFICE", eighths=8)]]
    out = sched.rebuild(days, start_date="2026-11-03")
    assert out["total_days"] == 2
    assert [d["scene_numbers"] for d in out["days"]] == [[1], [2]]
    assert out["hand_edited"] is True
    assert out["days"][1]["date"] == "2026-11-04"


def test_rebuild_recomputes_every_derived_figure():
    days = [
        [scene(1, "OFFICE", eighths=16, characters=["SAM"]),
         scene(2, "ROOFTOP", eighths=8, characters=["RAVI"])],
        [scene(3, "OFFICE", "INT", "NIGHT", 8, characters=["SAM"])],
    ]
    out = sched.rebuild(days)
    assert out["total_eighths"] == 32 and out["total_pages"] == 4.0
    assert out["days"][0]["company_moves"] == 1     # two locations in one day
    assert out["night_days"] == 1
    sam = next(c for c in out["dood"]["characters"] if c["character"] == "SAM")
    assert sam["work_days"] == 2 and sam["hold_days"] == 0


def test_rebuild_still_orders_night_work_last_within_a_day():
    days = [[scene(1, "OFFICE", "INT", "NIGHT", 8), scene(2, "ROOFTOP", "EXT", "DAY", 8)]]
    out = sched.rebuild(days)
    assert [s["time"] for s in out["days"][0]["scenes"]] == ["DAY", "NIGHT"]


def test_a_schedule_survives_a_round_trip_through_rebuild():
    """Drag → save → re-render must be lossless. The day record is exactly what
    the browser posts back, so anything it drops is destroyed on the first edit."""
    out = sched.build_schedule(SCENES, eighths_per_day=40)
    again = sched.rebuild([d["scenes"] for d in out["days"]])
    assert again["total_scenes"] == out["total_scenes"]
    assert again["total_eighths"] == out["total_eighths"]
    assert again["dood"]["characters"] == out["dood"]["characters"]
    assert again["dood"]["total_hold_days"] == out["dood"]["total_hold_days"]
    assert [d["cast"] for d in again["days"]] == [d["cast"] for d in out["days"]]


def test_rebuild_warns_on_an_unshootable_day():
    days = [[scene(i, "OFFICE", eighths=40) for i in range(1, 4)]]  # 15 pages in a day
    out = sched.rebuild(days)
    assert any("1.5×" in w for w in out["warnings"]), out["warnings"]


def test_rebuild_warns_on_an_empty_day():
    out = sched.rebuild([[scene(1, "OFFICE")], []])
    assert any("no scenes" in w for w in out["warnings"])


def test_empty_input_is_handled():
    out = sched.build_schedule([])
    assert out["total_days"] == 0 and out["warnings"]


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
    print(f"\n schedule: {len(tests) - failed} passed · {failed} failed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run() else 0)
