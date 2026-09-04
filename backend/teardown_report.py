"""
teardown_report.py — the Stage 0 deliverable, generated.

`docs/SOW-stage-0-teardown.md` promises four things: a Teardown Report (D1), a
Variance Ledger (D2, a spreadsheet — see `exporters.ledger_to_xlsx`), an
annualised cost of the current process (D3) and a remediation plan (D4). Until
now all four were assembled by hand at the end of five days' work.

This renders D1, D3 and D4 as one printable HTML document from ledgers the system
already computed. It is deliberately a renderer and not a writer: every figure in
the output traces to a line in a ledger, and where the data cannot support a
statement the document says so rather than filling the gap with plausible prose.
That constraint is not stylistic — the SOW says every finding must be evidenced
against a specific document the client supplied, and a generated report that
editorialises would breach it.

Print-first: the CSS carries `@page` margins and page-break rules, so "Print →
Save as PDF" in any browser produces the deliverable. No PDF library, no
headless browser, no new dependency.
"""

from __future__ import annotations

import html
from datetime import date
from typing import Any, Optional

# Classification → what Mark would actually change. The mapping is the entire
# opinion in this document, and it is stated as a rule rather than improvised per
# report so two engagements get the same recommendation for the same finding.
REMEDIATION = {
    "estimate_error": (
        "Put the line on the rate card",
        "The estimate moved because nothing anchored it. Capture the verified rate from these "
        "actuals so the next budget starts from what this line costs at this company, not from "
        "a market reference."),
    "vendor_variance": (
        "Re-quote or renegotiate",
        "Quantity held and the unit rate moved, so this is a supply problem rather than an "
        "estimating one. Worth a second quote before the next job and a rate agreed in advance."),
    "scope_change": (
        "Price the change when it happens",
        "More was bought than was budgeted. The cost is not the problem — the silence is. A "
        "change order at the moment of the decision keeps the approved budget honest."),
    "unrecorded_cost": (
        "Give the cost a home in the template",
        "This was spent against no budget line at all, so it could not have been approved or "
        "tracked. It needs a line in the standing template, not a better estimate."),
}

_STATUS_LABEL = {
    "over": "Over", "under": "Under", "unbudgeted": "Unbudgeted",
    "not_spent": "Not spent", "on_budget": "On budget",
}


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _money(value: Any, currency: str = "INR") -> str:
    try:
        n = float(value or 0)
    except (TypeError, ValueError):
        return "—"
    return f"{currency} {n:,.0f}"


def _pct(value: Any) -> str:
    return "—" if value is None else f"{float(value):+.0%}"


def render(
    ledgers: list[dict],
    *,
    client: str = "",
    annualised: Optional[dict] = None,
    patterns: Optional[list[dict]] = None,
    prepared_on: Optional[str] = None,
    currency: str = "INR",
    top_findings: int = 12,
) -> str:
    """Ledgers in, one printable HTML document out.

    `annualised` and `patterns` come from `variance.annualise()` /
    `variance.recurring_patterns()`. When they are absent the document renders
    the per-production findings only and says the annualisation is missing —
    it never computes an annual figure from a single production.
    """
    ledgers = [l for l in (ledgers or []) if l]
    prepared_on = prepared_on or date.today().isoformat()
    patterns = patterns or []

    parts = [_head(client, ledgers, prepared_on)]
    parts.append(_scope(ledgers, prepared_on, client))
    parts.append(_findings(ledgers, currency, top_findings))
    parts.append(_recurring(patterns, currency, len(ledgers)))
    parts.append(_annualised(annualised, currency))
    parts.append(_remediation(patterns, ledgers))
    parts.append(_limitations(ledgers))
    parts.append("</body></html>")
    return "\n".join(parts)


def _head(client: str, ledgers: list[dict], prepared_on: str) -> str:
    title = f"Production teardown — {client}" if client else "Production teardown"
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{_e(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Anton&family=Archivo:wght@400;500;600;700&display=swap" rel="stylesheet"/>
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
:root{{--ink:#171714;--paper:#D5D6CE;--line:#BDBEB5;--dim:#6B6C65;--tally:#C8330A;}}
body{{background:#fff;color:var(--ink);font-family:'Archivo',system-ui,sans-serif;
  font-size:14px;line-height:1.5;-webkit-font-smoothing:antialiased;}}
.sheet{{max-width:920px;margin:0 auto;padding:56px 48px 80px;}}
.display{{font-family:'Anton',Impact,sans-serif;text-transform:uppercase;line-height:.94;letter-spacing:.004em;font-weight:400;}}
h1{{font-size:52px;margin-bottom:14px;}}
h2{{font-size:26px;margin:0 0 6px;}}
.kicker{{font-size:10px;letter-spacing:.26em;text-transform:uppercase;color:var(--dim);font-weight:700;}}
section{{margin-top:44px;padding-top:28px;border-top:1px solid var(--line);page-break-inside:auto;}}
section:first-of-type{{border-top:none;}}
p{{margin:0 0 12px;max-width:70ch;}}
.lede{{color:var(--dim);font-size:15px;}}
table{{width:100%;border-collapse:collapse;margin-top:16px;font-size:13px;}}
th{{text-align:left;font-size:10px;letter-spacing:.16em;text-transform:uppercase;color:var(--dim);
  border-bottom:1px solid var(--ink);padding:0 10px 6px 0;font-weight:700;}}
td{{padding:9px 10px 9px 0;border-bottom:1px solid var(--line);vertical-align:top;}}
td.num,th.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap;}}
tr{{page-break-inside:avoid;}}
.evidence{{color:var(--dim);font-size:12px;}}
.tag{{display:inline-block;font-size:10px;letter-spacing:.1em;text-transform:uppercase;
  border:1px solid var(--line);padding:2px 6px;white-space:nowrap;}}
.figs{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1px;background:var(--line);
  border:1px solid var(--line);margin-top:20px;}}
.fig{{background:#fff;padding:16px 14px;}}
.fig .v{{font-family:'Anton',Impact,sans-serif;font-size:26px;line-height:1;}}
.fig .k{{font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:var(--dim);margin-top:8px;}}
.note{{border-left:2px solid var(--ink);padding:12px 16px;margin-top:20px;background:#F6F6F2;}}
.note p{{margin:0;font-size:13px;color:var(--dim);}}
ol{{padding-left:20px;}} li{{margin-bottom:14px;page-break-inside:avoid;}}
li h3{{font-family:'Anton',Impact,sans-serif;text-transform:uppercase;font-size:16px;font-weight:400;margin-bottom:4px;}}
footer{{margin-top:56px;padding-top:20px;border-top:1px solid var(--line);color:var(--dim);font-size:11px;}}
@page{{margin:16mm;}}
@media print{{body{{font-size:11pt;}} .sheet{{padding:0;max-width:none;}} section{{page-break-inside:auto;}}}}
</style></head><body><div class="sheet">
<p class="kicker">MARK · Stage 0 · Production teardown</p>
<h1 class="display">{_e(client or 'Production')}<br>teardown</h1>
<p class="lede">{len(ledgers)} completed production(s) reviewed · prepared {_e(prepared_on)}</p>"""


def _scope(ledgers: list[dict], prepared_on: str, client: str) -> str:
    rows = "".join(
        f"<tr><td>{_e(l.get('production') or 'Unnamed production')}</td>"
        f"<td class='num'>{_money(l['totals']['budget'], l.get('currency','INR'))}</td>"
        f"<td class='num'>{_money(l['totals']['actual'], l.get('currency','INR'))}</td>"
        f"<td class='num'>{_money(l['totals']['delta'], l.get('currency','INR'))}</td>"
        f"<td class='num'>{_pct(l['totals'].get('delta_pct'))}</td>"
        f"<td class='num'>{l['totals']['material_lines']}</td></tr>"
        for l in ledgers)
    return f"""<section>
<p class="kicker">Scope</p><h2 class="display">What was examined</h2>
<p>Each production below was reconciled line by line: the approved budget against the final
cost report, at a materiality threshold of {ledgers[0].get('threshold', 0.1):.0%} if the first
production is representative. Every figure in this report traces to a line in the accompanying
variance ledger.</p>
<table><thead><tr><th>Production</th><th class="num">Budget</th><th class="num">Actual</th>
<th class="num">Variance</th><th class="num">%</th><th class="num">Material lines</th></tr></thead>
<tbody>{rows}</tbody></table>
<div class="note"><p>This is a diagnostic engagement, not an audit, and must not be represented
as one. Findings are evidenced against the documents supplied and no others.</p></div>
</section>"""


def _findings(ledgers: list[dict], currency: str, top: int) -> str:
    blocks = []
    for l in ledgers:
        cur = l.get("currency", currency)
        lines = l.get("material_lines", [])[:top]
        if not lines:
            blocks.append(f"<h3 class='display' style='font-size:18px;margin-top:24px'>"
                          f"{_e(l.get('production') or 'Production')}</h3>"
                          f"<p class='lede'>No line moved beyond the materiality threshold.</p>")
            continue
        rows = "".join(
            f"<tr><td>{_e(li.get('desc'))}<div class='evidence'>{_e(li.get('basis'))}</div></td>"
            f"<td><span class='tag'>{_e(_STATUS_LABEL.get(li.get('status'), li.get('status')))}</span></td>"
            f"<td class='num'>{_money(li.get('budget'), cur)}</td>"
            f"<td class='num'>{_money(li.get('actual'), cur)}</td>"
            f"<td class='num'>{_money(li.get('delta'), cur)}</td>"
            f"<td class='num'>{_pct(li.get('delta_pct'))}</td>"
            f"<td>{_e((li.get('classification') or '').replace('_', ' '))}</td></tr>"
            for li in lines)
        more = l["totals"]["material_lines"] - len(lines)
        tail = (f"<p class='evidence'>{more} further material line(s) in the ledger.</p>"
                if more > 0 else "")
        blocks.append(
            f"<h3 class='display' style='font-size:18px;margin-top:28px'>{_e(l.get('production') or 'Production')}</h3>"
            f"<table><thead><tr><th>Line and evidence</th><th>Status</th><th class='num'>Budget</th>"
            f"<th class='num'>Actual</th><th class='num'>Variance</th><th class='num'>%</th>"
            f"<th>Classification</th></tr></thead><tbody>{rows}</tbody></table>{tail}")
    return ("<section><p class='kicker'>D1 · Findings</p>"
            "<h2 class='display'>Where each production moved</h2>"
            "<p>Lines are ordered by the size of the variance, not by section. The evidence column "
            "states why each line was classified as it was; where the data could not support a "
            "split between scope and rate, it says so rather than choosing.</p>"
            + "".join(blocks) + "</section>")


def _recurring(patterns: list[dict], currency: str, n_ledgers: int) -> str:
    if n_ledgers < 2:
        return ("<section><p class='kicker'>D2 · Recurring variance</p>"
                "<h2 class='display'>Not assessable</h2>"
                "<p>A single production cannot show a recurring pattern. This section requires at "
                "least two completed productions.</p></section>")
    if not patterns:
        return ("<section><p class='kicker'>D2 · Recurring variance</p>"
                "<h2 class='display'>No consistent pattern</h2>"
                "<p>No line moved in the same direction across every production examined. That is a "
                "finding in its own right: the variance here is job-specific rather than systemic, "
                "and the estimating process is not the thing to change first.</p></section>")
    rows = "".join(
        f"<tr><td>{_e(p['desc'])}</td><td class='num'>{p['productions']}</td>"
        f"<td>{_e(p['direction'])}</td><td class='num'>{_pct(p['avg_delta_pct'])}</td>"
        f"<td class='num'>{_money(p['avg_delta'], currency)}</td>"
        f"<td>{_e(', '.join(c.replace('_', ' ') for c in p.get('classifications', [])))}</td></tr>"
        for p in patterns)
    return f"""<section><p class="kicker">D2 · Recurring variance</p>
<h2 class="display">The lines that are wrong every time</h2>
<p>These moved in the same direction on every production examined. One job's overrun is a story;
the same line wrong repeatedly is a process, and it is the only part of this report that
extrapolates.</p>
<table><thead><tr><th>Line</th><th class="num">Productions</th><th>Direction</th>
<th class="num">Average</th><th class="num">Average variance</th><th>Classified as</th></tr></thead>
<tbody>{rows}</tbody></table></section>"""


def _annualised(annualised: Optional[dict], currency: str) -> str:
    if not annualised or annualised.get("error"):
        return ("<section><p class='kicker'>D3 · Annualised cost</p>"
                "<h2 class='display'>Not computed</h2>"
                "<p>An annual figure needs the client's stated production volume and at least two "
                "completed productions. Neither is assumed here.</p></section>")
    a = annualised
    return f"""<section><p class="kicker">D3 · Annualised cost of the current process</p>
<h2 class="display">What the pattern costs across a year</h2>
<div class="figs">
<div class="fig"><div class="v">{_money(a.get('recurring_cost_per_production'), currency)}</div>
<div class="k">Recurring, per production</div></div>
<div class="fig"><div class="v">{_money(a.get('recurring_cost_per_year'), currency)}</div>
<div class="k">Recurring, per year</div></div>
<div class="fig"><div class="v">{_e(a.get('productions_per_year'))}</div>
<div class="k">Productions a year (stated)</div></div>
<div class="fig"><div class="v">{_e(a.get('sample_productions'))}</div>
<div class="k">Productions sampled</div></div>
</div>
<div class="note"><p><strong>Method.</strong> {_e(a.get('method'))}</p></div>
{"".join(f"<p class='evidence'>· {_e(c)}</p>" for c in a.get("caveats", []))}
</section>"""


def _remediation(patterns: list[dict], ledgers: list[dict]) -> str:
    """D4. Built from the classifications actually observed — no recommendation
    appears unless the data produced the finding behind it."""
    seen: dict[str, dict] = {}
    for p in patterns:
        for c in p.get("classifications", []):
            entry = seen.setdefault(c, {"lines": [], "total": 0.0})
            entry["lines"].append(p["desc"])
            entry["total"] += p.get("total_delta", 0) or 0
    if not seen:
        for l in ledgers:
            for li in l.get("material_lines", []):
                c = li.get("classification")
                if not c:
                    continue
                entry = seen.setdefault(c, {"lines": [], "total": 0.0})
                if li["desc"] not in entry["lines"]:
                    entry["lines"].append(li["desc"])
                entry["total"] += li.get("delta", 0) or 0

    if not seen:
        return ("<section><p class='kicker'>D4 · Remediation</p>"
                "<h2 class='display'>Nothing to recommend</h2>"
                "<p>No material variance was classified, so there is no evidenced change to "
                "propose.</p></section>")

    items = []
    for cls, entry in sorted(seen.items(), key=lambda kv: -abs(kv[1]["total"])):
        title, body = REMEDIATION.get(cls, (cls.replace("_", " ").title(), ""))
        lines = ", ".join(entry["lines"][:6])
        items.append(f"<li><h3>{_e(title)}</h3><p>{_e(body)}</p>"
                     f"<p class='evidence'>Observed on: {_e(lines)}</p></li>")
    return ("<section><p class='kicker'>D4 · Remediation</p>"
            "<h2 class='display'>What to change, in order</h2>"
            "<p>Ordered by the money attached to each finding. Each recommendation follows from a "
            "classification in D1 — nothing here is proposed on general principle.</p>"
            f"<ol>{''.join(items)}</ol></section>")


def _limitations(ledgers: list[dict]) -> str:
    bullets = []
    unmatched = sum(l.get("unmatched_actuals", 0) for l in ledgers)
    if unmatched:
        bullets.append(f"{unmatched} actual line(s) could not be matched to a budget line and are "
                       f"reported as unbudgeted spend. Some may be re-codings rather than new cost.")
    no_qty = sum(1 for l in ledgers for li in l.get("material_lines", [])
                 if li.get("classification") == "estimate_error" and not li.get("quantity"))
    if no_qty:
        bullets.append(f"{no_qty} line(s) carried no quantity basis on either side, so their variance "
                       f"could not be split between a scope change and a rate change.")
    if len(ledgers) < 3:
        bullets.append(f"{len(ledgers)} production(s) examined. The SOW's sample is three; findings "
                       f"from fewer are indicative.")
    bullets.append("Figures are taken from the documents supplied. No independent verification of "
                   "vendor invoices or payroll records was performed.")
    return ("<section><p class='kicker'>Limitations</p>"
            "<h2 class='display'>What this report cannot tell you</h2>"
            + "".join(f"<p>· {_e(b)}</p>" for b in bullets)
            + "</section><footer>MARK · THE MARIO JUDE LTD · 15842207 · "
              "Prepared for the client's internal use. Not an audit, and no liability is accepted "
              "by any third party relying on it.</footer></div>")
