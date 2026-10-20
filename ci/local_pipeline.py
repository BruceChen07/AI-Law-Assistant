from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class CommandSpec:
    label: str
    cmd: list[str]
    cwd: Path
    timeout: int = 1800
    env: dict[str, str] | None = None


@dataclass
class StageSpec:
    stage_id: str
    title: str
    commands: list[CommandSpec]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the local offline CI/CD pipeline.")
    parser.add_argument("action", choices=[
                        "run", "plan"], nargs="?", default="run")
    parser.add_argument("--profile", default="quick",
                        help="Pipeline profile name.")
    parser.add_argument("--trigger", default="manual",
                        help="Trigger source label.")
    parser.add_argument("--config", default="",
                        help="Path to pipeline config JSON.")
    parser.add_argument("--from-stage", default="",
                        help="Resume execution from the given stage id.")
    parser.add_argument("--only", default="",
                        help="Comma-separated stage ids to run.")
    parser.add_argument("--max-parallel", type=int, default=0,
                        help="Override max parallel workers.")
    parser.add_argument("--keep-going", action="store_true",
                        help="Continue after failures.")
    parser.add_argument("--verbose", action="store_true",
                        help="Print extra pipeline debug output.")
    return parser.parse_args()


def load_config(config_path: Path) -> dict[str, Any]:
    return json.loads(config_path.read_text(encoding="utf-8"))


def load_stage_groups(config: dict[str, Any]) -> dict[str, str]:
    reverse: dict[str, str] = {}
    for group_name, stage_ids in config.get("stage_groups", {}).items():
        for stage_id in stage_ids:
            reverse[str(stage_id)] = str(group_name)
    return reverse


def resolve_python(repo_root: Path) -> str:
    candidates = [
        repo_root / "app" / ".venv" / "Scripts" / "python.exe",
        repo_root / ".venv" / "Scripts" / "python.exe",
        Path(sys.executable),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate.resolve())
    raise FileNotFoundError("python executable not found")


def resolve_npm() -> str:
    for name in ("npm.cmd", "npm"):
        found = shutil.which(name)
        if found:
            return found
    raise FileNotFoundError("npm executable not found")


def resolve_env(repo_root: Path, defaults: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    for key, value in defaults.get("resource_env", {}).items():
        env[str(key)] = str(value).replace("{repo_root}", str(repo_root))
    return env


def build_stage_specs(
    repo_root: Path,
    config: dict[str, Any],
    env: dict[str, str],
    artifact_dir: Path,
) -> dict[str, StageSpec]:
    defaults = config["defaults"]
    python_exe = resolve_python(repo_root)
    npm_exe = resolve_npm()
    backend_port = int(defaults["backend_port"])
    frontend_port = int(defaults["frontend_port"])

    def cmd(label: str, argv: list[str], cwd: Path, timeout: int = 1800, extra_env: dict[str, str] | None = None) -> CommandSpec:
        merged_env = env.copy()
        if extra_env:
            merged_env.update(extra_env)
        return CommandSpec(label=label, cmd=argv, cwd=cwd, timeout=timeout, env=merged_env)

    frontend_env = {"VITE_API_BASE": f"http://127.0.0.1:{backend_port}"}

    core_backend_tests = [
        "tests/test_api.py",
        "tests/test_llm_router.py",
        "tests/test_llm_api_key_resolution.py",
        "tests/test_contract_audit_error_payload.py",
        "tests/test_contract_preview_assets.py",
        "tests/test_contracts_source_download.py",
        "tests/test_pdf_pipeline.py",
    ]
    quarantined_backend_tests = [
        "tests/test_audit_prompt.py",
        "tests/test_audit_tax.py",
        "tests/test_export_jobs.py",
        "tests/test_memory_pipeline_fallback.py",
        "tests/test_tax_audit_crud.py",
        "tests/test_tax_audit_pipeline.py",
        "tests/test_tax_contract_parser.py",
        "tests/test_tax_lifecycle.py",
        "tests/test_tax_matcher.py",
        "tests/test_tax_parser.py",
        "tests/test_tax_report.py",
        "tests/test_tax_risk.py",
    ]

    return {
        "workspace_probe": StageSpec(
            "workspace_probe",
            "Workspace Probe",
            [
                cmd("python-version", [python_exe,
                    "--version"], repo_root, 120),
                cmd("npm-version", [npm_exe, "--version"], repo_root, 120),
            ],
        ),
        "deps_lock": StageSpec(
            "deps_lock",
            "Dependency Sync",
            [
                cmd(
                    "pip-install-pip-tools",
                    [
                        python_exe,
                        "-m",
                        "pip",
                        "install",
                        "pip-tools>=7.4,<8",
                        "packaging>=24,<26",
                    ],
                    repo_root,
                    1200,
                ),
                cmd(
                    "pip-sync",
                    [
                        python_exe,
                        "-m",
                        "piptools",
                        "sync",
                        str((repo_root / "app" / "dev-requirements.txt").resolve()),
                    ],
                    repo_root,
                    2400,
                ),
                cmd(
                    "python-dependency-check",
                    [
                        python_exe,
                        str((repo_root / "ci" / "check_python_dependencies.py").resolve()),
                        "--repo-root",
                        str(repo_root),
                        "--python",
                        python_exe,
                    ],
                    repo_root,
                    1800,
                ),
                cmd("npm-ci", [npm_exe, "ci", "--prefer-offline"],
                    repo_root / "web", 2400),
            ],
        ),
        "python_dependency_check": StageSpec(
            "python_dependency_check",
            "Python Dependency Consistency Check",
            [
                cmd(
                    "python-dependency-check",
                    [
                        python_exe,
                        str((repo_root / "ci" / "check_python_dependencies.py").resolve()),
                        "--repo-root",
                        str(repo_root),
                        "--python",
                        python_exe,
                    ],
                    repo_root,
                    1800,
                )
            ],
        ),
        "frontend_lint": StageSpec(
            "frontend_lint",
            "Frontend Lint",
            [cmd("npm-lint", [npm_exe, "run", "lint"], repo_root / "web", 1800)],
        ),
        "frontend_typecheck": StageSpec(
            "frontend_typecheck",
            "Frontend Typecheck",
            [cmd("npm-typecheck", [npm_exe, "run", "typecheck"],
                 repo_root / "web", 1800)],
        ),
        "frontend_unit": StageSpec(
            "frontend_unit",
            "Frontend Unit Tests",
            [cmd("npm-test", [npm_exe, "run", "test"], repo_root / "web", 2400)],
        ),
        "frontend_build": StageSpec(
            "frontend_build",
            "Frontend Build",
            [cmd("npm-build", [npm_exe, "run", "build"],
                 repo_root / "web", 2400, frontend_env)],
        ),
        "backend_static": StageSpec(
            "backend_static",
            "Backend Static Check",
            [cmd("ruff-check", [python_exe, "-m", "ruff", "check",
                 "app", "tests", "bin", "ci"], repo_root, 1800)],
        ),
        "backend_security": StageSpec(
            "backend_security",
            "Backend Offline Security Scan",
            [
                cmd(
                    "offline-dependency-guard",
                    [
                        python_exe,
                        str((repo_root / "ci" / "offline_dependency_guard.py").resolve()),
                        "--requirements",
                        str((repo_root / "app" / "requirements.txt").resolve()),
                        "--baseline",
                        str((repo_root / "ci" / "security" /
                            "python-advisories.json").resolve()),
                    ],
                    repo_root,
                    600,
                )
            ],
        ),
        "backend_unit_core": StageSpec(
            "backend_unit_core",
            "Backend Core Tests",
            [cmd("pytest-core", [python_exe, "-m", "pytest",
                 *core_backend_tests], repo_root, 3600)],
        ),
        "backend_unit_full": StageSpec(
            "backend_unit_full",
            "Backend Full Tests",
            [
                cmd(
                    "pytest-full-stable",
                    [python_exe, "-m", "pytest", "tests", *
                        [f"--ignore={item}" for item in quarantined_backend_tests]],
                    repo_root,
                    7200,
                )
            ],
        ),
        "backend_package": StageSpec(
            "backend_package",
            "Backend Package",
            [
                cmd(
                    "compileall",
                    [python_exe, "-m", "compileall", "-x",
                        r".*(\\.venv|\\.mypy_cache|__pycache__).*", "app", "tests", "bin", "ci"],
                    repo_root,
                    2400,
                ),
                cmd(
                    "package-backend",
                    [
                        python_exe,
                        str((repo_root / "ci" / "package_backend.py").resolve()),
                        "--repo-root",
                        str(repo_root),
                        "--output-dir",
                        str((artifact_dir / "backend").resolve()),
                    ],
                    repo_root,
                    1200,
                ),
            ],
        ),
        "prerelease_smoke": StageSpec(
            "prerelease_smoke",
            "Pre-release Smoke Validation",
            [
                cmd(
                    "smoke-runtime",
                    [
                        python_exe,
                        str((repo_root / "ci" / "smoke_runtime.py").resolve()),
                        "--repo-root",
                        str(repo_root),
                        "--output-dir",
                        str((artifact_dir / "smoke").resolve()),
                        "--backend-port",
                        str(backend_port),
                        "--frontend-port",
                        str(frontend_port),
                        "--admin-username",
                        str(defaults["admin_username"]),
                        "--admin-password",
                        str(defaults["admin_password"]),
                    ],
                    repo_root,
                    2400,
                    frontend_env,
                )
            ],
        ),
    }


def filter_stage_ids(profile_ids: list[str], from_stage: str, only: str) -> list[str]:
    result = list(profile_ids)
    if from_stage:
        if from_stage not in result:
            raise KeyError(
                f"stage '{from_stage}' not found in selected profile")
        result = result[result.index(from_stage):]
    if only:
        allowed = [item.strip() for item in only.split(",") if item.strip()]
        result = [stage_id for stage_id in result if stage_id in allowed]
    return result


def build_execution_units(stage_ids: list[str], stage_group_map: dict[str, str]) -> list[list[str]]:
    units: list[list[str]] = []
    idx = 0
    while idx < len(stage_ids):
        current = stage_ids[idx]
        group_name = stage_group_map.get(current)
        if not group_name:
            units.append([current])
            idx += 1
            continue
        grouped = [current]
        idx += 1
        while idx < len(stage_ids) and stage_group_map.get(stage_ids[idx]) == group_name:
            grouped.append(stage_ids[idx])
            idx += 1
        units.append(grouped)
    return units


def run_command(spec: CommandSpec, log_file: Path) -> tuple[int, float]:
    started = time.time()
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(f"$ {' '.join(spec.cmd)}\n")
        handle.flush()
        process = subprocess.Popen(
            spec.cmd,
            cwd=str(spec.cwd),
            env=spec.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert process.stdout is not None
        try:
            for line in process.stdout:
                handle.write(line)
            process.wait(timeout=spec.timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            handle.write(
                f"[timeout] command exceeded {spec.timeout} seconds\n")
            return 124, time.time() - started
        return process.returncode or 0, time.time() - started


def run_stage(stage: StageSpec, log_dir: Path, verbose: bool) -> dict[str, Any]:
    stage_log = log_dir / f"{stage.stage_id}.log"
    if verbose:
        print(f"[stage] {stage.stage_id} -> {stage.title}")
    stage_started = time.time()
    results: list[dict[str, Any]] = []
    status = "passed"
    for command in stage.commands:
        code, duration = run_command(command, stage_log)
        results.append(
            {
                "label": command.label,
                "exit_code": code,
                "duration_seconds": round(duration, 2),
            }
        )
        if code != 0:
            status = "failed"
            break
    return {
        "stage_id": stage.stage_id,
        "title": stage.title,
        "status": status,
        "duration_seconds": round(time.time() - stage_started, 2),
        "log_file": str(stage_log),
        "commands": results,
    }


def write_summary(log_dir: Path, summary: dict[str, Any]) -> None:
    json_path = log_dir / "summary.json"
    md_path = log_dir / "summary.md"
    json_path.write_text(json.dumps(
        summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        f"# Local CI Summary: {summary['profile']}",
        "",
        f"- Trigger: {summary['trigger']}",
        f"- Status: {summary['status']}",
        f"- Started At: {summary['started_at']}",
        f"- Duration Seconds: {summary['duration_seconds']}",
        "",
        "| Stage | Status | Duration(s) | Log |",
        "| --- | --- | ---: | --- |",
    ]
    for stage in summary["stages"]:
        lines.append(
            f"| {stage['stage_id']} | {stage['status']} | {stage['duration_seconds']} | `{Path(stage['log_file']).name}` |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if summary["status"] != "passed":
        failure_lines = [
            f"Local pipeline failed for profile `{summary['profile']}`.",
            "",
            "Failed stages:",
        ]
        for stage in summary["stages"]:
            if stage["status"] == "failed":
                failure_lines.append(
                    f"- `{stage['stage_id']}` -> `{stage['log_file']}`")
        (log_dir / "failure-notice.md").write_text(
            "\n".join(failure_lines) + "\n",
            encoding="utf-8",
        )


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parent.parent
    config_path = Path(args.config).resolve(
    ) if args.config else repo_root / "ci" / "local-pipeline.config.json"
    config = load_config(config_path)
    defaults = config["defaults"]
    profile_ids = list(config["profiles"].get(args.profile, []))
    if not profile_ids:
        raise KeyError(f"profile '{args.profile}' not found")

    selected_stage_ids = filter_stage_ids(
        profile_ids, args.from_stage, args.only)
    stage_group_map = load_stage_groups(config)
    execution_units = build_execution_units(
        selected_stage_ids, stage_group_map)

    if args.action == "plan":
        print(
            json.dumps(
                {
                    "profile": args.profile,
                    "stages": selected_stage_ids,
                    "units": execution_units,
                    "toolchain": config.get("selected_toolchain"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    started_at = datetime.now().isoformat()
    log_dir = repo_root / config["log_root"] / f"{timestamp}-{args.profile}"
    artifact_dir = repo_root / \
        config["artifact_root"] / f"{timestamp}-{args.profile}"
    log_dir.mkdir(parents=True, exist_ok=True)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    env = resolve_env(repo_root, defaults)
    stage_specs = build_stage_specs(repo_root, config, env, artifact_dir)
    max_parallel = args.max_parallel or int(defaults.get("max_parallel", 1))

    lock = threading.Lock()
    results: list[dict[str, Any]] = []
    started = time.time()
    status = "passed"

    for unit in execution_units:
        run_in_parallel = len(unit) > 1 and max_parallel > 1
        unit_results: list[dict[str, Any]] = []
        if run_in_parallel:
            workers = min(max_parallel, len(unit))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                future_map = {
                    executor.submit(run_stage, stage_specs[stage_id], log_dir, args.verbose): stage_id
                    for stage_id in unit
                }
                for future in as_completed(future_map):
                    unit_results.append(future.result())
        else:
            for stage_id in unit:
                unit_results.append(
                    run_stage(stage_specs[stage_id], log_dir, args.verbose))

        unit_results.sort(
            key=lambda item: selected_stage_ids.index(item["stage_id"]))
        with lock:
            results.extend(unit_results)

        failed = [item for item in unit_results if item["status"] != "passed"]
        if failed:
            status = "failed"
            if not args.keep_going and defaults.get("fail_fast", True):
                break

    summary = {
        "toolchain": config.get("selected_toolchain"),
        "profile": args.profile,
        "trigger": args.trigger,
        "config": str(config_path),
        "status": status,
        "started_at": started_at,
        "duration_seconds": round(time.time() - started, 2),
        "log_dir": str(log_dir),
        "artifact_dir": str(artifact_dir),
        "stages": results,
    }
    write_summary(log_dir, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if status == "passed" else 2


if __name__ == "__main__":
    sys.exit(main())
