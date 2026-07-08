import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def find_backend_python(repo_root: Path) -> str | None:
    candidates = [
        repo_root / "app" / ".venv" / "Scripts" / "python.exe",
        repo_root / ".venv" / "Scripts" / "python.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate.resolve())

    current = Path(sys.executable)
    if current.exists():
        return str(current.resolve())
    return None


def find_frontend_command() -> str | None:
    for candidate in ("npm.cmd", "npm"):
        found = shutil.which(candidate)
        if found:
            return found
    return None


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


def start_process(
    cmd: list[str],
    cwd: Path,
    env: dict[str, str],
    log_path: Path,
) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("a", encoding="utf-8")
    creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    return subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Start backend and frontend services without PowerShell."
    )
    parser.add_argument("--backend-port", type=int,
                        default=8000, help="Backend service port.")
    parser.add_argument("--frontend-port", type=int,
                        default=5173, help="Frontend dev server port.")
    parser.add_argument(
        "--backend-python",
        default="",
        help="Explicit python executable for backend. Defaults to app/.venv/Scripts/python.exe or current Python.",
    )
    parser.add_argument(
        "--npm-exe",
        default="",
        help="Explicit npm executable for frontend. Defaults to npm.cmd or npm in PATH.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    web_root = repo_root / "web"
    runtime_dir = repo_root / ".runtime"
    pid_file = runtime_dir / "services.pids.json"
    log_dir = runtime_dir / "logs"

    runtime_dir.mkdir(parents=True, exist_ok=True)

    existing = load_pid_file(pid_file)
    if existing:
        alive = []
        for pid in (existing.get("backend_pid"), existing.get("frontend_pid")):
            if is_pid_alive(pid):
                alive.append(pid)
        if alive:
            print(
                f"Services seem running. Stop them first. Alive PIDs: {', '.join(str(pid) for pid in alive)}")
            return 1
        pid_file.unlink(missing_ok=True)

    backend_python = args.backend_python or find_backend_python(repo_root)
    if not backend_python:
        print("Virtual environment python not found.")
        print(r"Checked defaults such as app\.venv\Scripts\python.exe and current Python.")
        print(r"Please create one, for example: python -m venv app\.venv")
        return 1

    npm_exe = args.npm_exe or find_frontend_command()
    if not npm_exe:
        print("npm not found in PATH. Please install Node.js or pass --npm-exe.")
        return 1

    backend_env = os.environ.copy()
    backend_env["APP_PORT"] = str(args.backend_port)
    frontend_env = os.environ.copy()

    backend_cmd = [backend_python, "-m", "app.main"]
    frontend_cmd = [
        npm_exe,
        "run",
        "dev",
        "--",
        "--host",
        "0.0.0.0",
        "--port",
        str(args.frontend_port),
    ]

    backend_log = log_dir / "backend.log"
    frontend_log = log_dir / "frontend.log"

    print(f"[START] Backend on http://localhost:{args.backend_port}")
    backend_proc = start_process(
        backend_cmd, repo_root, backend_env, backend_log)
    print(f"  PID: {backend_proc.pid}")
    print(f"  Log: {backend_log}")

    time.sleep(0.8)

    print(f"[START] Frontend on http://localhost:{args.frontend_port}")
    frontend_proc = start_process(
        frontend_cmd, web_root, frontend_env, frontend_log)
    print(f"  PID: {frontend_proc.pid}")
    print(f"  Log: {frontend_log}")

    data = {
        "backend_pid": backend_proc.pid,
        "frontend_pid": frontend_proc.pid,
        "backend_port": args.backend_port,
        "frontend_port": args.frontend_port,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "backend_log": str(backend_log),
        "frontend_log": str(frontend_log),
    }
    with pid_file.open("w", encoding="utf-8") as file_obj:
        json.dump(data, file_obj, ensure_ascii=False, indent=2)
        file_obj.write("\n")

    print()
    print(
        f"Started backend PID: {backend_proc.pid} at http://localhost:{args.backend_port}")
    print(
        f"Started frontend PID: {frontend_proc.pid} at http://localhost:{args.frontend_port}")
    print(f"PID file: {pid_file}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
