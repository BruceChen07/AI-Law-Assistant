import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# llama.cpp helpers (same logic as start-local-llamacpp-server.py)
# ---------------------------------------------------------------------------


def _scan_gguf_models(model_dir: Path) -> list[Path]:
    """Scan model_dir for *.gguf files, sorted by size descending."""
    if not model_dir.is_dir():
        return []
    return sorted(
        [p for p in model_dir.iterdir() if p.suffix.lower() == ".gguf"],
        key=lambda p: p.stat().st_size,
        reverse=True,
    )


def _llamacpp_ready(host: str, port: int) -> bool:
    try:
        url = f"http://{host}:{port}/v1/models"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def _select_model_interactive(candidates: list[Path], label: str) -> Path | None:
    """Prompt the user to select a model. Returns None if user skips."""
    print(f"\n{label}")
    for idx, p in enumerate(candidates, start=1):
        size_mb = p.stat().st_size / (1024 * 1024)
        print(f"  [{idx}] {p.name}  ({size_mb:.0f} MB)")
    print(f"  [0] Skip (do not start llama.cpp)")
    while True:
        try:
            choice = input(f"Select [0-{len(candidates)}]: ").strip()
            idx = int(choice)
            if 0 <= idx <= len(candidates):
                return candidates[idx - 1] if idx > 0 else None
        except ValueError:
            pass
        print(f"Please enter a number between 0 and {len(candidates)}.")


def _find_llamacpp_executable(repo_root: Path) -> str | None:
    candidates = [
        "llama-server.exe",
        "llama-server",
        str(repo_root / "third_party" / "llama.cpp" /
            "bin-win-cpu-x64" / "llama-server.exe"),
        str(repo_root / "third_party" / "llama.cpp" /
            "bin-win-cpu-x64" / "llama-server"),
        str(repo_root / "third_party" / "llama.cpp" /
            "build" / "bin" / "Release" / "llama-server.exe"),
        str(repo_root / "third_party" / "llama.cpp" /
            "build" / "bin" / "llama-server.exe"),
        str(repo_root / "third_party" / "llama.cpp" /
            "build" / "bin" / "llama-server"),
    ]
    for item in candidates:
        found = shutil.which(item)
        if found:
            return found
        if Path(item).exists():
            return str(Path(item).resolve())
    return None


def _is_llamacpp_provider(cfg: dict) -> bool:
    llm = cfg.get("llm_config") or {}
    return str(llm.get("provider", "")).strip().lower() == "llama_cpp"


def _read_config(repo_root: Path) -> dict | None:
    config_path = os.environ.get(
        "APP_CONFIG", str(repo_root / "app" / "config.json"))
    if not os.path.exists(config_path):
        return None
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Existing service helpers
# ---------------------------------------------------------------------------


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
    creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    return subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        creationflags=creationflags,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Start llama.cpp, backend, and frontend services in one command."
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
    parser.add_argument(
        "--skip-llamacpp",
        action="store_true",
        help="Skip starting llama.cpp server (use if already running or using Ollama).",
    )
    parser.add_argument(
        "--llamacpp-host",
        default="127.0.0.1",
        help="llama.cpp server host (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--llamacpp-port",
        type=int,
        default=18080,
        help="llama.cpp server port (default: 18080).",
    )
    parser.add_argument(
        "--llamacpp-threads",
        type=int,
        default=None,
        help="llama.cpp inference threads (default: CPU cores - 2).",
    )
    parser.add_argument(
        "--llamacpp-ctx-size",
        type=int,
        default=16384,
        help="llama.cpp context window size.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    web_root = repo_root / "web"
    runtime_dir = repo_root / ".runtime"
    pid_file = runtime_dir / "services.pids.json"
    log_dir = runtime_dir / "logs"
    models_dir = repo_root / "models" / "llm"

    runtime_dir.mkdir(parents=True, exist_ok=True)

    existing = load_pid_file(pid_file)
    if existing:
        alive = []
        for pid in (existing.get("backend_pid"), existing.get("frontend_pid"), existing.get("llamacpp_pid")):
            if is_pid_alive(pid):
                alive.append(pid)
        if alive:
            print(
                f"Services seem running. Stop them first. Alive PIDs: {', '.join(str(pid) for pid in alive)}")
            return 1
        pid_file.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Step 1: Start llama.cpp (unless skipped or not configured)
    # ------------------------------------------------------------------
    llamacpp_pid = None
    cfg = _read_config(repo_root)

    if args.skip_llamacpp:
        print("[SKIP] llama.cpp startup skipped (--skip-llamacpp).")
    elif cfg and _is_llamacpp_provider(cfg):
        server_exe = _find_llamacpp_executable(repo_root)
        if not server_exe:
            print("[WARN] llama-server.exe not found. Skipping llama.cpp startup.")
            print("       Install llama.cpp or pass --skip-llamacpp to continue.")
        elif _llamacpp_ready(args.llamacpp_host, args.llamacpp_port):
            print(
                f"[OK] llama.cpp already running at {args.llamacpp_host}:{args.llamacpp_port}")
        else:
            candidates = _scan_gguf_models(models_dir)
            if not candidates:
                print(
                    f"[WARN] No GGUF files found in {models_dir}. Skipping llama.cpp startup.")
            else:
                selected = _select_model_interactive(
                    candidates, "Select model for llama.cpp server:")
                if selected is None:
                    print("[SKIP] llama.cpp startup skipped by user.")
                else:
                    llamacpp_log = log_dir / "llamacpp-server.log"
                    threads = args.llamacpp_threads or max(
                        4, (os.cpu_count() or 8) - 2)
                    cmd = [
                        server_exe,
                        "--host", args.llamacpp_host,
                        "--port", str(args.llamacpp_port),
                        "--model", str(selected),
                        "--alias", selected.stem,
                        "--ctx-size", str(max(1024, args.llamacpp_ctx_size)),
                        "--threads", str(max(1, threads)),
                        "--threads-http", "2",
                        "--batch-size", "1024",
                        "--ubatch-size", "512",
                        "--parallel", "1",
                        "--n-gpu-layers", "0",
                        "--jinja",
                        "--cont-batching",
                        "--metrics",
                    ]
                    print(f"\n[START] llama.cpp server")
                    print(f"  Model: {selected.name}")
                    print(
                        f"  Host:  http://{args.llamacpp_host}:{args.llamacpp_port}")
                    print(f"  Alias: {selected.stem}")
                    llamacpp_proc = start_process(
                        cmd, repo_root, os.environ, llamacpp_log)
                    llamacpp_pid = llamacpp_proc.pid
                    print(f"  PID: {llamacpp_pid}")
                    print(f"  Log: {llamacpp_log}")
                    print("  Waiting for server to be ready ...",
                          end=" ", flush=True)
                    deadline = time.time() + 300
                    ready = False
                    while time.time() <= deadline:
                        if _llamacpp_ready(args.llamacpp_host, args.llamacpp_port):
                            ready = True
                            break
                        time.sleep(2)
                    print("READY" if ready else "TIMEOUT")
                    if not ready:
                        print(
                            "[WARN] llama.cpp did not become ready in time. Continuing anyway.")
    elif cfg:
        print(
            f"[INFO] Provider is '{cfg['llm_config'].get('provider')}', not llama_cpp. Skipping llama.cpp startup.")
    else:
        print("[WARN] No config.json found. Skipping llama.cpp startup.")

    # ------------------------------------------------------------------
    # Step 2: Start backend
    # ------------------------------------------------------------------
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

    print(f"\n[START] Backend on http://localhost:{args.backend_port}")
    backend_proc = start_process(
        backend_cmd, repo_root, backend_env, backend_log)
    print(f"  PID: {backend_proc.pid}")
    print(f"  Log: {backend_log}")

    time.sleep(0.8)

    # ------------------------------------------------------------------
    # Step 3: Start frontend
    # ------------------------------------------------------------------
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
    if llamacpp_pid:
        data["llamacpp_pid"] = llamacpp_pid
        data["llamacpp_port"] = args.llamacpp_port

    with pid_file.open("w", encoding="utf-8") as file_obj:
        json.dump(data, file_obj, ensure_ascii=False, indent=2)
        file_obj.write("\n")

    print()
    print(
        f"Started backend PID: {backend_proc.pid} at http://localhost:{args.backend_port}")
    print(
        f"Started frontend PID: {frontend_proc.pid} at http://localhost:{args.frontend_port}")
    if llamacpp_pid:
        print(
            f"Started llama.cpp PID: {llamacpp_pid} at http://{args.llamacpp_host}:{args.llamacpp_port}")
    print(f"PID file: {pid_file}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
