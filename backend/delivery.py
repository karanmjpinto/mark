"""
delivery.py — did the call sheet actually reach the crew, and did they read it?

Mark could already *send* a call sheet: `/callsheet/send` dispatches over email
and WhatsApp through Unipile, behind a propose/confirm approval gate. What it
could not do is answer the question a production coordinator asks at 11pm —
**who hasn't got it?** Croogloo sells exactly that state layer to studios. This
is it, built for the way an Indian unit actually works.

Three states, and they are not the same thing:

  * **delivered** — the provider says it reached the device. Useful, and outside
    our control: it depends on Unipile reporting it back.
  * **read** — the provider says it was opened. Same caveat, and on WhatsApp it
    is only available when the recipient has read receipts on. A crew member
    with receipts off is not a crew member who is ignoring you.
  * **confirmed** — a human tapped a link and said "I'll be there." This is the
    only one that means anything on a shoot day, and it is the only one that
    does not depend on a provider feature, because the link is ours.

That ordering is the design. The confirmation link works over any channel that
can carry a URL — WhatsApp, SMS, email, a message forwarded to someone who was
never on the list — and needs no login, because a spot boy at 6am is not going
to create an account.

**The provider mapping is unverified.** `apply_provider_event()` accepts the
event shape Unipile's docs describe and is deliberately tolerant, but it has
never seen a real webhook. It is marked as such in the record and in the status
board, and nothing about confirmation depends on it.

Pure functions plus a tenant-scoped store, same pattern as `ratecard.py`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timezone
from typing import Any, Optional

_redis = None
_mem: dict = {}

# Order matters: a state never moves backwards. A "delivered" webhook arriving
# after the crew member already confirmed must not undo the confirmation, and
# out-of-order webhooks are normal.
STATES = ("queued", "sent", "delivered", "read", "confirmed")
FAILED = "failed"
DECLINED = "declined"
_RANK = {s: i for i, s in enumerate(STATES)}


def init(redis_client) -> None:
    global _redis
    _redis = redis_client


def _tkey(key: str) -> str:
    try:
        import tenancy  # noqa: PLC0415 — lazy so this module stays testable without FastAPI
        return tenancy.tkey(key)
    except Exception:
        return key


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── the confirmation token ────────────────────────────────────────────────────
# A signed, opaque token. It identifies one recipient on one call sheet and
# nothing else: no session, no account, no way to read the call sheet back out of
# it. Worst case for a leaked link is that someone marks a crew member present.

def _secret() -> bytes:
    return (os.getenv("CONFIRM_SECRET") or os.getenv("CONNECTIONS_SECRET") or "mark-dev-secret").encode()


def make_token(sheet_id: str, recipient_id: str) -> str:
    body = f"{sheet_id}:{recipient_id}"
    sig = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{secrets.token_urlsafe(6)}.{sig}"


def token_matches(token: str, sheet_id: str, recipient_id: str) -> bool:
    """Constant-time check that a token belongs to this recipient on this sheet."""
    if not token or "." not in token:
        return False
    expected = hmac.new(_secret(), f"{sheet_id}:{recipient_id}".encode(), hashlib.sha256).hexdigest()[:16]
    return hmac.compare_digest(token.rsplit(".", 1)[-1], expected)


# ── storage ───────────────────────────────────────────────────────────────────

def _key(sheet_id: str) -> str:
    return _tkey(f"delivery:{sheet_id}")


def _load(sheet_id: str) -> Optional[dict]:
    k = _key(sheet_id)
    if _redis:
        raw = _redis.get(k)
        return json.loads(raw) if raw else None
    return _mem.get(k)


def _store(board: dict) -> None:
    k = _key(board["sheet_id"])
    board["updated_at"] = _now()
    if _redis:
        _redis.set(k, json.dumps(board))
    else:
        _mem[k] = board


def get_board(sheet_id: str) -> Optional[dict]:
    board = _load(sheet_id)
    return summarise(board) if board else None


# ── creating the board ────────────────────────────────────────────────────────

def _recipient_id(recipient: dict, index: int) -> str:
    """Stable per-person id. Prefers a real crew id; falls back to a hash of the
    address so re-sending the same sheet to the same person updates their row
    rather than creating a second one."""
    for key in ("id", "crew_id"):
        if recipient.get(key):
            return str(recipient[key])
    handle = (recipient.get("email") or recipient.get("phone") or "").strip().lower()
    if handle:
        return "h_" + hashlib.sha256(handle.encode()).hexdigest()[:10]
    return f"r{index}"


def open_board(sheet_id: str, recipients: list[dict], *, channels: list[str],
               shoot_day: str = "", date: str = "", project_id: str = "") -> dict:
    """Create (or top up) the delivery board for one call sheet.

    Re-sending is normal — a call sheet goes out, three things change, it goes
    out again. An existing recipient keeps their state and history; only new
    people are added.
    """
    board = _load(sheet_id) or {
        "sheet_id": sheet_id, "project_id": project_id, "shoot_day": shoot_day,
        "date": date, "channels": channels, "recipients": {}, "created_at": _now(),
    }
    board["channels"] = sorted(set(board.get("channels", [])) | set(channels))
    for i, r in enumerate(recipients or []):
        rid = _recipient_id(r, i)
        existing = board["recipients"].get(rid)
        if existing:
            existing["resent_at"] = _now()
            continue
        board["recipients"][rid] = {
            "id": rid,
            "name": r.get("name") or r.get("full_name") or "",
            "role": r.get("role") or r.get("department") or "",
            "email": r.get("email") or "",
            "phone": r.get("phone") or "",
            "state": "queued",
            "channel": None,
            "provider_message_id": None,
            "token": make_token(sheet_id, rid),
            "history": [{"state": "queued", "at": _now(), "source": "mark"}],
            "call_time": r.get("call_time") or "",
        }
    _store(board)
    return summarise(board)


def _advance(rec: dict, state: str, *, source: str, detail: str = "") -> bool:
    """Move a recipient forward. Returns True if the state actually changed.

    Backwards transitions are recorded in history but do not change `state` —
    a late "delivered" webhook must never un-confirm someone who has already
    replied, and providers deliver events out of order all the time.
    """
    entry = {"state": state, "at": _now(), "source": source}
    if detail:
        entry["detail"] = detail
    rec.setdefault("history", []).append(entry)

    if state in (FAILED, DECLINED):
        rec["state"] = state
        return True
    current = rec.get("state", "queued")
    if current in (FAILED, DECLINED) and state != "confirmed":
        return False
    if _RANK.get(state, -1) > _RANK.get(current, -1):
        rec["state"] = state
        return True
    return False


def record_send(sheet_id: str, results: list[dict]) -> dict:
    """Fold the result of an actual dispatch into the board.

    `results` is what the Unipile send path already returns — one entry per
    recipient per channel, each `{channel, ok, to?, error?, message_id?}`.
    """
    board = _load(sheet_id)
    if not board:
        raise KeyError(f"no delivery board for call sheet {sheet_id}")
    by_handle = {}
    for rid, rec in board["recipients"].items():
        for handle in (rec.get("email"), rec.get("phone")):
            if handle:
                by_handle[str(handle).strip().lower()] = rid

    for res in results or []:
        handle = str(res.get("to") or res.get("recipient") or "").strip().lower()
        rid = by_handle.get(handle)
        if not rid:
            continue
        rec = board["recipients"][rid]
        rec["channel"] = res.get("channel") or rec.get("channel")
        if res.get("message_id"):
            rec["provider_message_id"] = res["message_id"]
        if res.get("ok"):
            _advance(rec, "sent", source="unipile")
        else:
            _advance(rec, FAILED, source="unipile", detail=str(res.get("error") or "send failed"))
    _store(board)
    return summarise(board)


# ── provider events ───────────────────────────────────────────────────────────

# Unipile's event names, mapped to ours. Kept as data so correcting it after the
# first real webhook is a one-line change rather than a rewrite.
PROVIDER_EVENTS = {
    "message_delivered": "delivered",
    "message_read": "read",
    "message_seen": "read",
    "message_failed": FAILED,
    "message_bounced": FAILED,
    "mail_opened": "read",
    "mail_delivered": "delivered",
    "mail_bounced": FAILED,
}


def apply_provider_event(event: dict) -> Optional[dict]:
    """Apply one delivery/read webhook.

    **Unverified against a real Unipile payload.** The mapping is tolerant about
    where the message id and event name sit, and returns None rather than
    guessing when it cannot find a recipient — a webhook that silently marks the
    wrong person as read is worse than one that is ignored.
    """
    name = str(event.get("event") or event.get("type") or event.get("status") or "").lower()
    state = PROVIDER_EVENTS.get(name)
    if not state:
        return None
    message_id = (event.get("message_id") or event.get("id")
                  or (event.get("message") or {}).get("id"))
    if not message_id:
        return None

    for key in _all_board_keys():
        board = _raw_board(key)
        if not board:
            continue
        for rec in board["recipients"].values():
            if rec.get("provider_message_id") == message_id:
                changed = _advance(rec, state, source="provider",
                                   detail=str(event.get("error") or "")[:200])
                board["provider_events_seen"] = board.get("provider_events_seen", 0) + 1
                _store(board)
                return {"sheet_id": board["sheet_id"], "recipient": rec["id"],
                        "state": rec["state"], "changed": changed}
    return None


def _all_board_keys() -> list[str]:
    if _redis:
        return [k.decode() if isinstance(k, bytes) else k
                for k in _redis.scan_iter(_tkey("delivery:*"))]
    prefix = _tkey("delivery:")
    return [k for k in _mem if str(k).startswith(prefix)]


def _raw_board(key: str) -> Optional[dict]:
    if _redis:
        raw = _redis.get(key)
        return json.loads(raw) if raw else None
    return _mem.get(key)


# ── the crew member's tap ─────────────────────────────────────────────────────

def confirm(sheet_id: str, recipient_id: str, token: str, *,
            declined: bool = False, note: str = "") -> dict:
    """One tap from a crew member. The only state here that means anything."""
    board = _load(sheet_id)
    if not board:
        raise KeyError("call sheet not found")
    rec = board["recipients"].get(recipient_id)
    if not rec:
        raise KeyError("recipient not on this call sheet")
    if not token_matches(token, sheet_id, recipient_id):
        raise PermissionError("link is not valid for this recipient")

    _advance(rec, DECLINED if declined else "confirmed", source="crew", detail=note[:280])
    rec["confirmed_at"] = _now()
    if note:
        rec["note"] = note[:280]
    _store(board)
    return {"sheet_id": sheet_id, "recipient": rec["id"], "name": rec["name"],
            "state": rec["state"], "shoot_day": board.get("shoot_day"),
            "date": board.get("date")}


# ── the board a coordinator actually reads ────────────────────────────────────

def summarise(board: dict) -> dict:
    """The 11pm view: who has confirmed, who hasn't, who to ring.

    `outstanding` is ordered by how worried to be — a failed send first (they
    never got it), then people who have not opened it, then people who have read
    it and not replied.
    """
    recipients = list(board.get("recipients", {}).values())
    counts = {s: 0 for s in STATES}
    counts[FAILED] = 0
    counts[DECLINED] = 0
    for r in recipients:
        counts[r.get("state", "queued")] = counts.get(r.get("state", "queued"), 0) + 1

    worry = {FAILED: 0, "queued": 1, "sent": 2, "delivered": 3, "read": 4}
    outstanding = sorted(
        [r for r in recipients if r.get("state") not in ("confirmed", DECLINED)],
        key=lambda r: (worry.get(r.get("state"), 9), r.get("name") or ""))

    total = len(recipients)
    confirmed = counts.get("confirmed", 0)
    return {
        **{k: v for k, v in board.items() if k != "recipients"},
        "recipients": recipients,
        "counts": counts,
        "total": total,
        "confirmed": confirmed,
        "confirmed_pct": round(confirmed / total, 4) if total else 0.0,
        "outstanding": [
            {"id": r["id"], "name": r["name"], "role": r["role"], "state": r["state"],
             "phone": r["phone"], "email": r["email"], "call_time": r.get("call_time", "")}
            for r in outstanding],
        "read_state_verified": bool(board.get("provider_events_seen")),
        "note": ("Delivery and read state come from the messaging provider and have not yet been "
                 "verified against a real webhook. Confirmation is ours and is reliable."
                 if not board.get("provider_events_seen") else
                 "Read state is only available where the recipient has read receipts enabled."),
    }


def confirm_url(base_url: str, sheet_id: str, rec: dict) -> str:
    """The link that goes in the message. Short, because it is going into a
    WhatsApp message that a person reads on a phone at 6am."""
    return f"{base_url.rstrip('/')}/c/{sheet_id}/{rec['id']}/{rec['token']}"
