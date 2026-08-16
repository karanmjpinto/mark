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
async def recent_traces(limit: int = 20) -> dict:
    """Read recent agent-run traces (name, model, latency, tokens, cache_hit,
    ok/error) — useful for debugging why a budget came out the way it did."""
    return await _post("/admin/traces", {"limit": limit})


if __name__ == "__main__":
    mcp.run()
