"""
test_callsheet.py — a call sheet built from facts, asserted.

Run: python3 evals/test_callsheet.py

The rules being protected are the ones a commercial producer depends on: a
sheet with no script is complete, a sheet with no nearest hospital is not, one
WhatsApp message at a time never loses the last message's answer, and a time
means one minute however it was typed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import callsheet  # noqa: E402


FULL = {
    "project_title": "Nike — Dawn Training",
    "client": "Nike",
    "date": "2026-11-04",
    "shoot_day": "Day 1 of 1",
    "call_time": "6.30am",
    "wrap": "7pm",
    "location": "Bandra Rooftop, 14 Hill Road, Mumbai",
    "nearest_hospital": "Lilavati Hospital, Bandra West",
    "crew": [
        {"name": "Ravi Kulkarni", "role": "1st AD", "phone": "+919820000001", "call_time": "6am"},
        "Meera Shah, Producer, +919820000002",
    ],
    "cast": [{"artist": "Aisha Khan", "character": "Runner", "call_time": "7.15am"}],
}


def test_a_commercial_with_no_script_is_complete():
    sheet = callsheet.from_facts(**FULL)
    assert sheet["scenes"] == [], "a commercial has no scenes, and must not be asked for any"
    assert sheet["ready_to_send"] is True, sheet["needs"]
    assert [n for n in sheet["needs"] if n["blocking"]] == []


def test_a_time_means_one_minute_however_it_was_typed():
    for written, stored in (("6.30am", "06:30"), ("6:30 AM", "06:30"), ("0630", "06:30"),
                            ("18:00", "18:00"), ("7pm", "19:00"), ("12am", "00:00"),
                            ("12pm", "12:00"), ("9", "09:00")):
        assert callsheet.clock(written) == stored, written


def test_something_that_is_not_a_time_is_refused_rather_than_guessed():
    for junk in ("tomorrow", "", None, "first light", "25:00", "6:75"):
        assert callsheet.clock(junk) == "", junk


def test_a_missing_hospital_blocks_the_send():
    sheet = callsheet.from_facts(**{k: v for k, v in FULL.items() if k != "nearest_hospital"})
    assert sheet["ready_to_send"] is False
    assert any(n["field"] == "nearest_hospital" and n["blocking"] for n in sheet["needs"])


def test_a_location_with_no_address_is_not_a_location():
    sheet = callsheet.from_facts(**{**FULL, "location": "Bandra Rooftop"})
    assert any(n["field"] == "locations" for n in sheet["needs"]), sheet["needs"]


def test_crew_with_no_way_to_reach_them_blocks_the_send():
    sheet = callsheet.from_facts(**{**FULL, "crew": [{"name": "Ravi", "role": "1st AD"}]})
    assert any(n["field"] == "crew" and n["blocking"] for n in sheet["needs"])


def test_one_message_at_a_time_never_loses_the_last_answer():
    sheet = callsheet.from_facts(project_title="Nike", date="2026-11-04")
    sheet = callsheet.merge(sheet, {"call_time": "6.30am"})
    sheet = callsheet.merge(sheet, {"location": "Bandra Rooftop, 14 Hill Road"})
    sheet = callsheet.merge(sheet, {"hospital": "Lilavati Hospital"})
    sheet = callsheet.merge(sheet, {"crew": ["Ravi Kulkarni, 1st AD, +919820000001"]})
    assert sheet["project_title"] == "Nike"
    assert sheet["date"] == "2026-11-04"
    assert sheet["general_call_time"] == "06:30"
    assert sheet["locations"][0]["address"] == "14 Hill Road"
    assert sheet["nearest_hospital"] == "Lilavati Hospital"
    assert sheet["ready_to_send"] is True, sheet["needs"]


def test_a_message_that_names_nothing_changes_nothing():
    first = callsheet.from_facts(**FULL)
    second = callsheet.merge(first, {"weather": "   "})
    assert second["general_call_time"] == first["general_call_time"]
    assert second["crew"] == first["crew"]
    assert second["weather"] == first["weather"]


def test_a_supplied_list_corrects_and_add_appends():
    sheet = callsheet.from_facts(**FULL)
    assert len(sheet["crew"]) == 2
    corrected = callsheet.merge(sheet, {"crew": ["Only Ravi, 1st AD, +919820000001"]})
    assert len(corrected["crew"]) == 1, "naming the crew replaces it — it is a correction"
    extended = callsheet.merge(corrected, {"add_crew": ["Sam D'Souza, Gaffer, sam@example.com"]})
    assert len(extended["crew"]) == 2
    assert extended["crew"][1]["email"] == "sam@example.com"


def test_a_crew_line_typed_as_one_string_is_parsed():
    sheet = callsheet.from_facts(crew=["Meera Shah, Producer, +919820000002, 6am"])
    row = sheet["crew"][0]
    assert row["name"] == "Meera Shah" and row["role"] == "Producer"
    assert row["phone"] == "+919820000002" and row["call_time"] == "06:00"


def test_cast_with_no_call_time_is_advisory_and_not_blocking():
    sheet = callsheet.from_facts(**{**FULL, "cast": [{"artist": "Aisha Khan"}]})
    cast_need = next(n for n in sheet["needs"] if n["field"] == "cast_call_times")
    assert cast_need["blocking"] is False
    assert sheet["ready_to_send"] is True


def test_the_summary_is_one_screen_and_counts_who_can_be_reached():
    s = callsheet.summary(callsheet.from_facts(**FULL))
    assert s["general_call_time"] == "06:30" and s["wrap_time"] == "19:00"
    assert s["location"] == "Bandra Rooftop"
    assert s["crew_count"] == 2 and s["contactable"] == 2
    assert s["ready_to_send"] is True and s["needs"] == []


def test_an_empty_sheet_asks_for_everything_it_must_have():
    sheet = callsheet.from_facts()
    blocking = {n["field"] for n in sheet["needs"] if n["blocking"]}
    assert blocking == set(callsheet.REQUIRED), blocking
    assert sheet["ready_to_send"] is False
    assert callsheet.summary(sheet)["project_title"] == "Untitled"


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
    print(f"\n callsheet: {len(tests) - failed} passed · {failed} failed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run() else 0)
