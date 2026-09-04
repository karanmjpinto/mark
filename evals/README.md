# Mark budget evals

A regression gate for the budget agent. *"A change to its instructions can break
it as surely as a change to its code"* — this catches those breaks before a
producer does. It's the promotion of the in-prod `_post_ratio_hint` guardrail
into a real, versioned suite.

## What's here

| File | Purpose |
|---|---|
| `invariants.py` | Pure checks true of **any** valid budget (gst is a decimal, required post sections present, post:production ratio sane, totals reconcile, valid confidence markers). No network. |
| `fixtures/*.json` | Golden cases: an `input` (script + QA + breakdown), an `expect` block of bounds, and a bundled `sample_output`. |
| `run_evals.py` | Runs the invariants. Offline by default; `--live` hits a real backend. |
| `test_invariants.py` | Negative tests — mutates a good budget to prove each check actually fails on the defect it targets. |
| `test_screenplay.py` | Parser output → breakdown: scene fields, cast attribution, CONTINUOUS inheriting the previous scene's time, the scene cap. |
| `test_ratecard.py` | The rate library: seeds are never verified, a named city is never substituted, corrections blend rather than overwrite. |
| `test_schedule.py` | The scheduler's craft rules: location contiguity, day-before-night, a binding day count, hold days in the DooD. |
| `test_variance.py` | The teardown: unbudgeted spend is never absorbed, no classification without a basis, nothing recurring from one production. |
| `test_compliance.py` | India GST/TDS: deduction on the pre-GST value, s.206AA without a PAN, blocked input credit, disclaimer always present. |
| `test_budgetdiff.py` | Version-to-version change: no line matched twice, totals reconcile against the line deltas, a reworded line is not hidden. |
| `test_exporters.py` | Interop: every part of the .xlsx package is well-formed, hostile characters are escaped, numbers round-trip, and export→import returns the same total. |
| `test_teardown_report.py` | The client document: findings carry evidence, nothing is extrapolated from one production, and it never calls itself an audit. |
| `test_delivery.py` | Call-sheet delivery: state never moves backwards, a late webhook cannot un-confirm someone, one crew member's link cannot confirm another. |
| `test_roster.py` | Crew and vendor history: Indian phone formats are the same handset, a shorter name never overwrites a fuller one, no rate is proposed from one job. |
| `run_unit_tests.py` | Runs every `test_*.py` above in one command. |

## Run

```bash
cd mark/evals

# Offline — validates the bundled sample_outputs. No API key. This is the CI gate.
python run_evals.py

# Negative tests — proves the checks catch real defects (gst=18, missing post, etc.)
python test_invariants.py

# Every offline unit suite (rate library, scheduler, variance ledger, GST/TDS).
# Imports the backend modules directly — which is why none of them import
# FastAPI at module level, and why this needs no dependencies at all.
python run_unit_tests.py

# Live — sends each fixture's input to a running backend and validates the REAL
# agent output. Run this after any prompt or rate-card change.
python run_evals.py --live --api https://your-backend --key $MARK_API_KEY
```

Exit code is non-zero on any failure, so it drops straight into CI
(`.github/workflows/evals.yml`).

## Adding a fixture

1. Capture a real `input` (the `/budget/generate` request body: `script`,
   `region`, `currency`, `qa`, `breakdown`).
2. Add an `expect` block — at minimum `require_sections`, `post_ratio_min/max`,
   and a `total_min/total_max` band for the scale.
3. Paste a known-good `sample_output` (a budget you've reviewed and trust).
   Offline mode validates against it; live mode ignores it.

The more fixtures span your real range (TVC / MV / feature, low / mid / high,
india / uk / usa), the more of the prompt surface the gate protects.
