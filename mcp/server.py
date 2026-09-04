#!/usr/bin/env python3
"""
Mark MCP server — exposes Mark's line-producer capabilities as MCP tools.

This makes Mark callable from any MCP client (Claude Desktop, Claude Code,
Cursor, or another agent) instead of only the web UI. Each tool is a thin,
typed wrapper over an existing Mark REST endpoint — no business logic is
duplicated; the backend stays the single source of truth.

The send flow is deliberately split into `propose_callsheet_send` and
`confirm_callsheet_send` so a calling agent expresses human-in-the-loop
approval natively: propose returns exactly who would be contacted, a human
approves the proposal_id, and only then does confirm dispatch.

Run:
    MARK_API_BASE=https://your-backend MARK_API_KEY=... python server.py

Config (env):
    MARK_API_BASE   backend base URL (default http://localhost:8000)
    MARK_API_KEY    the X-API-Key shared secret, if the backend enforces one
"""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP

API_BASE = os.getenv("MARK_API_BASE", "http://localhost:8000").rstrip("/")
API_KEY = os.getenv("MARK_API_KEY")
_TIMEOUT = float(os.getenv("MARK_MCP_TIMEOUT", "240"))

_CURRENCY_SYMBOLS = {"INR": "₹", "USD": "$", "GBP": "£"}

mcp = FastMCP("mark")


async def _post(path: str, body: dict) -> Any:
    """POST JSON to the Mark backend and return the parsed response. Surfaces the
    backend's error detail as a readable string rather than a raw stack trace."""
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["X-API-Key"] = API_KEY
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(f"{API_BASE}{path}", json=body, headers=headers)
    if not resp.is_success:
        try:
            detail = resp.json().get("detail")
        except Exception:
            detail = resp.text[:300]
        return {"error": True, "status": resp.status_code, "detail": detail}
    return resp.json()


def _currency(code: str) -> dict:
    code = (code or "INR").upper()
    return {"code": code, "symbol": _CURRENCY_SYMBOLS.get(code, code)}


@mcp.tool()
async def generate_budget(
    script: str,
    qa: list[dict],
    region: str = "india",
    currency_code: str = "INR",
    breakdown: Optional[dict] = None,
) -> dict:
    """Generate an itemised production budget from a script summary and answered
    questions.

    Args:
        script: A short description or the script text of the production.
        qa: Answered questions, each {"id","question","answer"}. These are ground
            truth — shoot days, scale, locations, DOP, VFX flags, etc.
        region: One of india | uk | usa | hollywood | other. Drives tax + rates.
        currency_code: INR | USD | GBP.
        breakdown: Optional parsed-script summary (scene/location/character counts).
    """
    body = {"script": script, "qa": qa, "region": region, "currency": _currency(currency_code)}
    if breakdown:
        body["breakdown"] = breakdown
    return await _post("/budget/generate", body)


@mcp.tool()
async def generate_budget_async(
    script: str,
    qa: list[dict],
    region: str = "india",
    currency_code: str = "INR",
    breakdown: Optional[dict] = None,
) -> dict:
    """Queue a higher-quality (Sonnet) budget generation and return a job_id
    immediately. Poll `get_job` until status is 'done'. Use this for large
    scripts where the synchronous path would time out."""
    body = {"script": script, "qa": qa, "region": region, "currency": _currency(currency_code)}
    if breakdown:
        body["breakdown"] = breakdown
    return await _post("/budget/generate/async", body)


@mcp.tool()
async def get_job(job_id: str) -> dict:
    """Poll an async job. status ∈ queued | running | done | error. When done,
    `result` holds the budget."""
    return await _post("/jobs/get", {"job_id": job_id})


@mcp.tool()
async def refine_budget(budget: dict, instruction: str, currency_code: str = "INR") -> dict:
    """Apply a plain-English change to an existing budget (e.g. 'cut catering
    20%', 'add a second camera day'). `budget` is the budget_data object from a
    previous generate call."""
    return await _post("/budget/refine", {"budget": budget, "instruction": instruction,
                                          "currency": _currency(currency_code)})


@mcp.tool()
async def render_callsheet(callsheet: dict, template_text: str) -> dict:
    """Render a call sheet as HTML in the producer's own template layout.
    `template_text` is the extracted text of their template."""
    return await _post("/callsheet/render-template", {"callsheet": callsheet, "template_text": template_text})


@mcp.tool()
async def refine_callsheet(callsheet: dict, instruction: str) -> dict:
    """Apply a plain-English change to a call sheet (e.g. 'move unit call to
    6am', 'add nearest hospital')."""
    return await _post("/callsheet/refine", {"callsheet": callsheet, "instruction": instruction})


@mcp.tool()
async def propose_callsheet_send(callsheet: dict, channels: list[str]) -> dict:
    """STAGE a call-sheet send for human approval — nothing is dispatched. Returns
    a proposal_id and a preview of exactly who would be contacted on each channel.
    Show the preview to a human, then call confirm_callsheet_send with the
    proposal_id once approved.

    Args:
        callsheet: The call sheet object (must include a `crew` array).
        channels: Any of email | whatsapp | linkedin | instagram | telegram.
    """
    return await _post("/callsheet/send/propose", {"callsheet": callsheet, "channels": channels})


@mcp.tool()
async def confirm_callsheet_send(proposal_id: str) -> dict:
    """EXECUTE a send previously staged by propose_callsheet_send. Only call this
    after a human has approved the proposal's preview. Idempotent — confirming a
    spent proposal returns the original result instead of sending twice."""
    return await _post("/callsheet/send/confirm", {"proposal_id": proposal_id})


@mcp.tool()
async def enrich_crew(crew_id: str) -> dict:
    """Enrich a stored crew member (contact/role lookup) by their crew_id."""
    return await _post("/crew/enrich", {"crew_id": crew_id})


@mcp.tool()
async def list_rates(region: str = "india", city: str = "") -> dict:
    """List the tenant's rate library. Each row carries `verified` — a verified
    rate came from real production data and is binding on the budget agent; an
    unverified one is a market-reference placeholder."""
    return await _post("/rates/list", {"region": region, "city": city})


@mcp.tool()
async def upsert_rate(rate: dict) -> dict:
    """Create or correct one rate. Identity is (region, city, tier, item_key), so
    upserting the same tuple corrects the existing row rather than duplicating it.

    Args:
        rate: {region, city, tier, item_key, section, desc, unit, rate, currency,
               gst_rate, tds_section?, confidence?, source?, verified_at?}.
               `unit` ∈ day | week | flat | person_day | unit | hour | shift | episode.
               `gst_rate` is a decimal multiplier (0.18), never a percent.
               Set `verified_at` only when the number came from real production data.
    """
    return await _post("/rates/upsert", {"rate": rate})


@mcp.tool()
async def rate_pack(region: str = "india", city: str = "", tier: str = "mid") -> dict:
    """The rate pack the budget agent receives for a region/city/tier, plus the
    verified-coverage figure — the share of the pack confirmed from real data."""
    return await _post("/rates/pack", {"region": region, "city": city, "tier": tier})


@mcp.tool()
async def generate_schedule(
    scenes: Optional[list] = None,
    breakdown: Optional[dict] = None,
    shoot_days: Optional[int] = None,
    start_date: Optional[str] = None,
    title: str = "",
) -> dict:
    """Build a shooting schedule and Day-out-of-Days from a parsed script.

    Pass `scenes` (the per-scene list from /script/parse) for a real schedule, or
    `breakdown` alone to get an indicative shape from the aggregate summary — the
    result is then flagged `synthetic` and must not be presented as a plan.
    `shoot_days`, when given, is binding.
    """
    body: dict = {"shoot_days": shoot_days, "start_date": start_date, "title": title}
    if scenes:
        body["scenes"] = scenes
    if breakdown:
        body["breakdown"] = breakdown
    return await _post("/schedule/generate", body)


@mcp.tool()
async def callsheet_from_schedule(schedule: dict, day: int) -> dict:
    """Seed a call sheet from one shooting day of a schedule. Returns a partial
    call sheet: call times, weather and nearest hospital are listed under `needs`
    rather than invented. Pass it to refine_callsheet or render_callsheet next."""
    return await _post("/callsheet/from-schedule", {"schedule": schedule, "day": day})


@mcp.tool()
async def variance_ledger(
    budget: dict,
    actuals: Optional[list] = None,
    actuals_csv: str = "",
    production: str = "",
    currency_code: str = "INR",
    threshold: float = 0.10,
) -> dict:
    """Compare an approved budget against final actuals and return the variance
    ledger: every line that moved beyond the threshold, classified as estimate
    error, scope change, vendor variance or unrecorded cost, with the evidence
    for each classification.

    Args:
        budget: the budget_data object.
        actuals: rows of {code?, desc, amount, qty?, vendor?}.
        actuals_csv: alternatively, a pasted CSV/TSV cost report.
        threshold: materiality, default 0.10 (the SOW's 10%).
    """
    return await _post("/variance/compute", {
        "budget": budget, "actuals": actuals or [], "actuals_csv": actuals_csv,
        "production": production, "currency": currency_code, "threshold": threshold,
    })


@mcp.tool()
async def teardown(ledgers: list[dict], productions_per_year: int = 12) -> dict:
    """Across two or more variance ledgers: the lines that are wrong in the same
    direction every time, and what that pattern costs across a year's volume.
    Nothing is called recurring from a single production."""
    return await _post("/teardown/compute", {"ledgers": ledgers,
                                             "productions_per_year": productions_per_year})


@mcp.tool()
async def india_compliance(budget: dict, payee_types: Optional[dict] = None) -> dict:
    """GST and TDS for an Indian budget, line by line: gross, GST, the TDS section
    and rate, the deduction and the net payable per vendor, plus which GST input
    credit is blocked. Indicative only — the response carries a disclaimer and the
    rules need a chartered accountant's sign-off before being quoted."""
    return await _post("/compliance/compute", {"budget": budget,
                                               "payee_types": payee_types or {}})


@mcp.tool()
async def payment_schedule(budget: dict, advance_pct: float = 0.4) -> dict:
    """Split a budget into advance and balance stages with GST and TDS resolved at
    each — the cash-timing view an Indian production actually runs on."""
    return await _post("/compliance/payment-schedule", {"budget": budget,
                                                        "advance_pct": advance_pct})


@mcp.tool()
async def budget_versions(project_id: str) -> dict:
    """Every saved version of a project's budget, newest first, with line counts
    and totals — enough to pick two and diff them."""
    return await _post("/budget/versions", {"project_id": project_id})


@mcp.tool()
async def budget_diff(
    before: Optional[dict] = None,
    after: Optional[dict] = None,
    project_id: str = "",
    before_id: str = "",
    after_id: str = "",
) -> dict:
    """What moved between two budget versions: repriced, added, removed, and
    reworded-at-the-same-price, with the money attached to each. Pass two budget
    objects, two stored ids, or a project_id alone to compare its two most recent
    versions."""
    return await _post("/budget/diff", {
        "before": before, "after": after, "project_id": project_id or None,
        "before_id": before_id or None, "after_id": after_id or None,
    })


@mcp.tool()
async def export_budget(budget: dict, fmt: str = "xlsx", currency_code: str = "INR") -> dict:
    """Export a budget. `fmt` is `xlsx` (returns base64) or `mm` (returns text — a
    delimited account/detail file for Movie Magic Budgeting's import, NOT a .mmb;
    that format is proprietary and a wrong one opens with wrong numbers)."""
    return await _post("/budget/export", {"budget": budget, "format": fmt,
                                          "currency": currency_code})


@mcp.tool()
async def teardown_report(ledgers: list[dict], client: str = "",
                          productions_per_year: int = 0) -> dict:
    """Render the Stage 0 teardown report (findings, recurring variance,
    annualised cost, remediation) as printable HTML from two or more variance
    ledgers. Pass productions_per_year to include the annualised section — it is
    omitted rather than guessed when the volume is unknown."""
    body: dict = {"ledgers": ledgers, "client": client}
    if productions_per_year:
        body["productions_per_year"] = productions_per_year
    return await _post("/teardown/report", body)


@mcp.tool()
async def delivery_board(send_id: str) -> dict:
    """Who has the call sheet, who has opened it, and who has confirmed — plus
    `outstanding`, ordered by how worried to be (a failed send first, then people
    who never opened it, then people who read it and didn't reply). Delivery and
    read state depend on the messaging provider; confirmation is a human tapping
    a link and is the only one that means anything on a shoot day."""
    return await _post("/callsheet/delivery/board", {"send_id": send_id})


@mcp.tool()
async def roster_search(query: str = "", kind: str = "", tag: str = "") -> dict:
    """Search the crew and vendor roster. `kind` is `person` or `vendor`.
    Returns each entry's engagement count and median rate — the answer to
    "who have we used, and what do we pay them"."""
    return await _post("/roster/search", {"query": query, "kind": kind, "tag": tag})


@mcp.tool()
async def roster_history(roster_id: str) -> dict:
    """What this person or vendor has actually been paid: every engagement, the
    median (not the mean — one emergency weekend rate should not move it), the
    spread, and the direction of travel. `confidence` says whether it is one
    observation or a real history."""
    return await _post("/roster/history", {"id": roster_id})


@mcp.tool()
async def roster_from_ledger(ledger: dict, production: str = "") -> dict:
    """Record what vendors were actually paid on a production, from its variance
    ledger. This is the join that makes a teardown populate the vendor history as
    well as the rate card."""
    return await _post("/roster/from-ledger", {"ledger": ledger, "production": production})


@mcp.tool()
async def roster_propose_rates(city: str = "", region: str = "india", tier: str = "mid") -> dict:
    """Turn roster history into rate-card proposals — the median of at least two
    engagements, never one. Pass accepted ones to the rates endpoints. A proposal
    whose `item_key_source` is "derived from role" had its key inferred rather
    than confirmed."""
    return await _post("/roster/propose-rates", {"region": region, "city": city, "tier": tier})


@mcp.tool()
async def recent_traces(limit: int = 20) -> dict:
    """Read recent agent-run traces (name, model, latency, tokens, cache_hit,
    ok/error) — useful for debugging why a budget came out the way it did."""
    return await _post("/admin/traces", {"limit": limit})


if __name__ == "__main__":
    mcp.run()
