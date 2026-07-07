from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict


REPO_ROOT = Path(__file__).resolve().parents[1]
PID_FILE = REPO_ROOT / ".runtime" / "services.pids.json"


def log(message: str) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {message}", flush=True)


def load_pid_file(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig") as file_obj:
        return json.load(file_obj)


def is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                check=False,
            )
            output = str(result.stdout).strip()
            return bool(output and "No tasks are running" not in output and str(pid) in output)
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except Exception:
        return False


def stop_pid(pid: int) -> None:
    if pid <= 0:
        return
    if not is_pid_alive(pid):
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False)
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except Exception:
        return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="停止本地前后端服务。")
    parser.add_argument("--pid-file", default=str(PID_FILE), help="PID 文件路径。")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pid_file = Path(args.pid_file).resolve()
    data = load_pid_file(pid_file)
    backend_pid = int(data.get("backend_pid") or 0)
    frontend_pid = int(data.get("frontend_pid") or 0)

    if backend_pid <= 0 and frontend_pid <= 0:
        log("未找到可停止的服务 PID。")
        return 0

    if frontend_pid > 0:
        stop_pid(frontend_pid)
        log(f"[OK] 已请求停止前端进程: {frontend_pid}")
    if backend_pid > 0:
        stop_pid(backend_pid)
        log(f"[OK] 已请求停止后端进程: {backend_pid}")

    pid_file.unlink(missing_ok=True)
    log("[OK] 已清理 PID 文件。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
