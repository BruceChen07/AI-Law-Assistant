from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


REQ_PATTERN = re.compile(
    r"^\s*([A-Za-z0-9_.-]+)\s*(==|!=|>=|<=|~=|>|<)?\s*([^;\s#]+)?"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run an offline dependency policy scan against local advisory baselines."
    )
    parser.add_argument("--requirements", required=True, help="Path to requirements file.")
    parser.add_argument("--baseline", required=True, help="Path to offline advisory baseline JSON.")
    parser.add_argument("--strict", action="store_true", help="Fail on non-pinned packages as well.")
    return parser.parse_args()


def load_requirements(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or raw.startswith("-r "):
            continue
        match = REQ_PATTERN.match(raw)
        if not match:
            continue
        rows.append(
            {
                "name": match.group(1).lower(),
                "operator": match.group(2) or "",
                "version": match.group(3) or "",
                "raw": raw,
            }
        )
    return rows


def load_baseline(path: Path) -> list[dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    advisories = payload.get("advisories")
    return advisories if isinstance(advisories, list) else []


def version_matches(rule: dict[str, str], dependency: dict[str, str]) -> bool:
    package = str(rule.get("package") or "").strip().lower()
    version = str(rule.get("version") or "").strip()
    if not package or package != dependency["name"]:
        return False
    if not version:
        return True
    return version == dependency["version"]


def main() -> int:
    args = parse_args()
    requirements = load_requirements(Path(args.requirements))
    advisories = load_baseline(Path(args.baseline))

    failures: list[str] = []
    warnings: list[str] = []

    for dependency in requirements:
      if args.strict and dependency["operator"] != "==":
          warnings.append(f"non-pinned dependency: {dependency['raw']}")
      for advisory in advisories:
          if version_matches(advisory, dependency):
              failures.append(
                  f"blocked dependency: {dependency['raw']} | reason={advisory.get('reason', 'n/a')}"
              )

    report = {
        "requirements": len(requirements),
        "failures": failures,
        "warnings": warnings,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if failures:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
