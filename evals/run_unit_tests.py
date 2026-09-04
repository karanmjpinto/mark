#!/usr/bin/env python3
"""
run_unit_tests.py — one entry point for every offline test in evals/.

    python3 evals/run_unit_tests.py

Discovers `test_*.py` alongside it and runs each module's entry point. No
pytest, no dependencies, no network — the same constraint the invariant suite
has always run under, so CI needs nothing but a Python interpreter.

Two conventions are supported, because the suites were written at different
times: a module may expose `_run()` returning a failure count (the pure-logic
suites) or `main()` returning an exit code (the invariant negative tests).
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "backend"))


def main() -> int:
    modules = sorted(p.stem for p in HERE.glob("test_*.py"))
    if not modules:
        print("No test modules found.")
        return 1

    print(f"\n Mark unit tests — {len(modules)} module(s)\n" + "─" * 60)
    failures = 0
    for name in modules:
        print(f"\n{name}")
        mod = importlib.import_module(name)
        if hasattr(mod, "_run"):
            failures += mod._run()
        elif hasattr(mod, "main"):
            failures += 1 if mod.main() else 0
        else:
            print(f"  ⚠️  {name} exposes neither _run() nor main() — skipped")

    print("\n" + "─" * 60)
    print(" all unit tests passed\n" if not failures else f" {failures} FAILING\n")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
