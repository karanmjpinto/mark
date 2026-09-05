"""
test_delivery.py — call-sheet delivery state, asserted.

Run: python3 evals/test_delivery.py

The rules being protected are the ones that decide whether a coordinator can
trust the board at 11pm: a state never goes backwards, a late webhook cannot
un-confirm someone, a confirmation link works for exactly one person, and the
board never claims read state it did not actually receive.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import delivery  # noqa: E402


CREW = [
    {"id": "c1", "name": "Ravi Kulkarni", "role": "1st AD", "phone": "+919820000001",
     "email": "ravi@example.com", "call_time": "06:30"},
    {"id": "c2", "name": "Meera Shah", "role": "Producer", "phone": "+919820000002"},
    {"name": "Sam D'Souza", "role": "Gaffer", "email": "sam@example.com"},  # no id
]


def reset():
    delivery._mem.clear()
    delivery._redis = None


def board():
    return delivery.open_board("cs1", CREW, channels=["whatsapp", "email"],
                               shoot_day="Day 2 of 4", date="2026-11-04")


def test_board_opens_with_everyone_queued():
    b = board()
    assert b["total"] == 3
    assert b["counts"]["queued"] == 3
    assert b["confirmed"] == 0 and b["confirmed_pct"] == 0.0
    assert all(r["token"] for r in b["recipients"])


def test_a_recipient_without_an_id_still_gets_a_stable_one():
    first = board()
    reset()
    second = board()
    ids_1 = sorted(r["id"] for r in first["recipients"])
    ids_2 = sorted(r["id"] for r in second["recipients"])
    assert ids_1 == ids_2, "the same crew list must produce the same recipient ids"


def test_resending_does_not_duplicate_or_reset_anyone():
    board()
    delivery.record_send("cs1", [{"channel": "whatsapp", "ok": True, "to": "+919820000001",
                                  "message_id": "m1"}])
    again = delivery.open_board("cs1", CREW, channels=["whatsapp"])
    assert again["total"] == 3, "re-sending must not create a second row per person"
    ravi = next(r for r in again["recipients"] if r["id"] == "c1")
    assert ravi["state"] == "sent", "an existing recipient keeps their state"
    assert ravi["resent_at"]


def test_send_results_land_on_the_right_people():
    board()
    b = delivery.record_send("cs1", [
        {"channel": "whatsapp", "ok": True, "to": "+919820000001", "message_id": "m1"},
        {"channel": "whatsapp", "ok": False, "to": "+919820000002", "error": "not on WhatsApp"},
        {"channel": "email", "ok": True, "to": "sam@example.com", "message_id": "m3"},
    ])
    states = {r["id"]: r["state"] for r in b["recipients"]}
    assert states["c1"] == "sent"
    assert states["c2"] == delivery.FAILED
    assert b["counts"][delivery.FAILED] == 1


def test_a_failed_send_sorts_to_the_top_of_outstanding():
    board()
    b = delivery.record_send("cs1", [
        {"channel": "whatsapp", "ok": True, "to": "+919820000001", "message_id": "m1"},
        {"channel": "whatsapp", "ok": False, "to": "+919820000002", "error": "no whatsapp"},
    ])
    assert b["outstanding"][0]["state"] == delivery.FAILED, \
        "the person who never got it is the one to ring first"


def test_provider_events_advance_state():
    board()
    delivery.record_send("cs1", [{"channel": "whatsapp", "ok": True,
                                  "to": "+919820000001", "message_id": "m1"}])
    out = delivery.apply_provider_event({"event": "message_delivered", "message_id": "m1"})
    assert out["state"] == "delivered" and out["changed"] is True
    out = delivery.apply_provider_event({"event": "message_read", "message_id": "m1"})
    assert out["state"] == "read"


def test_an_unknown_event_or_message_is_ignored_not_guessed():
    board()
    delivery.record_send("cs1", [{"channel": "whatsapp", "ok": True,
                                  "to": "+919820000001", "message_id": "m1"}])
    assert delivery.apply_provider_event({"event": "something_else", "message_id": "m1"}) is None
    assert delivery.apply_provider_event({"event": "message_read", "message_id": "unknown"}) is None
    assert delivery.apply_provider_event({}) is None


def test_state_never_moves_backwards():
    board()
    delivery.record_send("cs1", [{"channel": "whatsapp", "ok": True,
                                  "to": "+919820000001", "message_id": "m1"}])
    delivery.apply_provider_event({"event": "message_read", "message_id": "m1"})
    out = delivery.apply_provider_event({"event": "message_delivered", "message_id": "m1"})
    assert out["state"] == "read" and out["changed"] is False


def test_a_late_webhook_cannot_unconfirm_someone():
    b = board()
    delivery.record_send("cs1", [{"channel": "whatsapp", "ok": True,
                                  "to": "+919820000001", "message_id": "m1"}])
    ravi = next(r for r in b["recipients"] if r["id"] == "c1")
    delivery.confirm("cs1", "c1", ravi["token"])
    out = delivery.apply_provider_event({"event": "message_delivered", "message_id": "m1"})
    assert out["state"] == "confirmed", "a delivery receipt must not undo a human's reply"


def test_confirmation_needs_the_right_token():
    b = board()
    ravi = next(r for r in b["recipients"] if r["id"] == "c1")
    meera = next(r for r in b["recipients"] if r["id"] == "c2")
    try:
        delivery.confirm("cs1", "c2", ravi["token"])
        raise AssertionError("one crew member's link must not confirm another")
    except PermissionError:
        pass
    out = delivery.confirm("cs1", "c2", meera["token"])
    assert out["state"] == "confirmed"


def test_confirmation_works_from_a_failed_send():
    """The link travels: forwarded by a colleague, or read on a phone the
    provider never reported. A failed send must not block a human confirming."""
    board()
    delivery.record_send("cs1", [{"channel": "whatsapp", "ok": False,
                                  "to": "+919820000002", "error": "no whatsapp"}])
    b = delivery.get_board("cs1")
    meera = next(r for r in b["recipients"] if r["id"] == "c2")
    assert meera["state"] == delivery.FAILED
    out = delivery.confirm("cs1", "c2", meera["token"])
    assert out["state"] == "confirmed"


def test_declining_is_a_distinct_answer():
    b = board()
    sam = next(r for r in b["recipients"] if r["name"].startswith("Sam"))
    out = delivery.confirm("cs1", sam["id"], sam["token"], declined=True,
                           note="Double-booked, sending a replacement")
    assert out["state"] == delivery.DECLINED
    board_now = delivery.get_board("cs1")
    assert board_now["counts"][delivery.DECLINED] == 1
    assert board_now["confirmed"] == 0
    assert not any(o["id"] == sam["id"] for o in board_now["outstanding"]), \
        "a decline is an answer — it leaves the chase list"


def test_history_records_everything_including_the_moves_it_refused():
    board()
    delivery.record_send("cs1", [{"channel": "whatsapp", "ok": True,
                                  "to": "+919820000001", "message_id": "m1"}])
    delivery.apply_provider_event({"event": "message_read", "message_id": "m1"})
    delivery.apply_provider_event({"event": "message_delivered", "message_id": "m1"})
    ravi = next(r for r in delivery.get_board("cs1")["recipients"] if r["id"] == "c1")
    assert [h["state"] for h in ravi["history"]] == ["queued", "sent", "read", "delivered"]
    assert ravi["state"] == "read"


def test_board_does_not_claim_read_state_it_never_received():
    board()
    b = delivery.get_board("cs1")
    assert b["read_state_verified"] is False
    assert "not yet been verified" in b["note"]
    delivery.record_send("cs1", [{"channel": "whatsapp", "ok": True,
                                  "to": "+919820000001", "message_id": "m1"}])
    delivery.apply_provider_event({"event": "message_delivered", "message_id": "m1"})
    b = delivery.get_board("cs1")
    assert b["read_state_verified"] is True
    assert "read receipts" in b["note"]


def test_confirm_url_is_short_and_addressed_to_one_person():
    b = board()
    ravi = next(r for r in b["recipients"] if r["id"] == "c1")
    url = delivery.confirm_url("https://mark.example.com/", "cs1", ravi)
    assert url.startswith("https://mark.example.com/c/cs1/c1/")
    assert url.count("/") == 6 and len(url) < 90


def test_confirmed_pct_reconciles():
    b = board()
    for rid in ("c1", "c2"):
        rec = next(r for r in b["recipients"] if r["id"] == rid)
        delivery.confirm("cs1", rid, rec["token"])
    final = delivery.get_board("cs1")
    assert final["confirmed"] == 2 and final["total"] == 3
    assert final["confirmed_pct"] == round(2 / 3, 4)
    assert len(final["outstanding"]) == 1


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
    print(f"\n delivery: {len(tests) - failed} passed · {failed} failed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run() else 0)
