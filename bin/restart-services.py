from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
PID_FILE = REPO_ROOT / ".runtime" / "services.pids.json"
START_SCRIPT = REPO_ROOT / "bin" / "start-services.py"
DEFAULT_BACKEND_PORT = 8000
DEFAULT_FRONTEND_PORT = 5173
DEFAULT_WAIT_TIMEOUT = 30


def log(message: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts} | INFO | {message}", flush=True)


def load_pid_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8-sig") as file_obj:
            return json.load(file_obj)
    except json.JSONDecodeError:
        return {}


def is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return False
    output = str(result.stdout).strip()
    return bool(output and "No tasks are running" not in output and str(pid) in output)


def terminate_pid(pid: int) -> None:
    if pid <= 0 or not is_pid_alive(pid):
        return
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        text=True,
        check=False,
    )


def probe_tcp_port(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_shutdown(pids: list[int], ports: list[int], timeout_sec: int) -> bool:
    deadline = time.time() + max(timeout_sec, 1)
    while time.time() <= deadline:
        alive_pids = [pid for pid in pids if is_pid_alive(pid)]
        busy_ports = [port for port in ports if probe_tcp_port("127.0.0.1", port)]
        if not alive_pids and not busy_ports:
            return True
        time.sleep(1)
    return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Restart backend and frontend services without manual stop."
    )
    parser.add_argument("--backend-port", type=int, default=0, help="Backend port override.")
    parser.add_argument("--frontend-port", type=int, default=0, help="Frontend port override.")
    parser.add_argument("--backend-python", default="", help="Pass through to start-services.py.")
    parser.add_argument("--npm-exe", default="", help="Pass through to start-services.py.")
    parser.add_argument("--startup-timeout", type=int, default=0, help="Pass through to start-services.py.")
    parser.add_argument("--status-interval", type=int, default=0, help="Pass through to start-services.py.")
    parser.add_argument(
        "--shutdown-timeout",
        type=int,
        default=DEFAULT_WAIT_TIMEOUT,
        help="Seconds to wait for old services to stop.",
    )
    return parser.parse_args()


def build_start_command(args: argparse.Namespace, backend_port: int, frontend_port: int) -> list[str]:
    command = [sys.executable, str(START_SCRIPT)]
    command.extend(["--backend-port", str(backend_port)])
    command.extend(["--frontend-port", str(frontend_port)])
    if args.backend_python:
        command.extend(["--backend-python", args.backend_python])
    if args.npm_exe:
        command.extend(["--npm-exe", args.npm_exe])
    if args.startup_timeout > 0:
        command.extend(["--startup-timeout", str(args.startup_timeout)])
    if args.status_interval > 0:
        command.extend(["--status-interval", str(args.status_interval)])
    return command


def main() -> int:
    args = parse_args()
    existing = load_pid_file(PID_FILE)

    backend_port = int(args.backend_port or existing.get("backend_port") or DEFAULT_BACKEND_PORT)
    frontend_port = int(args.frontend_port or existing.get("frontend_port") or DEFAULT_FRONTEND_PORT)

    launcher_pid = int(existing.get("launcher_pid") or 0)
    backend_pid = int(existing.get("backend_pid") or 0)
    frontend_pid = int(existing.get("frontend_pid") or 0)
    tracked_pids = [pid for pid in [launcher_pid, frontend_pid, backend_pid] if pid > 0]

    if tracked_pids:
        log(
            "Stopping existing services automatically. "
            f"launcher_pid={launcher_pid or '-'} backend_pid={backend_pid or '-'} frontend_pid={frontend_pid or '-'}"
        )
        if launcher_pid > 0:
            terminate_pid(launcher_pid)
        else:
            if frontend_pid > 0:
                terminate_pid(frontend_pid)
            if backend_pid > 0:
                terminate_pid(backend_pid)

        if launcher_pid > 0:
            if frontend_pid > 0:
                terminate_pid(frontend_pid)
            if backend_pid > 0:
                terminate_pid(backend_pid)

        stopped = wait_for_shutdown(
            tracked_pids,
            [backend_port, frontend_port],
            args.shutdown_timeout,
        )
        if not stopped:
            alive_pids = [pid for pid in tracked_pids if is_pid_alive(pid)]
            busy_ports = [port for port in [backend_port, frontend_port] if probe_tcp_port("127.0.0.1", port)]
            log(
                "Old services did not stop completely within timeout. "
                f"alive_pids={alive_pids or '-'} busy_ports={busy_ports or '-'}"
            )
            return 1
        PID_FILE.unlink(missing_ok=True)
        log("Previous services stopped successfully.")
    else:
        if PID_FILE.exists():
            PID_FILE.unlink(missing_ok=True)
        log("No existing PID record found. Starting services directly.")

    start_command = build_start_command(args, backend_port, frontend_port)
    log(f"Starting services with command: {subprocess.list2cmdline(start_command)}")
    completed = subprocess.run(start_command, cwd=str(REPO_ROOT), check=False)
    return int(completed.returncode)


if __name__ == "__main__":
    sys.exit(main())
