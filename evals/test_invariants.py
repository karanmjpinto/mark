#!/usr/bin/env python3
"""
test_invariants.py — negative tests for the invariant suite.

The eval suite is only useful if the checks actually fail on bad budgets. This
takes the known-good NIKE fixture, mutates it to inject each real-world defect,
and asserts the corresponding check flips to FAIL. Run alongside run_evals.py in
CI. Pure stdlib — no API, no deps.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import invariants  # noqa: E402

GOOD = json.loads((Path(__file__).resolve().parent / "fixtures" / "nike_tvc_mumbai.json").read_text())


def _fails(budget: dict, expect: dict, check_name: str) -> bool:
    return any((not r.passed) and r.name == check_name for r in invariants.run_all(budget, expect))


def main() -> int:
    expect = GOOD["expect"]
    cases = []

    # 1. Baseline good budget must pass everything.
    base = GOOD["sample_output"]
    baseline_ok = all(r.passed for r in invariants.run_all(base, expect))
    cases.append(("baseline passes clean", baseline_ok))

    # 2. gst_rate = 18 (the raw-percent bug) must trip tax.gst_rate_is_decimal.
    b = copy.deepcopy(base)
    b["sections"][0]["items"][0]["gst_rate"] = 18
    cases.append(("gst=18 caught", _fails(b, expect, "tax.gst_rate_is_decimal")))

    # 3. Dropping Post Sound (13100) must trip sections.required_present.
    b = copy.deepcopy(base)
    b["sections"] = [s for s in b["sections"] if str(s.get("code")) != "13100"]
    cases.append(("missing 13100 caught", _fails(b, expect, "sections.required_present")))

    # 4. Zeroing out post must trip ratio.post_vs_production.
    b = copy.deepcopy(base)
    for s in b["sections"]:
        if s.get("type") == "post":
            for it in s["items"]:
                it["amount"] = 1
    cases.append(("underweight post caught", _fails(b, expect, "ratio.post_vs_production")))

    # 5. Invalid confidence marker must trip items.conf_valid.
    b = copy.deepcopy(base)
    b["sections"][0]["items"][0]["conf"] = "maybe"
    cases.append(("bad conf caught", _fails(b, expect, "items.conf_valid")))

    # 6. Total blown past the band must trip reconcile.total_in_band.
    b = copy.deepcopy(base)
    b["sections"][0]["items"][0]["amount"] = 999999999
    cases.append(("total out of band caught", _fails(b, expect, "reconcile.total_in_band")))

    print("\n Negative tests for invariants\n" + "─" * 40)
    ok = True
    for name, passed in cases:
        print(f"  {'✅' if passed else '❌'} {name}")
        ok = ok and passed
    print("─" * 40)
    print(" all negative tests passed\n" if ok else " SOME NEGATIVE TESTS FAILED\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
