from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO


READY_HTTP_TIMEOUT = 5
DEFAULT_STARTUP_TIMEOUT = 60
DEFAULT_STATUS_INTERVAL = 5
TAIL_LINE_LIMIT = 80
STRUCTURED_LOG_RE = re.compile(
    r"^(?:\d{4}-\d{2}-\d{2} [0-9:,.-]+\s*\|\s*(?:DEBUG|INFO|WARNING|ERROR|CRITICAL)\s*\||(?:DEBUG|INFO|WARNING|ERROR|CRITICAL):)"
)
ERROR_CLASS_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception)\b")


def configure_logger(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("service_launcher")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    file_handler = logging.FileHandler(
        log_dir / "launcher.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


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


def terminate_pid(pid: int | None) -> None:
    if not pid or not is_pid_alive(pid):
        return
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                   check=False, capture_output=True, text=True)


def probe_tcp_port(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def probe_http(url: str, timeout: int = READY_HTTP_TIMEOUT) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return True, f"http={resp.status}"
    except Exception as exc:
        return False, str(exc)


def write_pid_file(pid_file: Path, payload: dict) -> None:
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    with pid_file.open("w", encoding="utf-8") as file_obj:
        json.dump(payload, file_obj, ensure_ascii=False, indent=2)
        file_obj.write("\n")


def read_tail_text(path: Path, max_chars: int = 4000) -> str:
    if not path.exists():
        return ""
    data = path.read_text(encoding="utf-8", errors="ignore")
    if len(data) <= max_chars:
        return data
    return data[-max_chars:]


def infer_log_level(stream_name: str, line: str) -> int:
    text = str(line or "").upper()
    if "TRACEBACK" in text or " CRITICAL " in f" {text} " or text.startswith("CRITICAL"):
        return logging.ERROR
    if " ERROR " in f" {text} " or text.startswith("ERROR"):
        return logging.ERROR
    if ERROR_CLASS_RE.search(str(line or "")):
        return logging.ERROR
    if "EXCEPTION IN CALLBACK" in text or text.startswith("HANDLE: <HANDLE "):
        return logging.ERROR
    if " WARNING " in f" {text} " or " WARN " in f" {text} " or text.startswith("WARNING"):
        return logging.WARNING
    if stream_name == "stderr" and "USERWARNING" in text:
        return logging.WARNING
    return logging.INFO


def starts_error_block(line: str) -> bool:
    text = str(line or "")
    upper = text.upper()
    return (
        "TRACEBACK (MOST RECENT CALL LAST):" in upper
        or "DURING HANDLING OF THE ABOVE EXCEPTION" in upper
        or "EXCEPTION IN CALLBACK" in upper
        or text.startswith("handle: <Handle ")
    )


def is_structured_log_line(line: str) -> bool:
    return bool(STRUCTURED_LOG_RE.match(str(line or "")))


@dataclass
class ServiceProcess:
    name: str
    cmd: list[str]
    cwd: Path
    env: dict[str, str]
    port: int
    health_url: str
    log_path: Path
    process: subprocess.Popen | None = None
    status: str = "pending"
    ready: bool = False
    port_listening: bool = False
    last_probe_detail: str = ""
    tail_lines: deque[str] = field(
        default_factory=lambda: deque(maxlen=TAIL_LINE_LIMIT))
    log_lock: threading.Lock = field(default_factory=threading.Lock)

    def spawn(self, logger: logging.Logger) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.process = subprocess.Popen(
            self.cmd,
            cwd=str(self.cwd),
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self.status = "starting"
        logger.info(
            "[START] service=%s pid=%s port=%s cmd=%s",
            self.name,
            self.process.pid,
            self.port,
            subprocess.list2cmdline(self.cmd),
        )
        for stream_name, stream in (("stdout", self.process.stdout), ("stderr", self.process.stderr)):
            if stream is None:
                continue
            thread = threading.Thread(
                target=self._relay_stream,
                args=(logger, stream_name, stream),
                daemon=True,
                name=f"{self.name}-{stream_name}",
            )
            thread.start()

    def _relay_stream(self, logger: logging.Logger, stream_name: str, stream: TextIO) -> None:
        error_block_active = False
        for raw_line in iter(stream.readline, ""):
            line = raw_line.rstrip()
            if not line:
                continue
            entry = f"[{stream_name}] {line}"
            with self.log_lock:
                with self.log_path.open("a", encoding="utf-8") as file_obj:
                    file_obj.write(entry + "\n")
                self.tail_lines.append(entry)
            if error_block_active and is_structured_log_line(line):
                error_block_active = False

            if starts_error_block(line):
                error_block_active = True
                level = logging.ERROR
            elif error_block_active:
                level = logging.ERROR
            else:
                level = infer_log_level(stream_name, line)
            logger.log(level, "[%s] %s", self.name, line)
        stream.close()

    def poll(self) -> int | None:
        if self.process is None:
            return None
        return self.process.poll()

    def snapshot(self) -> dict:
        return {
            "service": self.name,
            "pid": self.process.pid if self.process else None,
            "status": self.status,
            "ready": self.ready,
            "port": self.port,
            "port_listening": self.port_listening,
            "returncode": self.poll(),
            "detail": self.last_probe_detail,
        }

    def recent_output(self) -> str:
        with self.log_lock:
            return "\n".join(self.tail_lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Start backend and frontend services with unified logging and status monitoring."
    )
    parser.add_argument("--backend-port", type=int,
                        default=8001, help="Backend service port.")
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
    parser.add_argument(
        "--startup-timeout",
        type=int,
        default=DEFAULT_STARTUP_TIMEOUT,
        help="Seconds to wait for both services to become ready.",
    )
    parser.add_argument(
        "--status-interval",
        type=int,
        default=DEFAULT_STATUS_INTERVAL,
        help="Seconds between runtime status reports.",
    )
    return parser.parse_args()


def wait_for_service_ready(service: ServiceProcess, timeout_sec: int, logger: logging.Logger) -> bool:
    deadline = time.time() + max(timeout_sec, 1)
    last_summary = ""
    while time.time() <= deadline:
        returncode = service.poll()
        if returncode is not None:
            service.status = "exited"
            service.last_probe_detail = f"exit={returncode}"
            logger.error(
                "[FAIL] service=%s exited during startup returncode=%s\n%s",
                service.name,
                returncode,
                service.recent_output(),
            )
            return False

        service.port_listening = probe_tcp_port("127.0.0.1", service.port)
        ready_ok, ready_detail = probe_http(
            service.health_url) if service.port_listening else (False, "port-not-listening")
        service.ready = ready_ok
        service.last_probe_detail = ready_detail
        if ready_ok:
            service.status = "running"
            logger.info(
                "[READY] service=%s pid=%s port=%s detail=%s",
                service.name,
                service.process.pid if service.process else "unknown",
                service.port,
                ready_detail,
            )
            return True

        service.status = "starting"
        summary = f"port_listening={service.port_listening} detail={ready_detail}"
        if summary != last_summary:
            logger.info("[WAIT] service=%s pid=%s %s", service.name,
                        service.process.pid if service.process else "unknown", summary)
            last_summary = summary
        time.sleep(1)

    service.status = "timeout"
    logger.error(
        "[FAIL] service=%s did not become ready within %ss. detail=%s\n%s",
        service.name,
        timeout_sec,
        service.last_probe_detail,
        service.recent_output(),
    )
    return False


def monitor_services(services: list[ServiceProcess], status_interval: int, logger: logging.Logger) -> int:
    last_report_at = 0.0
    while True:
        now = time.time()
        if now - last_report_at >= max(status_interval, 1):
            for service in services:
                service.port_listening = probe_tcp_port(
                    "127.0.0.1", service.port)
                ready_ok, ready_detail = probe_http(
                    service.health_url) if service.port_listening else (False, "port-not-listening")
                service.ready = ready_ok
                service.last_probe_detail = ready_detail
                if service.poll() is None and service.ready:
                    service.status = "running"
                elif service.poll() is None:
                    service.status = "degraded"
                else:
                    service.status = "exited"
                logger.info(
                    "[STATUS] service=%s pid=%s state=%s port=%s listening=%s ready=%s detail=%s",
                    service.name,
                    service.process.pid if service.process else "unknown",
                    service.status,
                    service.port,
                    service.port_listening,
                    service.ready,
                    service.last_probe_detail,
                )
            last_report_at = now

        for service in services:
            returncode = service.poll()
            if returncode is not None:
                service.status = "exited"
                logger.error(
                    "[FAIL] service=%s exited unexpectedly returncode=%s\n%s",
                    service.name,
                    returncode,
                    service.recent_output(),
                )
                return 1
        time.sleep(1)


def cleanup_processes(services: list[ServiceProcess], logger: logging.Logger) -> None:
    for service in reversed(services):
        pid = service.process.pid if service.process else None
        if not pid:
            continue
        terminate_pid(pid)
        logger.info("[STOP] service=%s pid=%s", service.name, pid)


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    web_root = repo_root / "web"
    runtime_dir = repo_root / ".runtime"
    pid_file = runtime_dir / "services.pids.json"
    dated_log_dir = repo_root / "logs" / "services" / \
        datetime.now().strftime("%Y-%m-%d")
    logger = configure_logger(dated_log_dir)
    existing = load_pid_file(pid_file)

    if existing:
        alive = []
        for pid in (existing.get("backend_pid"), existing.get("frontend_pid")):
            if is_pid_alive(pid):
                alive.append(pid)
        if alive:
            logger.error("Services seem already running. Stop them first. Alive PIDs: %s", ", ".join(
                str(pid) for pid in alive))
            return 1
        pid_file.unlink(missing_ok=True)

    backend_python = args.backend_python or find_backend_python(repo_root)
    if not backend_python:
        logger.error(
            "Virtual environment python not found. Checked app\\.venv\\Scripts\\python.exe and current Python.")
        return 1

    npm_exe = args.npm_exe or find_frontend_command()
    if not npm_exe:
        logger.error(
            "npm not found in PATH. Please install Node.js or pass --npm-exe.")
        return 1

    runtime_dir.mkdir(parents=True, exist_ok=True)
    backend_env = os.environ.copy()
    backend_env["APP_PORT"] = str(args.backend_port)
    backend_env["PYTHONUNBUFFERED"] = "1"
    backend_env.setdefault("PYTHONIOENCODING", "utf-8")
    frontend_env = os.environ.copy()
    frontend_env["VITE_API_BASE"] = frontend_env.get(
        "VITE_API_BASE", f"http://127.0.0.1:{args.backend_port}")
    logger.info("frontend_api_base=%s", frontend_env["VITE_API_BASE"])

    services = [
        ServiceProcess(
            name="backend",
            cmd=[backend_python, "-m", "app.main"],
            cwd=repo_root,
            env=backend_env,
            port=args.backend_port,
            health_url=f"http://127.0.0.1:{args.backend_port}/health",
            log_path=dated_log_dir / "backend.log",
        ),
        ServiceProcess(
            name="frontend",
            cmd=[npm_exe, "run", "dev", "--", "--host",
                 "0.0.0.0", "--port", str(args.frontend_port)],
            cwd=web_root,
            env=frontend_env,
            port=args.frontend_port,
            health_url=f"http://127.0.0.1:{args.frontend_port}",
            log_path=dated_log_dir / "frontend.log",
        ),
    ]

    try:
        for service in services:
            service.spawn(logger)

        write_pid_file(
            pid_file,
            {
                "launcher_pid": os.getpid(),
                "backend_pid": services[0].process.pid if services[0].process else None,
                "frontend_pid": services[1].process.pid if services[1].process else None,
                "backend_port": args.backend_port,
                "frontend_port": args.frontend_port,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "log_dir": str(dated_log_dir),
                "launcher_log": str(dated_log_dir / "launcher.log"),
                "backend_log": str(services[0].log_path),
                "frontend_log": str(services[1].log_path),
            },
        )
        logger.info("PID file written: %s", pid_file)

        for service in services:
            ok = wait_for_service_ready(service, args.startup_timeout, logger)
            if not ok:
                raise RuntimeError(f"{service.name} failed to become ready")

        logger.info(
            "All services are ready. backend=http://127.0.0.1:%s frontend=http://127.0.0.1:%s",
            args.backend_port,
            args.frontend_port,
        )
        logger.info("Logs are persisted under: %s", dated_log_dir)
        logger.info("Press Ctrl+C to stop all services.")

        return monitor_services(services, args.status_interval, logger)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received. Stopping child services.")
        return 0
    except Exception as exc:
        logger.error("[FAIL] launcher exception: %s", exc)
        logger.error("%s", traceback.format_exc())
        for service in services:
            if service.process and service.recent_output():
                logger.error("[TAIL] service=%s\n%s",
                             service.name, service.recent_output())
        return 1
    finally:
        cleanup_processes(services, logger)
        pid_file.unlink(missing_ok=True)
        logger.info("Launcher shutdown complete. PID file removed.")


if __name__ == "__main__":
    sys.exit(main())
