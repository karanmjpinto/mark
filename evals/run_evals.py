#!/usr/bin/env python3
"""
run_evals.py — run budget invariants against golden fixtures.

Two modes:

  Offline (default) — validates each fixture's bundled `sample_output`. No
  network, no API key, deterministic. This is what runs in CI on every push:
  it proves the invariant suite itself is correct and guards the fixtures.

      python run_evals.py

  Live — sends each fixture's `input` to a running backend's /budget/generate
  and validates the REAL agent output. This is what you run after a prompt or
  rate-card change to catch regressions before producers do.

      python run_evals.py --live --api https://your-backend --key $API_KEY

Exit code is non-zero if any invariant fails, so it drops straight into CI.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import invariants  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def load_fixtures() -> list[dict]:
    fixtures = []
    for path in sorted(FIXTURES_DIR.glob("*.json")):
        with open(path) as f:
            fx = json.load(f)
            fx["_file"] = path.name
            fixtures.append(fx)
    return fixtures


def fetch_live_budget(api: str, key: str | None, payload: dict) -> dict:
    """POST a fixture input to /budget/generate and return the budget_data."""
    body = json.dumps({
        "script": payload.get("script", ""),
        "region": payload.get("region", "india"),
        "currency": payload.get("currency"),
        "qa": payload.get("qa", []),
        "breakdown": payload.get("breakdown"),
    }).encode()
    req = urllib.request.Request(f"{api.rstrip('/')}/budget/generate", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("X-API-Key", key)
    with urllib.request.urlopen(req, timeout=240) as resp:
        data = json.loads(resp.read())
    return (data.get("budget") or {}).get("budget_data") or {}


def main() -> int:
    ap = argparse.ArgumentParser(description="Run Mark budget evals.")
    ap.add_argument("--live", action="store_true", help="hit a running backend instead of using sample_output")
    ap.add_argument("--api", default=os.getenv("MARK_API_BASE", "http://localhost:8000"))
    ap.add_argument("--key", default=os.getenv("MARK_API_KEY"))
    args = ap.parse_args()

    fixtures = load_fixtures()
    if not fixtures:
        print("No fixtures found.")
        return 1

    mode = "LIVE" if args.live else "OFFLINE (sample_output)"
    print(f"\n Mark evals — {mode} — {len(fixtures)} fixture(s)\n" + "─" * 60)

    total_pass = total_fail = 0
    failed_fixtures = []

    for fx in fixtures:
        name = fx.get("name", fx["_file"])
        try:
            if args.live:
                budget = fetch_live_budget(args.api, args.key, fx["input"])
            else:
                budget = fx["sample_output"]
        except (urllib.error.URLError, urllib.error.HTTPError, KeyError) as e:
            print(f"\n{name}\n  ⚠️  could not obtain budget: {e}")
            failed_fixtures.append(name)
            total_fail += 1
            continue

        results = invariants.run_all(budget, fx.get("expect"))
        n_pass = sum(1 for r in results if r.passed)
        n_fail = len(results) - n_pass
        total_pass += n_pass
        total_fail += n_fail
        status = "PASS" if n_fail == 0 else "FAIL"
        print(f"\n{name}  [{status}]  {n_pass}/{len(results)} checks")
        for r in results:
            if not r.passed:
                print(f"  {r.icon} {r.name}: {r.detail}")
        if n_fail == 0:
            print("  ✅ all checks passed")
        else:
            failed_fixtures.append(name)

    print("\n" + "─" * 60)
    print(f" {total_pass} passed · {total_fail} failed")
    if failed_fixtures:
        print(f" Failing fixtures: {', '.join(failed_fixtures)}")
    print()
    return 1 if total_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
