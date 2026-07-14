import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from deploy_desktop_lib import (
    LOG_DIR,
    ROOT,
    RUNTIME_DIR,
    DeploymentTransaction,
    DeployError,
    build_backend_install_stamp,
    build_frontend_install_stamp,
    command_exists,
    detect_environment,
    detect_llamacpp_build_profile,
    ensure_ascii_workspace,
    http_ok,
    is_port_open,
    load_json,
    load_stamp,
    run_command,
    save_json,
    save_stamp,
    setup_logger,
    stamp_matches,
    tail_text_file,
    utc_now,
    version_gte,
    wait_http_ok,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="One-shot desktop deployment entrypoint for AI Law Assistant."
    )
    parser.add_argument(
        "--manifest-path",
        default=str((ROOT / "deploy" / "desktop-deployment.manifest.json").resolve()),
        help="Desktop deployment manifest path.",
    )
    parser.add_argument("--backend-port", type=int, default=8000, help="Backend port.")
    parser.add_argument("--frontend-port", type=int, default=5173, help="Frontend port.")
    parser.add_argument("--main-port", type=int, default=18080, help="Main llama.cpp port.")
    parser.add_argument("--small-port", type=int, default=18081, help="Small llama.cpp port.")
    parser.add_argument("--monitor-seconds", type=int, default=300, help="Availability monitor duration after startup.")
    parser.add_argument("--skip-download", action="store_true", help="Skip asset download step.")
    parser.add_argument("--skip-frontend", action="store_true", help="Skip frontend install and startup.")
    parser.add_argument("--skip-backend-install", action="store_true", help="Skip backend dependency installation.")
    parser.add_argument("--skip-frontend-install", action="store_true", help="Skip frontend dependency installation.")
    parser.add_argument("--source-dir", default="", help="Optional official llama.cpp source checkout used for git commit verification.")
    parser.add_argument("--force-reinstall", action="store_true", help="Force reinstall backend/frontend dependencies even if stamp matches.")
    return parser.parse_args()


def _log_name(prefix: str) -> Path:
    stamp = utc_now().replace(":", "").replace("+00:00", "Z")
    return LOG_DIR / f"{prefix}-{stamp}.log"


def _check_minimum_tooling(env_info: Dict[str, Any], skip_frontend: bool, logger: Optional[logging.Logger] = None) -> None:
    tooling = env_info.get("tooling") or {}
    if not version_gte(str(tooling.get("python") or ""), "3.10"):
        raise DeployError(f"python 3.10+ required, current={tooling.get('python')}")
    git_version = str(tooling.get("git") or "")
    if not git_version or not version_gte(git_version, "2.40"):
        raise DeployError(f"git 2.40+ required, current={git_version or '<missing>'}")
    cmake_version = str(tooling.get("cmake") or "")
    if not cmake_version or not version_gte(cmake_version, "3.20"):
        msg = f"cmake 3.20+ not found (current={cmake_version or '<missing>'}). " \
              f"Source build of llama.cpp will be unavailable, but pre-built binary can still be used."
        if logger:
            logger.warning(msg)
        else:
            logging.warning(msg)
    if not skip_frontend:
        node_version = str(tooling.get("node") or "")
        if not node_version or not version_gte(node_version, "22.0"):
            raise DeployError(f"node 22+ required, current={node_version or '<missing>'}")
        npm_version = str(tooling.get("npm") or "")
        if not npm_version:
            raise DeployError("npm is required for frontend deployment")


def _create_backend_venv(venv_dir: Path, logger, tx: DeploymentTransaction) -> Path:
    python_exe = Path(sys.executable).resolve()
    venv_python = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if venv_python.exists():
        return venv_python
    run_command([str(python_exe), "-m", "venv", str(venv_dir)], logger=logger)
    tx.add_rollback(f"remove venv {venv_dir}", lambda: shutil.rmtree(venv_dir, ignore_errors=True))
    if not venv_python.exists():
        raise DeployError(f"venv python not found after creation: {venv_python}")
    return venv_python


def _install_backend_deps(venv_python: Path, logger, force_reinstall: bool) -> None:
    requirements_path = ROOT / "app" / "requirements.txt"
    stamp_path = RUNTIME_DIR / "backend-deps.stamp.json"
    expected = build_backend_install_stamp(requirements_path, venv_python)
    if not force_reinstall and stamp_matches(load_stamp(stamp_path), expected):
        logger.info("backend dependency installation skipped by stamp")
        return
    run_command([str(venv_python), "-m", "pip", "install", "--upgrade", "pip"], logger=logger)
    run_command([str(venv_python), "-m", "pip", "install", "-r", str(requirements_path)], logger=logger)
    save_stamp(stamp_path, expected)


def _install_frontend_deps(logger, force_reinstall: bool) -> None:
    npm_exe = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm_exe:
        raise DeployError("npm not found in PATH")
    lock_path = ROOT / "web" / "package-lock.json"
    stamp_path = RUNTIME_DIR / "frontend-deps.stamp.json"
    expected = build_frontend_install_stamp(lock_path, npm_exe)
    if not force_reinstall and stamp_matches(load_stamp(stamp_path), expected):
        logger.info("frontend dependency installation skipped by stamp")
        return
    run_command([npm_exe, "install"], cwd=ROOT / "web", logger=logger)
    save_stamp(stamp_path, expected)


def _run_init(venv_python: Path, logger) -> None:
    run_command([str(venv_python), str(ROOT / "bin" / "init.py")], cwd=ROOT, logger=logger)


def _run_script(python_exe: Path, script_path: Path, args: list[str], logger) -> None:
    run_command([str(python_exe), str(script_path), *args], cwd=ROOT, logger=logger)


def _start_background_process(cmd: list[str], cwd: Path, env: Dict[str, str], log_path: Path, logger) -> subprocess.Popen:
    ensure_dir = log_path.parent.mkdir
    ensure_dir(parents=True, exist_ok=True)
    log_handle = log_path.open("a", encoding="utf-8")
    creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    logger.info("start_background cwd=%s cmd=%s log=%s", cwd, " ".join(cmd), log_path)
    return subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )


def _safe_stop_process(proc: Optional[subprocess.Popen], logger) -> None:
    if not proc:
        return
    try:
        if proc.poll() is None:
            logger.info("terminate process pid=%s", proc.pid)
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
    except Exception as exc:
        logger.warning("failed to stop process pid=%s err=%s", getattr(proc, "pid", "?"), exc)


def _start_llama_stack(python_exe: Path, args: argparse.Namespace, logger) -> None:
    _run_script(
        python_exe,
        ROOT / "bin" / "start-local-llamacpp-stack.py",
        [
            "--main-port",
            str(args.main_port),
            "--small-port",
            str(args.small_port),
        ],
        logger,
    )
    if not wait_http_ok(f"http://127.0.0.1:{args.main_port}/v1/models", 240):
        raise DeployError("main llama.cpp endpoint did not become ready")
    if not wait_http_ok(f"http://127.0.0.1:{args.small_port}/v1/models", 240):
        raise DeployError("small llama.cpp endpoint did not become ready")


def _apply_local_config(python_exe: Path, args: argparse.Namespace, logger) -> None:
    _run_script(
        python_exe,
        ROOT / "bin" / "apply-local-llm-config.py",
        [
            "--provider",
            "llama_cpp",
            "--small-provider",
            "llama_cpp",
            "--llama-cpp-host",
            f"http://127.0.0.1:{args.main_port}",
            "--small-llama-cpp-host",
            f"http://127.0.0.1:{args.small_port}",
        ],
        logger,
    )


def _start_backend(venv_python: Path, port: int, logger) -> subprocess.Popen:
    env = os.environ.copy()
    env["APP_PORT"] = str(port)
    env["APP_PORT_AUTO_SWITCH"] = "0"
    return _start_background_process(
        [str(venv_python), "-m", "app.main"],
        cwd=ROOT,
        env=env,
        log_path=LOG_DIR / "backend-runtime.log",
        logger=logger,
    )


def _start_frontend(port: int, logger) -> subprocess.Popen:
    npm_exe = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm_exe:
        raise DeployError("npm not found in PATH")
    env = os.environ.copy()
    return _start_background_process(
        [npm_exe, "run", "dev", "--", "--host", "0.0.0.0", "--port", str(port)],
        cwd=ROOT / "web",
        env=env,
        log_path=LOG_DIR / "frontend-runtime.log",
        logger=logger,
    )


def _monitor_services(
    *,
    args: argparse.Namespace,
    python_exe: Path,
    venv_python: Path,
    backend_proc: subprocess.Popen,
    frontend_proc: Optional[subprocess.Popen],
    logger,
) -> Dict[str, Any]:
    deadline = time.time() + max(30, args.monitor_seconds)
    restart_used = False
    checks = []

    while time.time() <= deadline:
        state = {
            "timestamp": utc_now(),
            "llama_main_ok": http_ok(f"http://127.0.0.1:{args.main_port}/v1/models", timeout=5),
            "llama_small_ok": http_ok(f"http://127.0.0.1:{args.small_port}/v1/models", timeout=5),
            "backend_port_ok": is_port_open("127.0.0.1", args.backend_port),
            "backend_health_ok": http_ok(f"http://127.0.0.1:{args.backend_port}/health", timeout=5),
            "backend_pid_alive": backend_proc.poll() is None,
            "frontend_pid_alive": True if args.skip_frontend or not frontend_proc else frontend_proc.poll() is None,
        }
        if not args.skip_frontend:
            state["frontend_port_ok"] = is_port_open("127.0.0.1", args.frontend_port)
        checks.append(state)

        all_ok = bool(
            state["llama_main_ok"]
            and state["llama_small_ok"]
            and state["backend_port_ok"]
            and state["backend_health_ok"]
            and state["backend_pid_alive"]
            and state["frontend_pid_alive"]
            and (True if args.skip_frontend else state.get("frontend_port_ok"))
        )
        if all_ok:
            time.sleep(10)
            continue

        if restart_used:
            return {"ok": False, "restart_used": True, "checks": checks}

        logger.warning("availability check failed, attempting one restart")
        restart_used = True
        if not state["llama_main_ok"] or not state["llama_small_ok"]:
            _start_llama_stack(python_exe, args, logger)
        if not state["backend_pid_alive"] or not state["backend_health_ok"]:
            _safe_stop_process(backend_proc, logger)
            backend_proc = _start_backend(venv_python, args.backend_port, logger)
            if not wait_http_ok(f"http://127.0.0.1:{args.backend_port}/health", 60):
                logger.warning("backend did not recover after restart attempt")
        if not args.skip_frontend and frontend_proc and (not state["frontend_pid_alive"] or not state.get("frontend_port_ok")):
            _safe_stop_process(frontend_proc, logger)
            frontend_proc = _start_frontend(args.frontend_port, logger)
        time.sleep(10)

    return {"ok": True, "restart_used": restart_used, "checks": checks}


def main() -> int:
    args = parse_args()
    ensure_ascii_workspace(ROOT)
    log_file = _log_name("desktop-deploy")
    logger = setup_logger("deploy_desktop", log_file)
    tx = DeploymentTransaction(logger)
    manifest_path = Path(args.manifest_path).resolve()
    manifest = load_json(manifest_path)

    backend_proc: Optional[subprocess.Popen] = None
    frontend_proc: Optional[subprocess.Popen] = None

    try:
        env_info = detect_environment()
        build_profile = detect_llamacpp_build_profile(env_info)
        _check_minimum_tooling(env_info, args.skip_frontend, logger)
        logger.info("environment detected: %s", json.dumps(env_info, ensure_ascii=False))
        logger.info("recommended llama.cpp build profile: %s", json.dumps(build_profile, ensure_ascii=False))

        python_exe = Path(sys.executable).resolve()
        venv_dir = ROOT / "app" / ".venv"
        venv_python = _create_backend_venv(venv_dir, logger, tx)

        if not args.skip_backend_install:
            _install_backend_deps(venv_python, logger, args.force_reinstall)
        if not args.skip_frontend and not args.skip_frontend_install:
            _install_frontend_deps(logger, args.force_reinstall)

        _run_init(venv_python, logger)

        if not args.skip_download:
            _run_script(
                python_exe,
                ROOT / "bin" / "download_deployment_assets.py",
                ["--manifest-path", str(manifest_path), "--asset-kind", "all"],
                logger,
            )

        _run_script(
            python_exe,
            ROOT / "bin" / "verify_deployment_assets.py",
            ["--manifest-path", str(manifest_path), "--asset-kind", "all"],
            logger,
        )

        verify_args = ["--manifest-path", str(manifest_path)]
        if args.source_dir:
            verify_args.extend(["--source-dir", args.source_dir])
        _run_script(python_exe, ROOT / "bin" / "verify_llamacpp_bundle.py", verify_args, logger)

        _apply_local_config(python_exe, args, logger)
        _start_llama_stack(python_exe, args, logger)

        backend_proc = _start_backend(venv_python, args.backend_port, logger)
        tx.add_rollback("stop backend process", lambda: _safe_stop_process(backend_proc, logger))
        if not wait_http_ok(f"http://127.0.0.1:{args.backend_port}/health", 120):
            raise DeployError("backend /health did not become ready")

        if not args.skip_frontend:
            frontend_proc = _start_frontend(args.frontend_port, logger)
            tx.add_rollback("stop frontend process", lambda: _safe_stop_process(frontend_proc, logger))
            deadline = time.time() + 60
            while time.time() <= deadline:
                if is_port_open("127.0.0.1", args.frontend_port):
                    break
                time.sleep(2)

        monitor_report = _monitor_services(
            args=args,
            python_exe=python_exe,
            venv_python=venv_python,
            backend_proc=backend_proc,
            frontend_proc=frontend_proc,
            logger=logger,
        )

        report = {
            "generated_at": utc_now(),
            "manifest_path": str(manifest_path),
            "environment": env_info,
            "recommended_build_profile": build_profile,
            "ports": {
                "llama_main": args.main_port,
                "llama_small": args.small_port,
                "backend": args.backend_port,
                "frontend": None if args.skip_frontend else args.frontend_port,
            },
            "monitor": monitor_report,
            "logs": {
                "deploy": str(log_file),
                "backend": str(LOG_DIR / "backend-runtime.log"),
                "frontend": "" if args.skip_frontend else str(LOG_DIR / "frontend-runtime.log"),
            },
            "ok": bool(monitor_report.get("ok")),
        }
        report_path = RUNTIME_DIR / "desktop-deploy-report.json"
        save_json(report_path, report)
        logger.info("deployment report saved: %s", report_path)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1
    except Exception as exc:
        logger.exception("desktop deployment failed: %s", exc)
        failure_report = {
            "generated_at": utc_now(),
            "error": str(exc),
            "logs": {
                "deploy": str(log_file),
                "backend_tail": tail_text_file(LOG_DIR / "backend-runtime.log"),
                "frontend_tail": tail_text_file(LOG_DIR / "frontend-runtime.log"),
            },
        }
        save_json(RUNTIME_DIR / "desktop-deploy-failure-report.json", failure_report)
        tx.rollback()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
