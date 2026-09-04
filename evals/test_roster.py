"""
test_roster.py — crew and vendor history, asserted.

Run: python3 evals/test_roster.py

The rules that matter here are about identity and restraint. Merging two people
corrupts a rate history invisibly, and proposing a rate off one job is how a
guess becomes a "verified" number in the rate card.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import roster  # noqa: E402
import variance  # noqa: E402


def reset():
    roster._mem.clear()
    roster._redis = None


def gaffer():
    return roster.upsert({"name": "Ravi Kulkarni", "role": "Gaffer", "phone": "+91 98200 00001",
                          "item_key": "gaffer", "city": "mumbai"})


def test_phone_matching_survives_indian_number_formats():
    reset()
    a = gaffer()
    for variant in ("9820000001", "09820000001", "+919820000001", "91 98200 00001"):
        b = roster.upsert({"name": "Ravi K", "phone": variant})
        assert b["id"] == a["id"], f"{variant} should be the same handset"
    assert len(roster.all_entries()) == 1


def test_email_matching():
    reset()
    a = roster.upsert({"name": "Meera Shah", "email": "Meera@Example.com"})
    b = roster.upsert({"name": "M. Shah", "email": "meera@example.com "})
    assert a["id"] == b["id"] and len(roster.all_entries()) == 1


def test_two_different_people_with_similar_names_are_not_merged():
    reset()
    a = roster.upsert({"name": "Ravi Kulkarni", "phone": "9820000001"})
    b = roster.upsert({"name": "Ravi Kumar", "phone": "9820000002"})
    assert a["id"] != b["id"]
    # And a bare name never merges into someone who has contact details.
    c = roster.upsert({"name": "Ravi Kulkarni"})
    assert c["id"] not in (a["id"],), "a nameless-only match must not claim a known person"


def test_a_shorter_name_on_a_later_job_does_not_overwrite_the_full_one():
    """Call sheets shorten people. If the latest write won, the roster would get
    worse every time it was used."""
    reset()
    roster.upsert({"name": "Ravi Kulkarni", "phone": "9820000001"})
    again = roster.upsert({"name": "Ravi K", "phone": "9820000001"})
    assert again["name"] == "Ravi Kulkarni"
    fuller = roster.upsert({"name": "Ravikumar Anand Kulkarni", "phone": "9820000001"})
    assert fuller["name"] == "Ravikumar Anand Kulkarni", "a fuller name is an improvement"


def test_item_key_is_derived_from_the_role_so_crew_can_reach_the_rate_card():
    reset()
    r = roster.upsert({"name": "Someone", "role": "Gaffer"})
    assert r["item_key"] == "gaffer" and r["item_key_source"] == "derived from role"
    given = roster.upsert({"name": "Another", "role": "Gaffer", "item_key": "gaffer.night"})
    assert given["item_key"] == "gaffer.night" and given["item_key_source"] == "given"


def test_details_fill_in_over_time_and_are_never_blanked():
    reset()
    first = roster.upsert({"name": "Sam D'Souza", "email": "sam@example.com"})
    second = roster.upsert({"name": "Sam D'Souza", "email": "sam@example.com",
                            "phone": "9820000003", "role": "Gaffer"})
    third = roster.upsert({"name": "Sam D'Souza", "email": "sam@example.com"})  # no new detail
    assert second["id"] == first["id"] == third["id"]
    assert third["phone"] == "9820000003" and third["role"] == "Gaffer"


def test_engagement_rejects_a_bad_rate():
    reset()
    g = gaffer()
    for bad in (-1, "12000", None, True):
        try:
            roster.record_engagement(g["id"], {"rate": bad})
            raise AssertionError(f"{bad!r} should not be accepted as a rate")
        except ValueError:
            pass


def test_rate_history_reports_median_not_mean():
    reset()
    g = gaffer()
    for date, rate in (("2026-01-10", 10000), ("2026-03-02", 12000), ("2026-06-18", 60000)):
        roster.record_engagement(g["id"], {"production": "job", "date": date, "rate": rate})
    h = roster.rate_history(g["id"])
    assert h["median"] == 12000, "one emergency weekend rate must not move the headline"
    assert h["last_paid"] == 60000 and h["last_paid_on"] == "2026-06-18"
    assert h["min"] == 10000 and h["max"] == 60000
    assert h["confidence"] == "solid"


def test_rate_history_flags_a_single_observation():
    reset()
    g = gaffer()
    roster.record_engagement(g["id"], {"production": "one job", "date": "2026-02-01", "rate": 12000})
    h = roster.rate_history(g["id"])
    assert h["confidence"] == "single observation"
    assert h["engagements"] == 1


def test_rate_history_shows_the_rise():
    reset()
    g = gaffer()
    roster.record_engagement(g["id"], {"date": "2026-01-01", "rate": 10000})
    roster.record_engagement(g["id"], {"date": "2026-08-01", "rate": 13000})
    h = roster.rate_history(g["id"])
    assert h["change_since_first"] == 0.3
    assert h["spread_pct"] == 0.3


def test_undated_engagements_sort_last_and_are_counted():
    reset()
    g = gaffer()
    roster.record_engagement(g["id"], {"rate": 9000})
    roster.record_engagement(g["id"], {"date": "2026-05-05", "rate": 11000})
    h = roster.rate_history(g["id"])
    assert h["undated"] == 1
    assert h["history"][-1]["rate"] == 9000, "an undated engagement sorts last"
    assert h["last_paid_on"] == "2026-05-05", "last paid comes from a dated engagement"


def test_import_crew_is_idempotent():
    reset()
    crew = [{"name": "Ravi Kulkarni", "role": "Gaffer", "phone": "9820000001", "day_rate": 12000},
            {"name": "Meera Shah", "role": "Line Producer", "email": "meera@example.com"},
            {"name": ""}]  # skipped
    first = roster.import_crew(crew, production="TVC A")
    second = roster.import_crew(crew, production="TVC B")
    assert first["added"] == 2 and first["updated"] == 0
    assert second["added"] == 0 and second["updated"] == 2
    assert len(roster.all_entries()) == 2
    ravi = next(r for r in roster.all_entries() if r["name"] == "Ravi Kulkarni")
    assert len(ravi["engagements"]) == 2, "each job is its own engagement"


def test_ingest_ledger_records_what_vendors_were_actually_paid():
    reset()
    budget = {"sections": [{"code": "11300", "name": "CAMERA", "items": [
        {"code": "11301", "desc": "Camera package rental", "sub": "3 days",
         "amount": 180000, "gst_rate": 0.18, "item_key": "camera.package.alexa35"}]}]}
    ledger = variance.build_ledger(budget, [
        {"code": "11301", "desc": "Camera package rental", "amount": 225000, "qty": 3,
         "vendor": "Kit House"}], production="TVC A")
    out = roster.ingest_ledger(ledger)
    assert out["vendors_recorded"] == 1
    kit = next(r for r in roster.all_entries() if r["name"] == "Kit House")
    assert kit["kind"] == roster.KIND_VENDOR
    assert kit["item_key"] == "camera.package.alexa35"
    # 225,000 over the 3 days actually worked, not the 3 budgeted — same rule
    # the rate proposals follow.
    assert kit["engagements"][0]["rate"] == 75000


def test_propose_rates_needs_two_engagements():
    reset()
    g = gaffer()
    roster.record_engagement(g["id"], {"date": "2026-01-01", "rate": 12000})
    assert roster.propose_rates() == [], "one job is a price, not a rate"
    roster.record_engagement(g["id"], {"date": "2026-04-01", "rate": 14000})
    proposals = roster.propose_rates(city="mumbai")
    assert len(proposals) == 1
    p = proposals[0]
    assert p["rate"] == 13000 and p["sample_size"] == 2
    assert p["item_key"] == "gaffer" and p["verified_at"] == "2026-04-01"
    assert "roster: Ravi Kulkarni" in p["source"]


def test_propose_rates_skips_an_entry_with_nothing_to_key_on():
    reset()
    r = roster.upsert({"name": "Someone Useful"})   # no role, so no key derivable
    roster.record_engagement(r["id"], {"date": "2026-01-01", "rate": 5000})
    roster.record_engagement(r["id"], {"date": "2026-02-01", "rate": 6000})
    assert roster.propose_rates() == [], "a rate with no key cannot be joined to a budget line"


def test_a_derived_key_is_proposed_but_labelled_as_derived():
    """A crew list has roles, not rate-card keys. Deriving one is what lets a
    call sheet reach the rate card at all — but the reviewer must see that
    nobody confirmed the name."""
    reset()
    r = roster.upsert({"name": "Someone Useful", "role": "Fixer"})
    for d, rate in (("2026-01-01", 5000), ("2026-02-01", 6000)):
        roster.record_engagement(r["id"], {"date": d, "rate": rate})
    p = roster.propose_rates()[0]
    assert p["item_key"] == "fixer"
    assert p["item_key_source"] == "derived from role"


def test_proposals_carry_the_spread_so_a_volatile_rate_is_visible():
    reset()
    g = gaffer()
    roster.record_engagement(g["id"], {"date": "2026-01-01", "rate": 8000})
    roster.record_engagement(g["id"], {"date": "2026-02-01", "rate": 24000})
    p = roster.propose_rates()[0]
    assert p["spread_pct"] == 2.0, "3x between jobs must be visible before anyone accepts it"


def test_search_by_name_role_and_tag():
    reset()
    roster.upsert({"name": "Ravi Kulkarni", "role": "Gaffer", "tags": ["night unit"]})
    roster.upsert({"name": "Meera Shah", "role": "Line Producer"})
    roster.upsert({"name": "Kit House", "kind": roster.KIND_VENDOR, "role": "Camera rental"})
    assert [r["name"] for r in roster.search("gaffer")] == ["Ravi Kulkarni"]
    assert [r["name"] for r in roster.search(tag="night unit")] == ["Ravi Kulkarni"]
    assert [r["name"] for r in roster.search(kind=roster.KIND_VENDOR)] == ["Kit House"]
    assert len(roster.search()) == 3


def test_search_orders_by_how_much_history_there_is():
    reset()
    a = roster.upsert({"name": "Often Used", "role": "Gaffer"})
    roster.upsert({"name": "Never Used", "role": "Gaffer"})
    for i in range(3):
        roster.record_engagement(a["id"], {"date": f"2026-0{i+1}-01", "rate": 10000})
    assert [r["name"] for r in roster.search("gaffer")] == ["Often Used", "Never Used"]


def test_delete():
    reset()
    g = gaffer()
    assert roster.delete(g["id"]) is True
    assert roster.all_entries() == []
    assert roster.delete(g["id"]) is False


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            reset()
            t()
            print(f"  ✅ {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  ❌ {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ❌ {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n roster: {len(tests) - failed} passed · {failed} failed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run() else 0)
