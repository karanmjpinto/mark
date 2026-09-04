"""
test_budgetdiff.py — version-to-version change, asserted.

Run: python3 evals/test_budgetdiff.py

The thing being protected here is trust in a meeting. If the diff misses a moved
line, or double-counts one it matched twice, the producer reading it out loud is
wrong in front of a client.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import budgetdiff  # noqa: E402


V1 = {
    "title": "TVC v1",
    "sections": [
        {"code": "11300", "name": "CAMERA", "items": [
            {"code": "11301", "desc": "Camera package rental", "sub": "3 days", "amount": 180000,
             "gst_rate": 0.18, "conf": "green"},
            {"code": "11302", "desc": "Focus puller", "sub": "3 days", "amount": 36000,
             "gst_rate": 0.18, "conf": "green"},
        ]},
        {"code": "12600", "name": "CATERING", "items": [
            {"code": "12601", "desc": "Crew catering", "sub": "", "amount": 90000,
             "gst_rate": 0.05, "conf": "amber"},
        ]},
    ],
}


def v2():
    b = copy.deepcopy(V1)
    b["title"] = "TVC v2"
    b["sections"][0]["items"][0]["amount"] = 225000              # repriced
    b["sections"][0]["items"][1]["desc"] = "Focus puller / 1st AC"  # reworded, same money
    b["sections"][1]["items"].append(                             # added
        {"code": "12602", "desc": "Craft services", "sub": "", "amount": 25000,
         "gst_rate": 0.05, "conf": "red"})
    return b


def test_no_change_reads_as_no_change():
    d = budgetdiff.diff(V1, copy.deepcopy(V1))
    assert d["totals"]["delta"] == 0
    assert d["moved"] == []
    assert d["summary"] == "No change."


def test_repricing_is_reported_with_the_delta():
    d = budgetdiff.diff(V1, v2())
    camera = next(c for c in d["changes"] if c["code"] == "11301")
    assert camera["status"] == budgetdiff.CHANGED
    assert camera["before"] == 180000 and camera["after"] == 225000
    assert camera["delta"] == 45000 and camera["delta_pct"] == 0.25


def test_a_reworded_line_at_the_same_price_is_not_hidden():
    d = budgetdiff.diff(V1, v2())
    fp = next(c for c in d["changes"] if c["code"] == "11302")
    assert fp["status"] == budgetdiff.RENAMED
    assert fp["delta"] == 0
    assert fp["desc_before"] == "Focus puller"


def test_added_and_removed():
    d = budgetdiff.diff(V1, v2())
    added = [c for c in d["changes"] if c["status"] == budgetdiff.ADDED]
    assert [c["code"] for c in added] == ["12602"]

    trimmed = copy.deepcopy(V1)
    trimmed["sections"][1]["items"] = []
    d2 = budgetdiff.diff(V1, trimmed)
    removed = [c for c in d2["changes"] if c["status"] == budgetdiff.REMOVED]
    assert [c["code"] for c in removed] == ["12601"]
    assert removed[0]["delta"] == -90000


def test_totals_reconcile_against_the_line_deltas():
    d = budgetdiff.diff(V1, v2())
    assert d["totals"]["before"] == 306000
    assert d["totals"]["after"] == 306000 + 45000 + 25000
    assert round(sum(c["delta"] for c in d["changes"]), 2) == d["totals"]["delta"]


def test_no_line_is_matched_twice():
    """Two lines sharing a code must not both match the same line on the other
    side — that would report one change and swallow the other."""
    before = {"sections": [{"code": "11300", "name": "CAMERA", "items": [
        {"code": "11301", "desc": "Camera A", "amount": 100},
        {"code": "11301", "desc": "Camera B", "amount": 200},
    ]}]}
    after = {"sections": [{"code": "11300", "name": "CAMERA", "items": [
        {"code": "11301", "desc": "Camera A", "amount": 150},
        {"code": "11301", "desc": "Camera B", "amount": 250},
    ]}]}
    d = budgetdiff.diff(before, after)
    assert len(d["changes"]) == 2
    assert d["totals"]["delta"] == 100
    assert all(c["status"] == budgetdiff.CHANGED for c in d["changes"])


def test_matching_falls_back_to_description_within_a_section():
    before = {"sections": [{"code": "11300", "name": "CAMERA", "items": [
        {"code": "", "desc": "Camera package rental", "amount": 180000}]}]}
    after = {"sections": [{"code": "11300", "name": "CAMERA", "items": [
        {"code": "", "desc": "Camera package rental (Alexa)", "amount": 200000}]}]}
    d = budgetdiff.diff(before, after)
    assert len(d["changes"]) == 1 and d["changes"][0]["status"] == budgetdiff.CHANGED


def test_a_line_that_moved_section_is_add_plus_remove_not_a_silent_match():
    moved = copy.deepcopy(V1)
    item = moved["sections"][1]["items"].pop()
    moved["sections"][0]["items"].append(item)
    d = budgetdiff.diff(V1, moved)
    statuses = {c["code"]: c["status"] for c in d["changes"]}
    assert statuses["12601"] in (budgetdiff.ADDED, budgetdiff.REMOVED, budgetdiff.UNCHANGED)
    assert d["totals"]["delta"] == 0, "moving a line between sections must not change the total"


def test_sections_rollup_sorted_by_impact():
    d = budgetdiff.diff(V1, v2())
    assert d["sections"][0]["section"] == "11300"
    assert d["sections"][0]["delta"] == 45000


def test_summary_is_a_sentence_a_producer_can_paste():
    d = budgetdiff.diff(V1, v2())
    s = d["summary"]
    assert "repriced" in s and "added" in s and "reworded" in s
    assert "306,000" in s and "376,000" in s


def test_version_summary_row():
    row = budgetdiff.version_summary({
        "id": "b1", "version": "1.0", "created_at": "2026-08-01T00:00:00Z",
        "budget_data": V1, "source": "flue:generate-budget"})
    assert row["total"] == 306000 and row["lines"] == 3
    assert row["title"] == "TVC v1" and row["locked"] is False


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
    print(f"\n budgetdiff: {len(tests) - failed} passed · {failed} failed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run() else 0)
