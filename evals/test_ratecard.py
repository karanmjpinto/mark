"""
test_ratecard.py — the rate library, asserted.

Run: python3 evals/test_ratecard.py

Runs against the in-memory store (no Redis), which is the same code path the
backend uses when REDIS_HOST is unset. The rules being pinned: a seed is never
verified, a named city is never substituted with another city's rate, and
corrections blend rather than overwrite.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import ratecard  # noqa: E402


def reset():
    ratecard._mem.clear()
    ratecard._redis = None


def make(**kw):
    row = {"region": "india", "city": "mumbai", "tier": "mid", "item_key": "dop",
           "desc": "Director of photography", "unit": "day", "rate": 75000,
           "currency": "INR", "gst_rate": 0.18}
    row.update(kw)
    return row


def test_norm_key():
    assert ratecard.norm_key("Camera Package / Alexa 35") == "camera_package_alexa_35"
    assert ratecard.norm_key("  DOP  ") == "dop"
    assert ratecard.norm_key("light--boy") == "light_boy"


def test_validation_rejects_percent_gst():
    ok, reason = ratecard.validate(make(gst_rate=18))
    assert not ok and "decimal" in reason


def test_validation_rejects_bad_unit_and_negative_rate():
    assert not ratecard.validate(make(unit="fortnight"))[0]
    assert not ratecard.validate(make(rate=-1))[0]
    assert not ratecard.validate(make(desc=""))[0]


def test_upsert_corrects_rather_than_duplicates():
    reset()
    a = ratecard.upsert(make())
    b = ratecard.upsert(make(rate=82000))
    assert a["id"] == b["id"], "same identity tuple must update in place"
    assert len(ratecard.all_rates()) == 1
    assert ratecard.all_rates()[0]["rate"] == 82000
    assert b["created_at"] == a["created_at"]


def test_a_different_tier_is_a_different_rate():
    reset()
    ratecard.upsert(make(tier="mid"))
    ratecard.upsert(make(tier="high", rate=150000))
    assert len(ratecard.all_rates()) == 2


def test_seeds_are_never_verified():
    reset()
    result = ratecard.seed("india")
    assert result["written"] > 0
    rows = ratecard.all_rates()
    assert all(not ratecard.is_verified(r) for r in rows), "a seeded rate must never read as verified"
    assert all(r["confidence"] == "amber" for r in rows)


def test_seeding_twice_does_not_stamp_on_corrections():
    reset()
    ratecard.seed("india")
    corrected = ratecard.upsert(make(item_key="dop", rate=99000, confidence="green",
                                     verified_at="2026-08-01", source="teardown"))
    again = ratecard.seed("india")
    assert again["written"] == 0 and again["skipped"] > 0
    row = ratecard.find_one(region="india", city="mumbai", tier="mid", item_key="dop")
    assert row["rate"] == 99000 and row["id"] == corrected["id"]


def test_pack_never_substitutes_a_named_city():
    reset()
    ratecard.upsert(make(city="mumbai", rate=75000))
    ratecard.upsert(make(city="chennai", rate=40000))
    pack = ratecard.resolve_pack("india", city="chennai", tier="mid")
    assert [p["rate"] for p in pack] == [40000]
    hyd = ratecard.resolve_pack("india", city="hyderabad", tier="mid")
    assert hyd == [], "no Hyderabad rate exists — nothing may be substituted for it"


def test_pack_prefers_exact_city_then_regionwide():
    reset()
    ratecard.upsert(make(city="", rate=60000))          # region-wide
    ratecard.upsert(make(city="mumbai", rate=75000))    # city-specific
    pack = ratecard.resolve_pack("india", city="mumbai", tier="mid")
    assert len(pack) == 1 and pack[0]["rate"] == 75000


def test_pack_prefers_exact_tier_then_any():
    reset()
    ratecard.upsert(make(tier="any", rate=60000))
    ratecard.upsert(make(tier="mid", rate=75000))
    pack = ratecard.resolve_pack("india", city="mumbai", tier="mid")
    assert len(pack) == 1 and pack[0]["rate"] == 75000


def test_verified_rates_sort_first():
    reset()
    ratecard.upsert(make(item_key="dop"))
    ratecard.upsert(make(item_key="gaffer", rate=12000, verified_at="2026-08-01",
                         confidence="green", source="teardown: three jobs"))
    pack = ratecard.resolve_pack("india", city="mumbai", tier="mid")
    assert pack[0]["item_key"] == "gaffer" and pack[0]["verified"] is True


def test_coverage_reports_verified_share():
    reset()
    ratecard.upsert(make(item_key="dop"))
    ratecard.upsert(make(item_key="gaffer", rate=12000, verified_at="2026-08-01"))
    cov = ratecard.coverage("india", city="mumbai", tier="mid")
    assert cov["rates"] == 2 and cov["verified"] == 1 and cov["verified_pct"] == 0.5


def test_proposals_come_from_actuals_and_are_not_auto_written():
    reset()
    ledger = {
        "currency": "INR", "production": "TEST TVC",
        "lines": [
            {"item_key": "camera.package.alexa35", "section": "11300", "desc": "Camera package",
             "actual": 225000, "quantity": 3, "delta_pct": 0.25, "status": "over", "gst_rate": 0.18},
            {"item_key": "", "desc": "Police bandobast", "actual": 85000, "status": "unbudgeted"},
        ],
    }
    proposals = ratecard.propose_from_variance(ledger, region="india", city="mumbai")
    assert len(proposals) == 1, "unbudgeted and keyless lines cannot propose a rate"
    assert proposals[0]["rate"] == 75000  # 225000 over 3 days
    assert proposals[0]["verified_at"], "an actual-derived rate is verified"
    assert ratecard.all_rates() == [], "proposing must not write"


def test_proposal_divides_by_the_actual_quantity_not_the_budgeted_one():
    # The scope-change trap: budgeted 2 days, shot 3. Dividing 465,000 by the
    # budgeted 2 would propose a 232,500 day rate for a location that actually
    # cost 155,000 a day.
    ledger = {
        "currency": "INR", "production": "TEST",
        "lines": [{"item_key": "location.bungalow", "section": "12400", "desc": "Bungalow",
                   "actual": 465000, "quantity": 2, "actual_quantity": 3,
                   "delta_pct": 0.55, "status": "over", "gst_rate": 0.18}],
    }
    p = ratecard.propose_from_variance(ledger, region="india", city="mumbai")[0]
    assert p["rate"] == 155000, p["rate"]
    assert p["quantity_basis"] == "actual" and p["needs_review"] is False


def test_proposal_without_actual_quantity_is_flagged_for_review():
    ledger = {
        "currency": "INR", "production": "TEST",
        "lines": [{"item_key": "location.bungalow", "section": "12400", "desc": "Bungalow",
                   "actual": 465000, "quantity": 2, "actual_quantity": None,
                   "delta_pct": 0.55, "status": "over", "gst_rate": 0.18}],
    }
    p = ratecard.propose_from_variance(ledger, region="india", city="mumbai")[0]
    assert p["quantity_basis"] == "budget" and p["needs_review"] is True


def test_review_metadata_never_reaches_the_stored_row():
    reset()
    written = ratecard.apply_proposals([make(item_key="dop", confidence="green",
                                             verified_at="2026-08-01", sample_size=1,
                                             delta_pct=0.2, quantity_basis="actual",
                                             needs_review=True)])
    assert "needs_review" not in written[0] and "delta_pct" not in written[0]


def test_applying_a_second_observation_blends_and_grows_the_sample():
    reset()
    ratecard.apply_proposals([make(item_key="gaffer", rate=10000, confidence="green",
                                   verified_at="2026-08-01", sample_size=1, source="job A")])
    ratecard.apply_proposals([make(item_key="gaffer", rate=14000, confidence="green",
                                   verified_at="2026-08-20", sample_size=1, source="job B")])
    row = ratecard.find_one(region="india", city="mumbai", tier="mid", item_key="gaffer")
    assert row["rate"] == 12000, row["rate"]
    assert row["sample_size"] == 2
    assert "job A" in row["source"] and "job B" in row["source"]


def test_delete():
    reset()
    row = ratecard.upsert(make())
    assert ratecard.delete_rate(row["id"]) is True
    assert ratecard.all_rates() == []
    assert ratecard.delete_rate(row["id"]) is False


def test_all_shipped_seeds_load_and_validate():
    reset()
    for region in ratecard.available_seeds():
        result = ratecard.seed(region)
        assert result["written"] > 0, region
    for row in ratecard.all_rates():
        ok, reason = ratecard.validate(row)
        assert ok, f"{row['item_key']}: {reason}"


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
    print(f"\n ratecard: {len(tests) - failed} passed · {failed} failed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run() else 0)
