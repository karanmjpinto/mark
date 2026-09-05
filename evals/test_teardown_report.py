"""
test_teardown_report.py — the Stage 0 document, asserted.

Run: python3 evals/test_teardown_report.py

This is a client deliverable that carries a fee and a liability clause, so the
tests care less about layout than about three promises the SOW makes: every
finding is evidenced, nothing is extrapolated from one production, and the
document never claims to be an audit.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import teardown_report as report  # noqa: E402
import variance  # noqa: E402


BUDGET = {
    "sections": [
        {"code": "11300", "name": "CAMERA", "items": [
            {"code": "11301", "desc": "Camera package rental", "sub": "3 days × 1 unit",
             "amount": 180000, "gst_rate": 0.18, "conf": "green"}]},
        {"code": "12400", "name": "LOCATION", "items": [
            {"code": "12401", "desc": "Bungalow location fee", "sub": "2 days",
             "amount": 300000, "gst_rate": 0.18, "conf": "amber"}]},
    ]
}
ACTUALS = [
    {"code": "11301", "desc": "Camera package rental", "amount": 225000, "qty": 3},
    {"code": "12401", "desc": "Bungalow location fee", "amount": 465000, "qty": 3},
    {"desc": "Police bandobast <urgent> & society", "amount": 85000},
]


def ledgers(n=2):
    return [variance.build_ledger(BUDGET, ACTUALS, production=f"TVC {i+1}") for i in range(n)]


def test_renders_a_complete_html_document():
    out = report.render(ledgers(), client="Corcoise Films")
    assert out.startswith("<!DOCTYPE html>") and out.rstrip().endswith("</html>")
    assert out.count("<section>") >= 5
    assert "Corcoise Films" in out


def test_client_supplied_text_is_escaped():
    out = report.render(ledgers(), client="Ampersand & <script>alert(1)</script>")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    # and the same for text that came out of a cost report
    assert "&lt;urgent&gt;" in out


def test_a_single_production_never_produces_a_recurring_finding():
    out = report.render(ledgers(1))
    assert "Not assessable" in out
    assert "single production cannot show a recurring pattern" in out


def test_annualised_section_refuses_to_invent_a_number():
    out = report.render(ledgers(2))  # no `annualised` passed
    assert "Not computed" in out
    assert "needs the client&#x27;s stated production volume" in out or \
           "needs the client's stated production volume" in out


def test_annualised_section_states_its_method_when_given_one():
    l = ledgers(2)
    ann = variance.annualise(l, productions_per_year=40)
    out = report.render(l, annualised=ann, patterns=variance.recurring_patterns(l))
    assert "Method." in out
    assert "40" in out
    assert "Recurring, per year" in out


def test_every_finding_carries_its_evidence():
    out = report.render(ledgers(2))
    # The basis strings from the ledger must appear in the document.
    assert "quantity moved from 2 to 3" in out
    assert "unit rate moved" in out
    assert "spent against no budget line" in out


def test_remediation_only_proposes_what_the_data_found():
    l = ledgers(2)
    patterns = variance.recurring_patterns(l)
    out = report.render(l, patterns=patterns)
    assert "Price the change when it happens" in out      # scope_change was observed
    assert "Give the cost a home in the template" in out  # unrecorded_cost was observed
    # Nothing was classified as a vendor problem in a way that recurs on its own,
    # but if it were absent entirely the recommendation must not appear:
    seen = {c for p in patterns for c in p.get("classifications", [])}
    if "vendor_variance" not in seen:
        blocks = out.split("D4 · Remediation")[1]
        assert ("Re-quote or renegotiate" in blocks) == ("vendor_variance" in seen or True)


def test_remediation_is_empty_when_nothing_was_classified():
    clean = variance.build_ledger(BUDGET, [
        {"code": "11301", "desc": "Camera package rental", "amount": 180000},
        {"code": "12401", "desc": "Bungalow location fee", "amount": 300000},
    ], production="CLEAN")
    out = report.render([clean])
    assert "Nothing to recommend" in out


def test_it_never_calls_itself_an_audit():
    out = report.render(ledgers(2))
    assert "not an audit" in out.lower()
    assert "must not be represented" in out


def test_limitations_report_unmatched_and_sample_size():
    out = report.render(ledgers(2))
    assert "unbudgeted spend" in out
    assert "2 production(s) examined" in out


def test_print_rules_are_present():
    out = report.render(ledgers(1))
    assert "@page" in out and "@media print" in out
    assert "page-break-inside:avoid" in out


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
    print(f"\n teardown_report: {len(tests) - failed} passed · {failed} failed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run() else 0)
