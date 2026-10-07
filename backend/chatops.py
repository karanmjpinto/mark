"""
chatops.py — driving Mark from a chat channel.

Mark already *sends* to WhatsApp (call sheets, through Unipile) and already
exposes its tools to agents (`mcp/`). Neither covers the inbound conversational
case: a producer asking for a budget from the same thread they'd text their
first AD in. That agent lives on a chat platform — CodeWords today — and calls
this backend over HTTP. Three things it needs that the REST API did not have:

  * **Who is texting.** The API authenticates a tenant, not a person, and a
    phone number is the only identity a chat channel carries. Operators are
    enrolled per tenant and resolved on the way in; an unenrolled number is
    refused before it reaches a tool, because the agent's number is public by
    construction. Role decides what it may do: only a producer can release a
    send.

  * **A budget that fits in a message.** The REST shapes are built for a web
    table — 150 lines of JSON. A phone gets the total, the sections carrying the
    money, and what is unverified, in under ~1,400 characters. Same numbers, one
    screen, and `₹1.86 Cr` rather than `18600000`.

  * **Somewhere to put the document.** An .xlsx or the Stage 0 report cannot be
    read in a thread. `create_share()` parks *Mark-rendered* HTML behind an
    opaque, expiring, login-free URL — the same trick the call-sheet
    confirmation page uses, for the same reason: the one thing every chat
    channel reliably carries is a link. Callers never supply the HTML, so a
    share can't be used to serve someone else's markup from our origin.

Pure logic, no FastAPI import: the renderers, the token and the phone parser are
covered offline in `evals/test_chatops.py`.
"""

from __future__ import annotations

import hashlib
import hmac
import html as _html
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

_redis = None

# WhatsApp accepts 4096 characters. A producer reads about a third of that
# before scrolling, so that is the budget the renderers work to.
MAX_CHARS = 1400

# A production office, not a mailing list. The cap is here so a mistake in an
# enrolment loop cannot grow the record unboundedly.
MAX_OPERATORS = 200

ROLES: dict[str, set[str]] = {
    "viewer": {"read"},
    "coordinator": {"read", "write"},
    "producer": {"read", "write", "send"},
}
DEFAULT_ROLE = "coordinator"

SHARE_TTL_SECONDS = int(os.getenv("SHARE_TTL_SECONDS", str(7 * 24 * 3600)))

# In-memory fallback for shares when Redis is absent (local dev, offline tests).
_mem_shares: dict[str, dict] = {}


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


# ── phone numbers ─────────────────────────────────────────────────────────────
# Every channel spells the same number differently: `whatsapp:+919820012345`,
# `919820012345@c.us`, `98200 12345`. One parser, one stored form (E.164), so
# enrolment and lookup cannot disagree about who a person is.

def normalise_phone(raw: Any) -> Optional[str]:
    """Any channel's spelling of a number → `+<digits>`, or None if it isn't one."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if ":" in s:                      # whatsapp:+9198..., tel:+9198...
        s = s.rsplit(":", 1)[-1]
    if "@" in s:                      # 919820012345@c.us
        s = s.split("@", 1)[0]
    explicit = s.lstrip().startswith("+")
    digits = re.sub(r"\D", "", s)
    if not digits:
        return None
    if not explicit:
        if digits.startswith("00"):        # 0049… — an explicit international prefix
            digits = digits[2:]
        else:
            cc = (os.getenv("DEFAULT_COUNTRY_CODE") or "").strip().lstrip("+")
            if digits.startswith("0") and len(digits) > 10:
                digits = digits[1:]           # a national trunk prefix: 098… → 98…
            if cc and len(digits) <= 10:
                digits = cc + digits
            elif len(digits) < 11:
                # A bare local number with no country code configured. Guessing
                # one would silently enrol a stranger in another country.
                return None
    if not 8 <= len(digits) <= 15:     # E.164 bounds
        return None
    return "+" + digits


def mask_phone(phone: str) -> str:
    """For logs and traces: keep the country code and last two digits."""
    if not phone:
        return ""
    return phone[:3] + "…" + phone[-2:] if len(phone) > 6 else "…"


# ── the operator roster ───────────────────────────────────────────────────────
# One record per tenant, keyed by phone. Small by nature, so it is a single
# document rather than a set plus a key apiece: `operators()` is one read.

_mem_roster: dict[str, dict] = {}


def _roster_key() -> str:
    return _tkey("chatops:operators")


def _load_roster() -> dict:
    if _redis:
        raw = _redis.get(_roster_key())
        return json.loads(raw) if raw else {}
    return dict(_mem_roster.setdefault(_roster_key(), {}))



def _store_roster(roster: dict) -> None:
    if _redis:
        _redis.set(_roster_key(), json.dumps(roster))
    else:
        _mem_roster[_roster_key()] = roster


def enrol(phone: Any, *, name: str = "", role: str = DEFAULT_ROLE) -> dict:
    """Admit a number to this tenant's chat channel. Re-enrolling corrects the
    record in place rather than creating a second one."""
    e164 = normalise_phone(phone)
    if not e164:
        raise ValueError(f"not a usable phone number: {phone!r}")
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r} — one of {sorted(ROLES)}")
    roster = _load_roster()
    if e164 not in roster and len(roster) >= MAX_OPERATORS:
        raise ValueError(f"operator limit reached ({MAX_OPERATORS})")
    existing = roster.get(e164) or {}
    roster[e164] = {
        "phone": e164,
        "name": (name or existing.get("name") or "").strip(),
        "role": role,
        "active_project": existing.get("active_project"),
        "enrolled_at": existing.get("enrolled_at") or _now(),
        "last_seen": existing.get("last_seen"),
        "messages": existing.get("messages", 0),
    }
    _store_roster(roster)
    return roster[e164]


def revoke(phone: Any) -> bool:
    e164 = normalise_phone(phone)
    roster = _load_roster()
    if not e164 or e164 not in roster:
        return False
    del roster[e164]
    _store_roster(roster)
    return True


def operators() -> list[dict]:
    return sorted(_load_roster().values(), key=lambda o: (o.get("name") or "", o["phone"]))


def operator(phone: Any) -> Optional[dict]:
    e164 = normalise_phone(phone)
    return _load_roster().get(e164) if e164 else None


def can(op: Optional[dict], action: str) -> bool:
    """Is this operator allowed to do that? Unknown number → no."""
    if not op:
        return False
    return action in ROLES.get(op.get("role") or "", set())


def set_active_project(phone: Any, project_id: Optional[str]) -> Optional[dict]:
    """The thread's current production. A chat has no URL to carry it, so it is
    remembered per operator — 'what's the budget' means *this* one."""
    e164 = normalise_phone(phone)
    roster = _load_roster()
    if not e164 or e164 not in roster:
        return None
    roster[e164]["active_project"] = project_id or None
    _store_roster(roster)
    return roster[e164]


def touch(phone: Any) -> Optional[dict]:
    """Record that this operator said something. Cheap, and it answers 'is
    anyone actually using the WhatsApp agent' without a separate analytics hop."""
    e164 = normalise_phone(phone)
    roster = _load_roster()
    if not e164 or e164 not in roster:
        return None
    roster[e164]["last_seen"] = _now()
    roster[e164]["messages"] = int(roster[e164].get("messages") or 0) + 1
    _store_roster(roster)
    return roster[e164]


def session(phone: Any) -> Optional[dict]:
    """What the agent needs before its first tool call: who this is, what they
    may do, and which production they are talking about. None → not enrolled."""
    op = operator(phone)
    if not op:
        return None
    op = touch(phone) or op
    return {
        "phone": op["phone"],
        "name": op.get("name") or "",
        "role": op.get("role"),
        "can": sorted(ROLES.get(op.get("role") or "", set())),
        "active_project": op.get("active_project"),
        "enrolled_at": op.get("enrolled_at"),
        "last_seen": op.get("last_seen"),
        "messages": op.get("messages", 0),
    }


# ── share links ───────────────────────────────────────────────────────────────
# An opaque, expiring URL for one rendered document. The token is an HMAC over
# the id, so a guessed id is useless, and the record carries its own expiry so
# an in-memory deploy expires links the same way Redis does.

def _share_secret() -> bytes:
    return (os.getenv("SHARE_SECRET") or os.getenv("CONFIRM_SECRET")
            or os.getenv("CONNECTIONS_SECRET") or "mark-dev-secret").encode()


def make_share_token(share_id: str) -> str:
    sig = hmac.new(_share_secret(), share_id.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{secrets.token_urlsafe(6)}.{sig}"


def share_token_matches(token: str, share_id: str) -> bool:
    if not token or "." not in token:
        return False
    expected = hmac.new(_share_secret(), share_id.encode(), hashlib.sha256).hexdigest()[:16]
    return hmac.compare_digest(token.rsplit(".", 1)[-1], expected)


def _share_key(share_id: str) -> str:
    # Global on purpose: the page is fetched without an API key, so it cannot be
    # behind a tenant prefix the reader has no way to supply.
    return f"g:share:{share_id}"


def create_share(*, kind: str, title: str, html: str, ttl: Optional[int] = None) -> dict:
    """Park one Mark-rendered document behind a login-free URL.

    `html` always comes from this backend's own renderers. Nothing user-supplied
    is stored here: a share is not a hosting endpoint.
    """
    ttl = int(SHARE_TTL_SECONDS if ttl is None else ttl)   # ttl=0 means "already gone"
    share_id = secrets.token_hex(8)
    record = {
        "id": share_id,
        "kind": kind,
        "title": title,
        "html": html,
        "created_at": _now(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat(),
    }
    if _redis:
        _redis.setex(_share_key(share_id), ttl, json.dumps(record))
    else:
        _mem_shares[share_id] = record
    return {"share_id": share_id, "token": make_share_token(share_id),
            "kind": kind, "title": title, "expires_at": record["expires_at"]}


def get_share(share_id: str, token: str) -> Optional[dict]:
    if not share_id or not share_token_matches(token, share_id):
        return None
    if _redis:
        raw = _redis.get(_share_key(share_id))
        record = json.loads(raw) if raw else None
    else:
        record = _mem_shares.get(share_id)
    if not record:
        return None
    try:
        if datetime.fromisoformat(record["expires_at"]) <= datetime.now(timezone.utc):
            _mem_shares.pop(share_id, None)
            return None
    except Exception:
        pass
    return record


def share_url(base_url: str, share: dict) -> str:
    return f"{(base_url or '').rstrip('/')}/s/{share['share_id']}/{share['token']}"


# ── money, the way a producer says it ─────────────────────────────────────────

def fmt_money(amount: Any, *, code: str = "INR", symbol: str = "") -> str:
    """`₹1.86 Cr`, not `₹18,600,000`. Indian budgets are read in crore and lakh;
    everything else in millions and thousands."""
    if amount is None or amount == "":
        return "—"
    try:
        v = float(amount)
    except (TypeError, ValueError):
        return "—"
    sym = symbol or ("₹" if (code or "").upper() == "INR" else (f"{code.upper()} " if code else ""))
    sign = "-" if v < 0 else ""
    v = abs(v)
    if (code or "").upper() == "INR":
        if v >= 1e7:
            return f"{sign}{sym}{_trim(v / 1e7)} Cr"
        if v >= 1e5:
            return f"{sign}{sym}{_trim(v / 1e5)} L"
        return f"{sign}{sym}{v:,.0f}"
    if v >= 1e6:
        return f"{sign}{sym}{_trim(v / 1e6)}M"
    if v >= 1e4:
        return f"{sign}{sym}{_trim(v / 1e3)}K"
    return f"{sign}{sym}{v:,.0f}"


def _trim(v: float) -> str:
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def _pct(v: Any) -> str:
    try:
        return f"{float(v) * 100:+.1f}%"
    except (TypeError, ValueError):
        return ""


def _n(count: Any, singular: str, plural: str = "") -> str:
    """"1 company move", "3 company moves" — a producer notices the grammar."""
    try:
        c = int(count)
    except (TypeError, ValueError):
        return ""
    return f"{c} {singular if c == 1 else (plural or singular + 's')}"


def clip(text: str, limit: int = MAX_CHARS) -> str:
    """Trim to a message, on a line boundary, and say that it was trimmed."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 24]
    if "\n" in cut:
        cut = cut[: cut.rfind("\n")]
    return cut.rstrip() + "\n… (trimmed)"


# ── renderers: the same numbers, one screen ───────────────────────────────────

def _sections(budget: dict) -> list[dict]:
    return [s for s in (budget or {}).get("sections", []) if isinstance(s, dict)]


def _section_total(section: dict) -> float:
    return sum(float(i.get("amount") or 0) for i in (section.get("items") or []))


def budget_text(budget: dict, *, currency: str = "INR", url: str = "", top: int = 6) -> str:
    """A budget as a producer would text it: the total, where it sits, and what
    is not yet trustworthy."""
    budget = budget or {}
    sections = _sections(budget)
    items = [i for s in sections for i in (s.get("items") or [])]
    total = sum(float(i.get("amount") or 0) for i in items)
    money = lambda v: fmt_money(v, code=currency)

    head = [f"*{budget.get('title') or 'Budget'}*"]
    facets = [budget.get("production_type"), 
              f"{budget.get('shoot_days')} shoot days" if budget.get("shoot_days") else "",
              budget.get("scale_tier"),
              f"{len(budget.get('locations') or [])} locations" if budget.get("locations") else ""]
    head.append(" · ".join(f for f in facets if f))
    head.append(f"Total {money(total)} · {len(sections)} sections · {len(items)} lines")

    ranked = sorted(((s, _section_total(s)) for s in sections), key=lambda p: -p[1])
    lines = ["", "Where it sits:"]
    for s, amt in ranked[:top]:
        share = f" ({amt / total * 100:.0f}%)" if total else ""
        lines.append(f"• {s.get('name') or s.get('code')} {money(amt)}{share}")
    rest = ranked[top:]
    if rest:
        lines.append(f"+{len(rest)} more sections {money(sum(a for _, a in rest))}")

    warn = []
    amber = [i for i in items if (i.get("conf") or "green") != "green"]
    if amber:
        warn.append(f"⚠ {_n(len(amber), 'line')} on unverified rates")
    flags = [f for f in (budget.get("flags") or []) if f]
    if flags:
        warn.append(f"⚠ {_n(len(flags), 'flag')}: {_first_line(flags[0])}")
    excluded = [x for x in (budget.get("excluded") or []) if x]
    if excluded:
        warn.append(f"Not included: {len(excluded)} items — ask what's excluded")
    if warn:
        lines += [""] + warn
    if url:
        lines += ["", f"Full budget: {url}"]
    return clip("\n".join(head + lines))


def _first_line(value: Any, limit: int = 90) -> str:
    text = str(value if not isinstance(value, dict) else
               (value.get("message") or value.get("note") or value.get("flag") or value))
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def schedule_text(sched: dict, *, url: str = "", days: int = 6) -> str:
    sched = sched or {}
    day_rows = sched.get("days") or []
    head = [f"*{sched.get('title') or 'Schedule'} — {_n(sched.get('total_days', len(day_rows)), 'shoot day')}*",
            " · ".join(f for f in [
                _n(sched.get("total_scenes"), "scene") if sched.get("total_scenes") else "",
                f"{sched.get('total_pages')} pages" if sched.get("total_pages") else "",
                _n(sched.get("company_moves"), "company move") if sched.get("company_moves") else "",
                _n(sched.get("night_days"), "night day") if sched.get("night_days") else "",
            ] if f)]
    lines = [""]
    for d in day_rows[:days]:
        locs = d.get("locations") or []
        where = locs[0] if locs else "—"
        if len(locs) > 1:
            where += f" +{len(locs) - 1}"
        scenes = ",".join(str(n) for n in (d.get("scene_numbers") or [])[:5])
        if len(d.get("scene_numbers") or []) > 5:
            scenes += "…"
        when = d.get("date") or ""
        lines.append(f"D{d.get('day')}{' ' + when if when else ''} · {where} · sc {scenes} ({d.get('pages')} pp)")
    if len(day_rows) > days:
        lines.append(f"+{len(day_rows) - days} more days")
    if sched.get("synthetic"):
        lines += ["", "⚠ Built from the budget's location list, not a parsed script — scene count is indicative."]
    warnings = [w for w in (sched.get("warnings") or []) if w]
    if warnings:
        lines.append(f"⚠ {_n(len(warnings), 'warning')}: {_first_line(warnings[0])}")
    if url:
        lines += ["", f"Stripboard: {url}"]
    return clip("\n".join(head + lines))


def variance_text(ledger: dict, *, url: str = "", top: int = 5) -> str:
    ledger = ledger or {}
    cur = ledger.get("currency") or "INR"
    money = lambda v: fmt_money(v, code=cur)
    t = ledger.get("totals") or {}
    delta = float(t.get("delta") or 0)
    direction = "over" if delta > 0 else "under"
    head = [f"*Teardown — {ledger.get('production') or 'production'}*",
            f"Budget {money(t.get('budget'))} → actual {money(t.get('actual'))} · "
            f"{direction} by {money(abs(delta))} ({_pct(t.get('delta_pct'))})"]
    material = [l for l in (ledger.get("lines") or []) if l.get("material")]
    thr = ledger.get("threshold")
    head.append(_n(len(material), "material line")
                + (f" beyond ±{float(thr) * 100:.0f}%" if thr else ""))

    lines = ["", "Worst:"]
    for l in sorted(material, key=lambda l: -abs(float(l.get("delta") or 0)))[:top]:
        lines.append(f"• {l.get('section_name') or l.get('section')} · {l.get('desc')} "
                     f"{'+' if float(l.get('delta') or 0) > 0 else '−'}{money(abs(float(l.get('delta') or 0)))} "
                     f"({(l.get('classification') or '').replace('_', ' ')})")
    tail = []
    if t.get("unbudgeted_spend"):
        tail.append(f"Unbudgeted spend {money(t['unbudgeted_spend'])}")
    if t.get("unspent_budget"):
        tail.append(f"Unspent {money(t['unspent_budget'])}")
    if ledger.get("unmatched_actuals"):
        tail.append(f"{ledger['unmatched_actuals']} actual rows matched nothing")
    if tail:
        lines += [""] + tail
    if url:
        lines += ["", f"Report: {url}"]
    return clip("\n".join(head + lines))


def teardown_text(result: dict, *, currency: str = "INR", url: str = "") -> str:
    result = result or {}
    a = result.get("annualised") or {}
    money = lambda v: fmt_money(v, code=currency)
    if a.get("error"):
        return f"*Teardown*\n{a['error']}"
    head = [f"*Across {_n(result.get('productions') or a.get('sample_productions') or 0, 'production')}*",
            f"{_n(a.get('recurring_lines', 0), 'line')} wrong in the same direction every time",
            f"{money(a.get('recurring_cost_per_production'))} per production · "
            f"{money(a.get('recurring_cost_per_year'))} a year at "
            f"{_n(a.get('productions_per_year'), 'production')}"]
    lines = []
    patterns = result.get("recurring_patterns") or []
    if patterns:
        lines += ["", "Repeat offenders:"]
        for p in patterns[:4]:
            lines.append(f"• {p.get('desc') or p.get('section_name') or p.get('section')} "
                         f"{money(p.get('median_delta') or p.get('mean_delta') or p.get('delta'))} "
                         f"× {p.get('productions') or p.get('occurrences') or ''}".rstrip())
    if a.get("method"):
        lines += ["", f"Method: {_first_line(a['method'], 160)}"]
    if result.get("rate_proposals"):
        lines.append(f"{len(result['rate_proposals'])} rate corrections proposed — "
                     f"say 'apply rate proposals' to accept.")
    if url:
        lines += ["", f"Stage 0 report: {url}"]
    return clip("\n".join(head + lines))


_STATE_WORDS = {
    "failed": "send failed",
    "queued": "not sent yet",
    "sent": "sent, not opened",
    "delivered": "delivered, not opened",
    "read": "read, no reply",
}


def delivery_text(summary: dict, *, top: int = 8) -> str:
    summary = summary or {}
    head = [f"*Call sheet — {summary.get('shoot_day') or 'shoot day'}"
            f"{', ' + summary['date'] if summary.get('date') else ''}*",
            f"{summary.get('confirmed', 0)} of {summary.get('total', 0)} confirmed "
            f"({float(summary.get('confirmed_pct') or 0) * 100:.0f}%)"]
    outstanding = summary.get("outstanding") or []
    lines = []
    if not outstanding:
        lines += ["", "Everyone has confirmed."]
    else:
        lines += ["", "Chase, worst first:"]
        for r in outstanding[:top]:
            state = _STATE_WORDS.get(r.get("state"), r.get("state") or "")
            contact = r.get("phone") or r.get("email") or ""
            lines.append(f"• {r.get('name')}{' · ' + r['role'] if r.get('role') else ''} — {state}"
                         f"{' · ' + contact if contact else ''}")
        if len(outstanding) > top:
            lines.append(f"+{len(outstanding) - top} more")
    declined = (summary.get("counts") or {}).get("declined") or 0
    if declined:
        lines.append(f"{_n(declined, 'person')} can't make it — they need replacing.")
    if not summary.get("read_state_verified"):
        lines += ["", "Read state comes from the provider and is unverified; confirmations are ours."]
    return clip("\n".join(head + lines))


# ── the one document a chat thread can't hold ─────────────────────────────────

def budget_html(budget: dict, *, currency: str = "INR", title: str = "") -> str:
    """A printable budget table. No framework, no CDN — it is opened on a phone
    on set data, and it has to render before anyone loses patience."""
    e = lambda v: _html.escape(str(v if v is not None else ""))
    money = lambda v: fmt_money(v, code=currency)
    sections = _sections(budget)
    items = [i for s in sections for i in (s.get("items") or [])]
    total = sum(float(i.get("amount") or 0) for i in items)

    rows = []
    for s in sections:
        st = _section_total(s)
        rows.append(f'<tr class="sec"><th colspan="2">{e(s.get("code"))} {e(s.get("name"))}</th>'
                    f'<td class="num">{e(money(st))}</td></tr>')
        for i in (s.get("items") or []):
            conf = (i.get("conf") or "green").lower()
            dot = {"green": "", "amber": ' <span class="amber" title="unverified rate">●</span>',
                   "red": ' <span class="red" title="no basis">●</span>'}.get(conf, "")
            sub = f'<div class="sub">{e(i.get("sub"))}</div>' if i.get("sub") else ""
            rows.append(f'<tr><td class="code">{e(i.get("code"))}</td>'
                        f'<td>{e(i.get("desc"))}{dot}{sub}</td>'
                        f'<td class="num">{e(money(i.get("amount")))}</td></tr>')

    notes = []
    for key, label in (("comparable_note", "Comparables"), ("confidence_note", "Confidence")):
        if budget.get(key):
            notes.append(f"<p><strong>{label}.</strong> {e(budget[key])}</p>")
    if budget.get("excluded"):
        lis = "".join(f"<li>{e(_first_line(x, 200))}</li>" for x in budget["excluded"])
        notes.append(f"<p><strong>Not included.</strong></p><ul>{lis}</ul>")
    if budget.get("flags"):
        lis = "".join(f"<li>{e(_first_line(x, 200))}</li>" for x in budget["flags"])
        notes.append(f"<p><strong>Flags.</strong></p><ul>{lis}</ul>")

    heading = title or budget.get("title") or "Budget"
    facets = " · ".join(f for f in [budget.get("production_type"),
                                    f"{budget.get('shoot_days')} shoot days" if budget.get("shoot_days") else "",
                                    budget.get("scale_tier")] if f)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(heading)}</title><style>
/* Mark's paper surface, inlined — this page is served from Redis with no asset
   pipeline behind it, and it has to render on set data before anyone gives up. */
:root{{color-scheme:light;--ink:#171714;--paper:#D5D6CE;--paper-raise:#CBCCC3;
--paper-line:#BDBEB5;--dim-ink:#6B6C65;--signal:#C8330A;--gold:#B8842D}}
body{{margin:0 auto;padding:28px 18px 72px;max-width:760px;background:var(--paper);color:var(--ink);
font:15px/1.45 ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}}
h1{{font-size:23px;margin:0 0 4px;letter-spacing:-.01em}}
.facets{{color:var(--dim-ink);margin:0 0 22px}}
table{{width:100%;border-collapse:collapse}}
td,th{{padding:7px 6px;text-align:left;vertical-align:top;border-bottom:1px solid var(--paper-line)}}
tr.sec th{{background:var(--paper-raise);font-size:12px;letter-spacing:.06em;text-transform:uppercase}}
.num{{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}}
.code{{color:var(--dim-ink);white-space:nowrap;font-variant-numeric:tabular-nums}}
.sub{{color:var(--dim-ink);font-size:13px}}
.amber{{color:var(--gold)}} .red{{color:var(--signal)}}
tfoot td{{font-weight:700;border-top:2px solid var(--ink);border-bottom:none;padding-top:11px}}
.notes{{margin-top:30px;font-size:14px}}
.notes ul{{margin:4px 0 14px 18px;padding:0}}
.legend{{margin-top:24px;color:var(--dim-ink);font-size:13px}}
@media print{{body{{background:#fff;padding:0}}tr.sec th{{background:#f2f2ef}}}}
</style></head><body>
<h1>{e(heading)}</h1><p class="facets">{e(facets)}</p>
<table><tbody>{''.join(rows)}</tbody>
<tfoot><tr><td colspan="2">Total</td><td class="num">{e(money(total))}</td></tr></tfoot></table>
<div class="notes">{''.join(notes)}</div>
<p class="legend"><span class="amber">●</span> unverified rate — a market placeholder, not a quote.
Replace it from a teardown before this number reaches a client.</p>
</body></html>"""


def share_page(record: dict) -> str:
    """What `/s/{id}/{token}` serves: the stored document, unchanged."""
    return record.get("html") or ""


def expired_page(message: str = "This link has expired.") -> str:
    e = _html.escape(message)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Link expired</title>
<style>:root{{color-scheme:dark}}body{{margin:0;min-height:100vh;display:grid;place-items:center;
font:16px/1.5 ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
color:#D5D6CE;background:#171714;padding:24px}}
p{{max-width:30ch;text-align:center}}.sub{{color:#8A8B83;font-size:14px}}</style></head>
<body><div><p>{e}</p><p class="sub">Ask the production office to send it again.</p></div></body></html>"""
