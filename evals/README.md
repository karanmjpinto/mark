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

## Run

```bash
cd mark/evals

# Offline — validates the bundled sample_outputs. No API key. This is the CI gate.
python run_evals.py

# Negative tests — proves the checks catch real defects (gst=18, missing post, etc.)
python test_invariants.py

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
