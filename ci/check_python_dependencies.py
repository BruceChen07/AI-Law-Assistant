from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from importlib.metadata import distributions
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify Python lock files and the active environment stay in sync."
    )
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--python", default=sys.executable)
    return parser.parse_args()


def canon(name: str) -> str:
    return str(name or "").replace("_", "-").lower()


def read_lock_requirements(path: Path) -> dict[str, Requirement]:
    result: dict[str, Requirement] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        if raw.startswith("-") or raw.startswith("--"):
            continue
        if raw.startswith("pip ") or raw.startswith("setuptools "):
            continue
        req = Requirement(raw)
        result[canon(req.name)] = req
    return result


def active_requirements(requirements: dict[str, Requirement]) -> dict[str, Requirement]:
    env = default_environment()
    active: dict[str, Requirement] = {}
    for name, req in requirements.items():
        if req.marker is not None and not req.marker.evaluate(env):
            continue
        active[name] = req
    return active


def requirement_signature(req: Requirement) -> str:
    extras = ",".join(sorted(req.extras))
    marker = f"; {req.marker}" if req.marker else ""
    extra_text = f"[{extras}]" if extras else ""
    return f"{canon(req.name)}{extra_text}{req.specifier}{marker}"


def compile_to_map(
    python_exe: str,
    source: Path,
    output_name: str,
) -> dict[str, Requirement]:
    with tempfile.TemporaryDirectory(prefix="dep-lock-check-") as temp_dir:
        temp_output = Path(temp_dir) / output_name
        command = [
            python_exe,
            "-m",
            "piptools",
            "compile",
            "--quiet",
            "--resolver=backtracking",
            "--output-file",
            str(temp_output),
            str(source),
        ]
        subprocess.run(command, check=True)
        return read_lock_requirements(temp_output)


def installed_versions() -> dict[str, str]:
    rows: dict[str, str] = {}
    for item in distributions():
        name = item.metadata.get("Name")
        if not name:
            continue
        rows[canon(name)] = item.version
    return rows


def compare_lock_regeneration(
    repo_root: Path,
    python_exe: str,
) -> tuple[list[str], dict[str, list[str]]]:
    prod_source = repo_root / "app" / "requirements.in"
    prod_lock = repo_root / "app" / "requirements.txt"

    prod_expected = active_requirements(read_lock_requirements(prod_lock))
    prod_actual = active_requirements(compile_to_map(python_exe, prod_source, "requirements.txt"))

    errors: list[str] = []
    details = {
        "production_only_in_lock": [],
        "production_only_in_compiled": [],
    }

    for name in sorted(set(prod_expected) | set(prod_actual)):
        expected = prod_expected.get(name)
        actual = prod_actual.get(name)
        if expected and actual and requirement_signature(expected) == requirement_signature(actual):
            continue
        if expected:
            details["production_only_in_lock"].append(requirement_signature(expected))
        if actual:
            details["production_only_in_compiled"].append(requirement_signature(actual))

    if any(details.values()):
        errors.append("lock files are out of date with .in sources")
    return errors, details


def compare_environment(
    repo_root: Path,
) -> tuple[list[str], dict[str, list[str]]]:
    prod_lock = active_requirements(read_lock_requirements(repo_root / "app" / "requirements.txt"))
    dev_lock = active_requirements(read_lock_requirements(repo_root / "app" / "dev-requirements.txt"))
    expected = {}
    expected.update(prod_lock)
    expected.update(dev_lock)

    installed = installed_versions()
    ignored = {"pip", "setuptools"}

    missing: list[str] = []
    mismatched: list[str] = []
    extras: list[str] = []

    for name, req in sorted(expected.items()):
        version = installed.get(name)
        if version is None:
            missing.append(requirement_signature(req))
            continue
        if not req.specifier.contains(version, prereleases=True):
            mismatched.append(f"{name} installed={version} expected={req.specifier}")

    expected_names = set(expected)
    for name, version in sorted(installed.items()):
        if name in ignored:
            continue
        if name not in expected_names:
            extras.append(f"{name}=={version}")

    errors: list[str] = []
    if missing or mismatched or extras:
        errors.append("active environment does not match locked dependencies")

    return errors, {
        "missing": missing,
        "mismatched": mismatched,
        "extras": extras,
    }


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    python_exe = str(Path(args.python).resolve())

    lock_errors, lock_details = compare_lock_regeneration(repo_root, python_exe)
    env_errors, env_details = compare_environment(repo_root)

    report = {
        "repo_root": str(repo_root),
        "python": python_exe,
        "status": "passed" if not (lock_errors or env_errors) else "failed",
        "errors": [*lock_errors, *env_errors],
        "lock_regeneration": lock_details,
        "environment": env_details,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
