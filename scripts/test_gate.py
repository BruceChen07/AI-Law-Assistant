#!/usr/bin/env python3
"""Test gate: compare the current pytest failure set against a recorded baseline.

The project does not currently have a green suite, so "all tests pass" is not a
usable gate. Instead we freeze the exact set of failing test IDs and require that
a refactor introduces **no new failures**.

Usage:
    # record / refresh the baseline
    app/.venv/Scripts/python.exe scripts/test_gate.py --save
    # verify after a change (exit 1 if a new failure appeared)
    app/.venv/Scripts/python.exe scripts/test_gate.py --check
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / ".refactor-baseline" / "pytest_baseline.json"
PY = ROOT / "app" / ".venv" / "Scripts" / "python.exe"
if not PY.exists():
    PY = ROOT / "app" / ".venv" / "bin" / "python"

FAILED_RE = re.compile(r"^FAILED\s+(\S+)", re.M)
SUMMARY_RE = re.compile(r"^(?:=+\s*)?(\d+) failed,\s*(\d+) passed", re.M)


def run_pytest() -> tuple[set[str], str]:
    proc = subprocess.run(
        [str(PY), "-m", "pytest", "tests", "-q", "--tb=no", "--no-header",
         "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    out = proc.stdout + proc.stderr
    failed = set(FAILED_RE.findall(out))
    tail = "\n".join(out.strip().splitlines()[-3:])
    return failed, tail


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true", help="record current failures as baseline")
    ap.add_argument("--check", action="store_true", help="compare against baseline")
    args = ap.parse_args()

    print("running pytest tests ...", flush=True)
    failed, tail = run_pytest()
    print(f"summary: {tail}")
    print(f"failing tests: {len(failed)}")

    if args.save:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(
            json.dumps({"failing": sorted(failed), "summary": tail}, indent=2, ensure_ascii=False),
            encoding="utf-8")
        print(f"baseline saved -> {BASELINE}")
        for t in sorted(failed):
            print(f"  {t}")
        return

    if args.check:
        if not BASELINE.exists():
            print("no baseline; run with --save first", file=sys.stderr)
            sys.exit(2)
        base = json.loads(BASELINE.read_text(encoding="utf-8"))
        known = set(base["failing"])
        new = sorted(failed - known)
        fixed = sorted(known - failed)
        print(f"\nbaseline failures: {len(known)}")
        if fixed:
            print(f"\n[+] newly PASSING ({len(fixed)}):")
            for t in fixed:
                print(f"    {t}")
        if new:
            print(f"\n[!] NEW FAILURES ({len(new)}) - regression:")
            for t in new:
                print(f"    {t}")
            print("\nGATE FAILED")
            sys.exit(1)
        print("\nGATE PASSED - no new failures")
        return

    for t in sorted(failed):
        print(f"  {t}")


if __name__ == "__main__":
    main()
