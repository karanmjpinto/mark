"""
test_chatops.py — the chat channel, asserted.

Run: python3 evals/test_chatops.py

The rules being protected are the ones that decide whether a WhatsApp agent is
safe to point at a tenant's budgets: an unenrolled number is nobody, a role is
a limit and not a label, a share link works for exactly one document and stops
working, a crew member's own description cannot inject markup into a page we
serve, and no renderer ever returns more than a person will read on a phone.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import chatops  # noqa: E402


def reset():
    chatops._redis = None
    chatops._mem_roster.clear()
    chatops._mem_shares.clear()
    os.environ.pop("DEFAULT_COUNTRY_CODE", None)


BUDGET = {
    "title": "Nike — Dawn Training TVC",
    "production_type": "TVC",
    "shoot_days": 3,
    "scale_tier": "mid",
    "locations": ["Bandra rooftop", "Marine Drive"],
    "sections": [
        {"code": "10300", "name": "DIRECTOR", "type": "above_the_line",
         "items": [{"code": "10301", "desc": "Director fee", "sub": "3 days + prep",
                    "amount": 2000000, "conf": "green"}]},
        {"code": "20100", "name": "CAMERA", "type": "production",
         "items": [{"code": "20101", "desc": "Camera package", "amount": 3600000, "conf": "amber"},
                   {"code": "20102", "desc": "DIT", "amount": 180000, "conf": "green"}]},
        {"code": "50100", "name": "EDITORIAL", "type": "post",
         "items": [{"code": "50101", "desc": "Offline edit", "amount": 900000, "conf": "green"}]},
    ],
    "excluded": ["Music licensing", "Talent buyout beyond 1 year"],
    "flags": ["Street permit is the largest single risk — confirm before locking."],
}

LEDGER = {
    "production": "Nike — Dawn Training TVC",
    "currency": "INR",
    "threshold": 0.1,
    "lines": [
        {"section": "20100", "section_name": "CAMERA", "desc": "Camera package",
         "budget": 3600000, "actual": 4220000, "delta": 620000, "delta_pct": 0.172,
         "material": True, "classification": "vendor_variance"},
        {"section": "10300", "section_name": "DIRECTOR", "desc": "Director fee",
         "budget": 2000000, "actual": 2000000, "delta": 0, "delta_pct": 0.0,
         "material": False, "classification": "on_budget"},
    ],
    "totals": {"budget": 6680000, "actual": 7300000, "delta": 620000, "delta_pct": 0.0928,
               "unbudgeted_spend": 410000, "unspent_budget": 0},
    "unmatched_actuals": 3,
}

BOARD = {
    "shoot_day": "Day 2 of 4", "date": "2026-11-04", "total": 4, "confirmed": 1,
    "confirmed_pct": 0.25, "counts": {"confirmed": 1, "declined": 1, "failed": 1, "read": 1},
    "outstanding": [
        {"id": "r1", "name": "Sam D'Souza", "role": "Gaffer", "state": "failed",
         "phone": "+919820000003", "email": ""},
        {"id": "r2", "name": "Meera Shah", "role": "Producer", "state": "read",
         "phone": "", "email": "meera@example.com"},
    ],
    "read_state_verified": False,
}


# ── who is texting ────────────────────────────────────────────────────────────

def test_every_channels_spelling_of_a_number_resolves_to_one_form():
    for raw in ("+919820012345", "whatsapp:+91 98200 12345", "919820012345@c.us",
                "+91-98200-12345", "0091 9820012345"):
        assert chatops.normalise_phone(raw) == "+919820012345", raw


def test_a_local_number_needs_a_configured_country_code():
    assert chatops.normalise_phone("98200 12345") is None, "no country code, no guess"
    os.environ["DEFAULT_COUNTRY_CODE"] = "91"
    assert chatops.normalise_phone("98200 12345") == "+919820012345"
    assert chatops.normalise_phone("098200 12345") == "+919820012345"


def test_rubbish_is_not_a_phone_number():
    for raw in ("", None, "ask mark", "+1", "+1234567890123456789"):
        assert chatops.normalise_phone(raw) is None, raw


def test_an_unenrolled_number_is_nobody():
    assert chatops.operator("+919820012345") is None
    assert chatops.session("+919820012345") is None
    assert chatops.can(None, "read") is False


def test_re_enrolling_corrects_the_record_rather_than_adding_a_second_one():
    chatops.enrol("+919820012345", name="Karan", role="coordinator")
    chatops.enrol("whatsapp:+919820012345", name="Karan Pinto", role="producer")
    ops = chatops.operators()
    assert len(ops) == 1, ops
    assert ops[0]["name"] == "Karan Pinto" and ops[0]["role"] == "producer"
    assert ops[0]["enrolled_at"], "the original enrolment date survives a correction"


def test_a_role_is_a_limit_not_a_label():
    viewer = chatops.enrol("+919820000001", name="Client", role="viewer")
    coord = chatops.enrol("+919820000002", name="Coordinator", role="coordinator")
    producer = chatops.enrol("+919820000003", name="Producer", role="producer")
    assert chatops.can(viewer, "read") and not chatops.can(viewer, "write")
    assert chatops.can(coord, "write") and not chatops.can(coord, "send")
    assert chatops.can(producer, "send")


def test_an_unknown_role_is_refused():
    try:
        chatops.enrol("+919820000004", role="admin")
    except ValueError:
        return
    raise AssertionError("an unknown role must not be storable")


def test_revoking_a_number_removes_it():
    chatops.enrol("+919820000005", name="Left the company")
    assert chatops.revoke("+91 98200 00005") is True
    assert chatops.operator("+919820000005") is None
    assert chatops.revoke("+919820000005") is False


def test_the_thread_remembers_one_production():
    chatops.enrol("+919820000006", name="Karan", role="producer")
    chatops.set_active_project("+919820000006", "proj-7")
    assert chatops.session("+919820000006")["active_project"] == "proj-7"
    chatops.set_active_project("+919820000006", None)
    assert chatops.session("+919820000006")["active_project"] is None


def test_a_session_counts_messages_so_usage_is_visible():
    chatops.enrol("+919820000007", name="Karan")
    chatops.session("+919820000007")
    sess = chatops.session("+919820000007")
    assert sess["messages"] == 2 and sess["last_seen"] is not sess.get("enrolled_at")


def test_the_roster_is_capped():
    chatops.MAX_OPERATORS, original = 2, chatops.MAX_OPERATORS
    try:
        chatops.enrol("+919820000011")
        chatops.enrol("+919820000012")
        try:
            chatops.enrol("+919820000013")
        except ValueError:
            pass
        else:
            raise AssertionError("the roster must not grow past its cap")
        chatops.enrol("+919820000012", name="still correctable")
    finally:
        chatops.MAX_OPERATORS = original


def test_a_masked_number_is_safe_to_log():
    masked = chatops.mask_phone("+919820012345")
    assert "9820012" not in masked and masked.endswith("45")


# ── share links ───────────────────────────────────────────────────────────────

def test_a_share_link_opens_exactly_one_document():
    share = chatops.create_share(kind="budget", title="Nike", html="<p>budget</p>")
    assert chatops.get_share(share["share_id"], share["token"])["html"] == "<p>budget</p>"


def test_a_guessed_id_or_a_forged_token_is_refused():
    share = chatops.create_share(kind="budget", title="Nike", html="<p>budget</p>")
    assert chatops.get_share(share["share_id"], "abcdef.0000000000000000") is None
    assert chatops.get_share(share["share_id"], "") is None
    assert chatops.get_share("deadbeefdeadbeef", share["token"]) is None


def test_a_token_is_not_transferable_between_documents():
    a = chatops.create_share(kind="budget", title="A", html="<p>a</p>")
    b = chatops.create_share(kind="budget", title="B", html="<p>b</p>")
    assert chatops.get_share(b["share_id"], a["token"]) is None


def test_a_share_link_expires():
    share = chatops.create_share(kind="budget", title="Nike", html="<p>budget</p>", ttl=0)
    assert chatops.get_share(share["share_id"], share["token"]) is None


def test_the_url_is_built_from_the_public_base():
    share = chatops.create_share(kind="budget", title="Nike", html="<p>x</p>")
    url = chatops.share_url("https://askmark.filmsbykp.com/", share)
    assert url == f"https://askmark.filmsbykp.com/s/{share['share_id']}/{share['token']}"


# ── money, the way a producer says it ─────────────────────────────────────────

def test_indian_budgets_read_in_crore_and_lakh():
    assert chatops.fmt_money(18600000) == "₹1.86 Cr"
    assert chatops.fmt_money(420000) == "₹4.2 L"
    assert chatops.fmt_money(9500) == "₹9,500"
    assert chatops.fmt_money(-620000) == "-₹6.2 L"


def test_other_currencies_are_not_dressed_as_rupees():
    assert chatops.fmt_money(1860000, code="USD") == "USD 1.86M"
    assert chatops.fmt_money(1860000, code="USD", symbol="$") == "$1.86M"


def test_a_missing_number_says_so_rather_than_claiming_zero():
    for v in (None, "", "lots"):
        assert chatops.fmt_money(v) == "—", v


# ── renderers ─────────────────────────────────────────────────────────────────

def test_a_budget_fits_in_a_message_and_carries_the_total():
    text = chatops.budget_text(BUDGET, url="https://x/s/a/b")
    assert len(text) <= chatops.MAX_CHARS
    assert "₹66.8 L" in text, text
    assert "CAMERA" in text and "https://x/s/a/b" in text


def test_a_budget_says_what_is_not_verified_and_what_is_missing():
    text = chatops.budget_text(BUDGET)
    assert "1 line on unverified rates" in text, text
    assert "Not included: 2 items" in text
    assert "Street permit" in text


def test_a_large_budget_is_still_one_screen():
    big = {"title": "Feature", "sections": [
        {"code": str(i), "name": f"SECTION {i}",
         "items": [{"code": f"{i}-{j}", "desc": "Line " * 12, "amount": 100000}
                   for j in range(20)]} for i in range(60)]}
    text = chatops.budget_text(big)
    assert len(text) <= chatops.MAX_CHARS, len(text)
    assert "+54 more sections" in text


def test_a_variance_ledger_says_which_way_it_went():
    text = chatops.variance_text(LEDGER)
    assert "over by ₹6.2 L" in text, text
    assert "+9.3%" in text
    assert "vendor variance" in text, "the classification is the point"
    assert "3 actual rows matched nothing" in text


def test_an_under_budget_production_is_not_reported_as_over():
    under = {**LEDGER, "totals": {**LEDGER["totals"], "delta": -400000, "delta_pct": -0.06}}
    assert "under by ₹4 L" in chatops.variance_text(under)


def test_the_delivery_board_leads_with_who_to_ring():
    text = chatops.delivery_text(BOARD)
    assert text.index("Sam D'Souza") < text.index("Meera Shah"), "worst first"
    assert "send failed" in text and "+919820000003" in text
    assert "1 of 4 confirmed (25%)" in text
    assert "1 person can't make it" in text
    assert "unverified" in text, "read state must not be presented as fact"


def test_a_fully_confirmed_board_says_so_instead_of_an_empty_list():
    text = chatops.delivery_text({**BOARD, "outstanding": [], "confirmed": 4,
                                  "confirmed_pct": 1.0, "counts": {"confirmed": 4}})
    assert "Everyone has confirmed." in text


def test_a_schedule_names_the_days_and_admits_when_it_is_synthetic():
    sched = {"title": "Nike", "total_days": 2, "total_scenes": 9, "total_pages": 11.5,
             "company_moves": 1, "night_days": 1, "synthetic": True, "warnings": ["no script"],
             "days": [{"day": 1, "date": "2026-11-03", "locations": ["Bandra rooftop", "Lane"],
                       "scene_numbers": [1, 2, 3], "pages": 6.0},
                      {"day": 2, "date": "2026-11-04", "locations": ["Marine Drive"],
                       "scene_numbers": [4, 5], "pages": 5.5}]}
    text = chatops.schedule_text(sched)
    assert "2 shoot days" in text and "D1 2026-11-03" in text
    assert "Bandra rooftop +1" in text
    assert "indicative" in text, "a synthetic schedule must not pass as a parsed one"


def test_an_empty_object_does_not_crash_a_renderer():
    for fn in (chatops.budget_text, chatops.schedule_text, chatops.variance_text,
               chatops.delivery_text):
        assert isinstance(fn({}), str)
    assert isinstance(chatops.teardown_text({}), str)


def test_clipping_happens_on_a_line_boundary_and_admits_itself():
    text = chatops.clip("\n".join(f"line {i} " + "x" * 40 for i in range(200)))
    assert text.endswith("… (trimmed)")
    assert len(text) <= chatops.MAX_CHARS
    assert "\nline" in text and not text.splitlines()[-2].endswith("x" * 5 + " ")


# ── the shared document ───────────────────────────────────────────────────────

def test_a_crew_members_own_words_cannot_inject_markup():
    hostile = {"title": "<script>alert(1)</script>", "sections": [
        {"code": "1", "name": "CAMERA", "items": [
            {"code": "1a", "desc": "<img src=x onerror=alert(1)>", "amount": 100,
             "sub": "</td></tr><script>bad()</script>"}]}],
        "excluded": ["<b>no</b>"], "flags": ["<i>no</i>"]}
    html = chatops.budget_html(hostile)
    assert "<script>" not in html, "a description is data, not markup"
    assert "onerror" not in html or "&lt;img" in html
    assert "&lt;script&gt;" in html


def test_the_document_totals_what_the_message_totalled():
    html = chatops.budget_html(BUDGET)
    assert "₹66.8 L" in html
    assert "DIRECTOR" in html and "Offline edit" in html
    assert "unverified rate" in html, "the amber legend has to travel with the table"


def test_an_expired_link_explains_itself_without_leaking_anything():
    page = chatops.expired_page("This link isn't valid any more.")
    assert "production office" in page and "<script" not in page


def test_a_call_sheet_shows_its_state_and_asks_for_what_is_missing():
    sheet = {"project_title": "Nike — Dawn Training", "shoot_day": "Day 1 of 1",
             "date": "2026-11-04", "general_call_time": "06:30",
             "locations": [{"name": "Bandra Rooftop", "address": "14 Hill Road"}],
             "crew": [{"name": "Ravi", "phone": "+9198"}, {"name": "Meera"}],
             "cast": [{"artist": "Aisha"}],
             "needs": [{"field": "nearest_hospital", "ask": "the nearest hospital",
                        "blocking": True},
                       {"field": "wrap_time", "ask": "the estimated wrap", "blocking": False}]}
    text = chatops.callsheet_text(sheet)
    assert "Nike — Dawn Training · Day 1 of 1" in text
    assert "call 06:30" in text and "Bandra Rooftop, 14 Hill Road" in text
    assert "2 crew members (1 reachable)" in text, text
    assert "Still needed:" in text and "the nearest hospital" in text
    assert "Nice to have: the estimated wrap" in text
    assert "Ready to send" not in text


def test_a_complete_call_sheet_says_how_many_it_reaches():
    sheet = {"project_title": "Nike", "general_call_time": "06:30",
             "nearest_hospital": "Lilavati",
             "locations": [{"name": "Rooftop", "address": "14 Hill Road"}],
             "crew": [{"name": "Ravi", "phone": "+9198"}, {"name": "Meera", "email": "m@x.com"}],
             "needs": []}
    text = chatops.callsheet_text(sheet)
    assert "Ready to send to 2 people." in text, text
    assert "Still needed" not in text


def test_a_share_can_carry_a_file_and_hands_back_its_bytes():
    blob = b"PK\x03\x04 pretend xlsx"
    xlsx = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    share = chatops.create_share(kind="budget", title="Nike budget", blob=blob,
                                 content_type=xlsx)
    assert share["content_type"] == xlsx
    assert share["filename"] == "Nike budget.xlsx"
    record = chatops.get_share(share["share_id"], share["token"])
    assert chatops.share_bytes(record) == blob
    assert chatops.share_page(record) == "", "a file share has no page"


def test_a_share_carries_exactly_one_of_html_or_a_file():
    for kwargs in ({}, {"html": "<p>x</p>", "blob": b"x",
                        "content_type": "text/csv"}):
        try:
            chatops.create_share(kind="budget", title="T", **kwargs)
        except ValueError:
            continue
        raise AssertionError(f"accepted an ambiguous share: {kwargs}")


def test_a_file_type_we_do_not_write_is_refused():
    for bad in ("text/html", "image/svg+xml", "application/x-msdownload", ""):
        try:
            chatops.create_share(kind="x", title="T", blob=b"x", content_type=bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad!r} as a shareable file")


def test_an_html_share_has_no_bytes():
    share = chatops.create_share(kind="budget", title="T", html="<p>x</p>")
    record = chatops.get_share(share["share_id"], share["token"])
    assert chatops.share_bytes(record) is None
    assert chatops.share_page(record) == "<p>x</p>"


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
    print(f"\n chatops: {len(tests) - failed} passed · {failed} failed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run() else 0)
