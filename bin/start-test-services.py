"""Quick test deployment using Llama-3.2-3B (1.88 GB) for fast verification.

Does NOT modify app/config.json — generates a temporary config with model
overrides and sets APP_CONFIG env var.  All child processes are cleaned up
on Ctrl+C or termination.

Usage:
    python bin/start-test-services.py
    python bin/start-test-services.py --llama-port 18090 --skip-frontend
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "app"
TEST_CONFIG_NAME = "config.test.json"
TEST_CONFIG_PATH = APP_DIR / TEST_CONFIG_NAME

DEFAULTS = {
    "llama_port": 18080,
    "backend_port": 8000,
    "frontend_port": 5173,
    "model_filename": "Llama-3.2-3B-Instruct-Q4_K_M.gguf",
    "model_path": str(REPO_ROOT / "models/llm/Llama-3.2-3B-Instruct-Q4_K_M.gguf"),
    "ctx_size": 4096,
    "max_tokens": 1024,
    "threads": max(4, (os.cpu_count() or 8) - 2),
}

_child_processes: list[subprocess.Popen] = []


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

def _find_llama_server() -> str | None:
    """Locate llama-server.exe, same search order as start-local-llamacpp-server."""
    candidates: list[str] = [
        "llama-server.exe",
        "llama-server",
        str(REPO_ROOT / "third_party/llama.cpp/bin-win-cpu-x64/llama-server.exe"),
        str(REPO_ROOT / "third_party/llama.cpp/bin-win-cpu-x64/llama-server"),
    ]
    for item in candidates:
        found = shutil.which(item)
        if found:
            return found
        candidate = Path(item)
        if candidate.exists():
            return str(candidate.resolve())
    return None


def _find_backend_python() -> str:
    """Return the venv Python (or current Python as fallback)."""
    for candidate in (
        REPO_ROOT / "app/.venv/Scripts/python.exe",
        REPO_ROOT / ".venv/Scripts/python.exe",
    ):
        if candidate.exists():
            return str(candidate.resolve())
    return sys.executable


def _http_ok(url: str, timeout: int = 5) -> bool:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def _port_open(host: str, port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    try:
        s.connect((host, port))
        s.close()
        return True
    except Exception:
        return False


def _start_process(cmd: list[str], cwd: Path, env: dict[str, str] | None,
                   log_path: Path) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fh = log_path.open("a", encoding="utf-8")
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env or os.environ.copy(),
        stdout=log_fh,
        stderr=subprocess.STDOUT,
    )
    _child_processes.append(proc)
    return proc


# ---------------------------------------------------------------------------
#  Temporary config
# ---------------------------------------------------------------------------

def _create_test_config() -> str:
    """Copy production config and override model fields for test."""
    prod = APP_DIR / "config.json"
    if not prod.exists():
        print(f"[ERROR] Production config not found: {prod}")
        sys.exit(1)

    with open(prod, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)

    model_name = DEFAULTS["model_filename"]
    api_base = f"http://127.0.0.1:{DEFAULTS['llama_port']}/v1"

    # -- llm_config (base route)
    llm = cfg.setdefault("llm_config", {})
    llm["provider"] = "llama_cpp"
    llm["api_base"] = api_base
    llm["model"] = model_name
    llm["max_tokens"] = DEFAULTS["max_tokens"]
    llm.setdefault("api_key", "")
    llm.setdefault("temperature", 0.2)
    llm.setdefault("timeout", 600)
    llm.setdefault("headers", {})

    # -- local_llm (routed dual-model sharing single instance)
    local = cfg.setdefault("local_llm", {})
    local["enabled"] = True
    local["routing_enabled"] = True
    local["allow_small_to_main_fallback"] = True
    local.setdefault("timeout_sec", 600)
    local.setdefault("json_repair_enabled", True)

    for role in ("main_model", "small_model"):
        block = local.setdefault(role, {})
        block["provider"] = "llama_cpp"
        block["api_base"] = api_base          # both point to port 18080
        block["model"] = model_name
        block["api_key"] = ""
        block.setdefault("temperature", 0.2 if role == "main_model" else 0.1)
        block.setdefault("max_tokens", DEFAULTS["max_tokens"] if role == "main_model" else 512)
        block.setdefault("timeout", 600 if role == "main_model" else 120)
        block.setdefault("headers", {})

    # Write temp config alongside original so relative paths (../data etc.) resolve correctly
    with open(TEST_CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=4)
        fh.write("\n")

    return str(TEST_CONFIG_PATH.resolve())


def _remove_test_config() -> None:
    if TEST_CONFIG_PATH.exists():
        try:
            TEST_CONFIG_PATH.unlink()
            print(f"[CLEANUP] Removed {TEST_CONFIG_PATH}")
        except OSError:
            pass


# ---------------------------------------------------------------------------
#  Cleanup
# ---------------------------------------------------------------------------

def _cleanup() -> None:
    print("\n[CLEANUP] Stopping services ...")
    for proc in _child_processes:
        if proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass

    # Grace period then force-kill
    deadline = time.time() + 3
    while time.time() < deadline:
        if all(p.poll() is not None for p in _child_processes):
            break
        time.sleep(0.5)

    for proc in _child_processes:
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass

    _remove_test_config()
    print("[CLEANUP] Done.")


def _signal_handler(signum: int, _frame) -> None:
    print(f"\n[SIGNAL] Received {signal.Signals(signum).name}, shutting down ...")
    _cleanup()
    sys.exit(0)


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Quick test deployment — Llama-3.2-3B (1.88 GB), no config.json modified."
    )
    parser.add_argument("--llama-port", type=int, default=DEFAULTS["llama_port"])
    parser.add_argument("--backend-port", type=int, default=DEFAULTS["backend_port"])
    parser.add_argument("--frontend-port", type=int, default=DEFAULTS["frontend_port"])
    parser.add_argument("--model-path", default=DEFAULTS["model_path"])
    parser.add_argument("--ctx-size", type=int, default=DEFAULTS["ctx_size"])
    parser.add_argument("--threads", type=int, default=DEFAULTS["threads"])
    parser.add_argument("--skip-frontend", action="store_true")
    args = parser.parse_args()

    # Resolve ports into DEFAULTS so downstream helpers see them
    DEFAULTS["llama_port"] = args.llama_port
    DEFAULTS["backend_port"] = args.backend_port
    DEFAULTS["frontend_port"] = args.frontend_port

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # --- environment & prerequisites ---
    model_path = Path(args.model_path).resolve()
    if not model_path.exists():
        print(f"[ERROR] Model not found: {model_path}")
        return 1

    server_exe = _find_llama_server()
    if not server_exe:
        print("[ERROR] llama-server.exe not found.")
        print("Expected at: third_party/llama.cpp/bin-win-cpu-x64/llama-server.exe")
        print("Run: python bin/deploy_desktop.py  or  python bin/download_deployment_assets.py --asset-kind llama_cpp")
        return 1

    python_exe = _find_backend_python()

    # --- port conflict check ---
    for name, port in (("llama.cpp", args.llama_port), ("backend", args.backend_port)):
        if _port_open("127.0.0.1", port):
            print(f"[WARN] Port {port} ({name}) is already in use — may cause startup failure.")

    # --- temporary config ---
    temp_config = _create_test_config()
    model_gb = model_path.stat().st_size / (1024 ** 3)

    print("=" * 62)
    print("  AI Law Assistant — Test Deployment (llama.cpp)")
    print("=" * 62)
    print(f"  Model:       {model_path.name}  ({model_gb:.2f} GB)")
    print(f"  llama.cpp:   http://127.0.0.1:{args.llama_port}")
    print(f"  Backend:     http://127.0.0.1:{args.backend_port}")
    if not args.skip_frontend:
        print(f"  Frontend:    http://127.0.0.1:{args.frontend_port}")
    print(f"  Temp config: {temp_config}")
    print(f"  Production config (app/config.json):  UNTOUCHED")
    print("=" * 62)

    log_dir = REPO_ROOT / "logs/test-deploy"

    # ==================================================================
    #  1.  llama.cpp server
    # ==================================================================
    print(f"\n[1/3] Starting llama.cpp server ...")

    llama_cmd = [
        server_exe,
        "--host", "127.0.0.1",
        "--port", str(args.llama_port),
        "--model", str(model_path),
        "--alias", model_path.name,
        "--ctx-size", str(max(1024, args.ctx_size)),
        "--threads", str(max(1, args.threads)),
        "--threads-http", "2",
        "--batch-size", "512",
        "--ubatch-size", "256",
        "--parallel", "1",
        "--n-gpu-layers", "0",
        "--jinja",
        "--cont-batching",
        "--metrics",
    ]
    llama_proc = _start_process(llama_cmd, REPO_ROOT, None,
                                log_dir / "llama-cpp-test.log")
    print(f"  PID: {llama_proc.pid}")

    health_url = f"http://127.0.0.1:{args.llama_port}/health"
    print("  Waiting for llama.cpp ...", end="", flush=True)
    deadline = time.time() + 120
    ready = False
    while time.time() < deadline:
        if _http_ok(health_url, timeout=3):
            ready = True
            break
        if llama_proc.poll() is not None:
            print(f"\n[ERROR] llama.cpp exited (code={llama_proc.returncode})")
            _cleanup()
            return 1
        time.sleep(2)
        print(".", end="", flush=True)

    if not ready:
        print("\n[ERROR] llama.cpp did not become ready.")
        _cleanup()
        return 1
    print(" ready.")

    # ==================================================================
    #  2.  Backend
    # ==================================================================
    print(f"\n[2/3] Starting backend ...")

    backend_env = os.environ.copy()
    backend_env["APP_CONFIG"] = temp_config
    backend_env["APP_PORT"] = str(args.backend_port)
    backend_env["APP_PORT_AUTO_SWITCH"] = "0"

    backend_proc = _start_process(
        [python_exe, "-m", "app.main"], REPO_ROOT, backend_env,
        log_dir / "backend-test.log")
    print(f"  PID: {backend_proc.pid}")

    backend_health = f"http://127.0.0.1:{args.backend_port}/health"
    print("  Waiting for backend ...", end="", flush=True)
    deadline = time.time() + 90
    ready = False
    while time.time() < deadline:
        if _http_ok(backend_health, timeout=3):
            ready = True
            break
        if backend_proc.poll() is not None:
            print(f"\n[ERROR] Backend exited (code={backend_proc.returncode})")
            _cleanup()
            return 1
        time.sleep(2)
        print(".", end="", flush=True)

    if not ready:
        print("\n[ERROR] Backend did not become ready.")
        _cleanup()
        return 1
    print(" ready.")

    # ==================================================================
    #  3.  Frontend (optional)
    # ==================================================================
    frontend_proc = None
    if not args.skip_frontend:
        print(f"\n[3/3] Starting frontend ...")

        npm_exe = shutil.which("npm.cmd") or shutil.which("npm")
        if not npm_exe:
            print("[WARN] npm not found, skip frontend.")
        else:
            frontend_proc = _start_process(
                [npm_exe, "run", "dev", "--", "--host", "0.0.0.0",
                 "--port", str(args.frontend_port)],
                REPO_ROOT / "web", None,
                log_dir / "frontend-test.log")
            print(f"  PID: {frontend_proc.pid}")

            print("  Waiting for frontend ...", end="", flush=True)
            deadline = time.time() + 40
            while time.time() < deadline:
                if _port_open("127.0.0.1", args.frontend_port):
                    break
                if frontend_proc.poll() is not None:
                    print(f"\n[WARN] Frontend exited early (code={frontend_proc.returncode})")
                    frontend_proc = None
                    break
                time.sleep(2)
                print(".", end="", flush=True)
            print()

    # ==================================================================
    #  Ready
    # ==================================================================
    print()
    print("=" * 62)
    print("  ALL SERVICES RUNNING")
    print("=" * 62)
    print(f"  llama.cpp   http://127.0.0.1:{args.llama_port}/v1")
    print(f"  Backend     http://127.0.0.1:{args.backend_port}")
    if frontend_proc and frontend_proc.poll() is None:
        print(f"  Frontend    http://127.0.0.1:{args.frontend_port}")
    print()
    print("  Press Ctrl+C to stop all services.")
    print("=" * 62)
    print()
    print("--- Production Switch Notice ---")
    print("  This test uses Llama-3.2-3B (1.88 GB) for fast startup.")
    print("  For production deployment with 35B model:")
    print(f"    1. Place Qwen3.6-35B-A3B-UD-Q4_K_M.gguf  in  {REPO_ROOT / 'models/llm'}")
    print("    2. Run:  python bin/deploy_desktop.py")
    print("       or manually:  python bin/start-local-llamacpp-server.py  +  python -m app.main")
    print("  Test model is for FUNCTIONAL VERIFICATION only, not quality.")
    print("---------------------------------")

    # ==================================================================
    #  Monitor and keep alive
    # ==================================================================
    try:
        while True:
            procs = [(llama_proc, "llama.cpp"), (backend_proc, "backend")]
            if frontend_proc:
                procs.append((frontend_proc, "frontend"))

            for proc, name in procs:
                if proc.poll() is not None:
                    print(f"\n[ERROR] {name} process died (code={proc.returncode}).")
                    # If llama or backend die, it's fatal; frontend is optional
                    if name != "frontend":
                        _cleanup()
                        return 1
            time.sleep(5)
    except KeyboardInterrupt:
        pass

    _cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
