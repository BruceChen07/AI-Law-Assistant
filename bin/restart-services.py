"""Restart backend, frontend and (optionally) llama.cpp services.

Stops the processes tracked in .runtime/services.pids.json, waits for the
service ports to be released, then delegates startup to start-services.py.

Options that this script does not recognise are forwarded verbatim to
start-services.py, for example:

    python bin/restart-services.py --skip-llamacpp
    python bin/restart-services.py --backend-port 8001 --frontend-port 5174

Usage:
    python bin/restart-services.py
    python bin/restart-services.py --stop-timeout 30 --kill-by-port
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_BACKEND_PORT = 8000
DEFAULT_FRONTEND_PORT = 5173
DEFAULT_LLAMACPP_PORT = 18080


# ---------------------------------------------------------------------------
# Process / port helpers
# ---------------------------------------------------------------------------


def load_pid_file(pid_file: Path) -> dict | None:
    if not pid_file.exists():
        return None
    try:
        with pid_file.open("r", encoding="utf-8-sig") as file_obj:
            return json.load(file_obj)
    except json.JSONDecodeError:
        print(f"Invalid PID file detected, ignoring it: {pid_file}")
        return None


def is_pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    try:
        process = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=True,
        )
    except Exception:
        return False

    output = process.stdout.strip()
    if not output:
        return False
    return "No tasks are running" not in output and str(pid) in output


def terminate_pid(pid: int) -> bool:
    """Force-kill a process tree. Returns True if the process was alive."""
    if not is_pid_alive(pid):
        return False
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
        )
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return False
    return True


def port_in_use(host: str, port: int, timeout: float = 0.5) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        try:
            return sock.connect_ex((host, port)) == 0
        except OSError:
            return False


def listening_pids(port: int) -> set[int]:
    """Return PIDs listening on the given port (Windows only)."""
    if os.name != "nt":
        return set()
    try:
        process = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            check=True,
        )
    except Exception:
        return set()

    pids: set[int] = set()
    for line in process.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[3].upper() != "LISTENING":
            continue
        if parts[1].rsplit(":", 1)[-1] != str(port):
            continue
        try:
            pids.add(int(parts[4]))
        except ValueError:
            continue
    return pids


# ---------------------------------------------------------------------------
# Stop / restart flow
# ---------------------------------------------------------------------------


def stop_services(
    pid_file: Path,
    ports: list[int],
    stop_timeout: float,
    kill_by_port: bool,
) -> None:
    data = load_pid_file(pid_file)
    if not data:
        print(f"[INFO] PID file not found or invalid, nothing tracked to stop: {pid_file}")
    else:
        targets = [
            data.get("frontend_pid"),
            data.get("backend_pid"),
            data.get("llamacpp_pid"),
        ]
        stopped = []
        for pid in targets:
            if pid and terminate_pid(int(pid)):
                stopped.append(int(pid))
                print(f"[STOP] Killed PID {pid}")
        if not stopped:
            print("[INFO] No tracked service process is alive.")

        deadline = time.time() + stop_timeout
        while time.time() < deadline:
            alive = [int(pid) for pid in targets if pid and is_pid_alive(int(pid))]
            if not alive:
                break
            time.sleep(0.5)

        alive = [int(pid) for pid in targets if pid and is_pid_alive(int(pid))]
        if alive:
            print(
                f"[WARN] Processes still alive after {stop_timeout:.0f}s: "
                f"{', '.join(str(pid) for pid in alive)}"
            )

        pid_file.unlink(missing_ok=True)
        print("[OK] Removed PID file.")

    busy = [port for port in ports if port_in_use("127.0.0.1", port)]
    if not busy:
        return

    for port in busy:
        pids = listening_pids(port)
        if not pids:
            print(f"[WARN] Port {port} is still in use (owner PID unknown).")
            continue
        if not kill_by_port:
            print(
                f"[WARN] Port {port} is still in use by PID(s) "
                f"{', '.join(str(pid) for pid in sorted(pids))}. "
                "Re-run with --kill-by-port to force-release it."
            )
            continue
        for pid in sorted(pids):
            if pid == os.getpid():
                continue
            if terminate_pid(pid):
                print(f"[STOP] Killed PID {pid} occupying port {port}")

    deadline = time.time() + stop_timeout
    while time.time() < deadline:
        busy = [port for port in ports if port_in_use("127.0.0.1", port)]
        if not busy:
            print("[OK] Service ports released.")
            return
        time.sleep(0.5)

    busy = [port for port in ports if port_in_use("127.0.0.1", port)]
    if busy:
        print(
            f"[WARN] Port(s) still in use: {', '.join(str(port) for port in busy)}. "
            "Startup may fail."
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def forwarded_int(forwarded: list[str], name: str, default: int) -> int:
    """Read an int option out of the arguments forwarded to start-services.py."""
    for idx, token in enumerate(forwarded):
        if token == name and idx + 1 < len(forwarded):
            try:
                return int(forwarded[idx + 1])
            except ValueError:
                return default
        if token.startswith(f"{name}="):
            try:
                return int(token.split("=", 1)[1])
            except ValueError:
                return default
    return default


def parse_args() -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(
        description="Restart backend, frontend and llama.cpp services.",
        epilog=(
            "All options not listed here are forwarded to start-services.py, "
            "e.g. --skip-llamacpp or --frontend-port 5174."
        ),
    )
    parser.add_argument(
        "--stop-timeout",
        type=float,
        default=20.0,
        help="Seconds to wait for processes and ports to be released (default: 20).",
    )
    parser.add_argument(
        "--kill-by-port",
        action="store_true",
        help="Force-kill leftover processes still listening on the service ports.",
    )
    return parser.parse_known_args()


def main() -> int:
    args, forwarded = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    pid_file = repo_root / ".runtime" / "services.pids.json"
    start_script = repo_root / "bin" / "start-services.py"

    if not start_script.exists():
        print(f"start-services.py not found: {start_script}")
        return 1

    ports = [
        forwarded_int(forwarded, "--backend-port", DEFAULT_BACKEND_PORT),
        forwarded_int(forwarded, "--frontend-port", DEFAULT_FRONTEND_PORT),
        forwarded_int(forwarded, "--llamacpp-port", DEFAULT_LLAMACPP_PORT),
    ]

    print(f"[INFO] Stopping services (PID file: {pid_file})")
    stop_services(pid_file, ports, args.stop_timeout, args.kill_by_port)

    print("\n[INFO] Starting services via start-services.py")
    return subprocess.call(
        [sys.executable, str(start_script), *forwarded],
        cwd=str(repo_root),
    )


if __name__ == "__main__":
    sys.exit(main())
