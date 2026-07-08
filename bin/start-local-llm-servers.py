import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_MAIN_MODEL = "qwen3.6:27b"
DEFAULT_SMALL_MODEL = "llama3.2:3b"


def build_url(host: str, path: str) -> str:
    return host.rstrip("/") + path


def ollama_api_ready(host: str) -> bool:
    request = urllib.request.Request(
        build_url(host, "/api/version"), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status == 200
    except Exception:
        return False


def get_installed_models(host: str) -> set[str]:
    request = urllib.request.Request(
        build_url(host, "/api/tags"), method="GET")
    with urllib.request.urlopen(request, timeout=15) as response:
        data = json.loads(response.read().decode("utf-8"))
    models = data.get("models") if isinstance(data.get("models"), list) else []
    return {
        str(item.get("name") or "").strip()
        for item in models
        if isinstance(item, dict)
    }


def pull_model(host: str, model: str) -> None:
    data = json.dumps({"name": model, "stream": False}).encode("utf-8")
    request = urllib.request.Request(
        build_url(host, "/api/pull"),
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=1800):
        return


def find_ollama_executable(explicit_path: str) -> str | None:
    if explicit_path:
        candidate = Path(explicit_path)
        if candidate.exists():
            return str(candidate.resolve())

    path_candidates = [
        "ollama.exe",
        "ollama",
        str(Path.home() / "AppData" / "Local" /
            "Programs" / "Ollama" / "ollama.exe"),
        r"C:\Program Files\Ollama\ollama.exe",
    ]

    for item in path_candidates:
        found = shutil.which(item)
        if found:
            return found
        candidate = Path(item)
        if candidate.exists():
            return str(candidate.resolve())

    return None


def start_process(cmd: list[str], log_path: Path, cwd: Path) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("a", encoding="utf-8")
    creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    return subprocess.Popen(
        cmd,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
        cwd=str(cwd),
        env=os.environ.copy(),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ensure Ollama service is running and local models are available."
    )
    parser.add_argument("--ollama-exe", default="",
                        help="Path to Ollama executable.")
    parser.add_argument(
        "--ollama-host", default=DEFAULT_OLLAMA_HOST, help="Ollama API host.")
    parser.add_argument(
        "--main-model", default=DEFAULT_MAIN_MODEL, help="Main Ollama model name.")
    parser.add_argument(
        "--small-model", default=DEFAULT_SMALL_MODEL, help="Small Ollama model name.")
    parser.add_argument("--pull-missing", action="store_true",
                        help="Pull missing models after startup.")
    parser.add_argument(
        "--wait-seconds",
        type=int,
        default=15,
        help="Seconds to wait for Ollama API readiness.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    ollama_exe = find_ollama_executable(args.ollama_exe)
    ollama_log: Path | None = None

    if not ollama_api_ready(args.ollama_host):
        if not ollama_exe:
            print(
                "[ERROR] Ollama service is not reachable and ollama executable was not found.")
            print("Install Ollama, or pass --ollama-exe with the full path.")
            return 1
        print("=== Ollama Startup ===")
        print(f"Ollama executable: {ollama_exe}")
        log_dir = repo_root / "logs" / "local-llm"
        ollama_log = log_dir / "ollama.log"
        print("[START] Launching ollama serve ...")
        proc = start_process([ollama_exe, "serve"], ollama_log, repo_root)
        print(f"  PID: {proc.pid}")
        print(f"  Log: {ollama_log}")
        deadline = time.time() + max(args.wait_seconds, 0)
        while time.time() <= deadline:
            if ollama_api_ready(args.ollama_host):
                break
            time.sleep(1)
        if not ollama_api_ready(args.ollama_host):
            print("[ERROR] Ollama API did not become ready in time.")
            print(f"Check log: {ollama_log}")
            return 1
    else:
        print("=== Ollama Startup ===")
        print("Ollama API is already reachable.")

    installed = get_installed_models(args.ollama_host)
    missing = [name for name in (
        args.main_model, args.small_model) if name not in installed]
    if missing and args.pull_missing:
        print()
        print(f"Pulling missing models: {', '.join(missing)}")
        for model_name in missing:
            pull_model(args.ollama_host, model_name)
        installed = get_installed_models(args.ollama_host)
        missing = [name for name in (
            args.main_model, args.small_model) if name not in installed]
    elif missing:
        print()
        print(f"Missing models: {', '.join(missing)}")
        print(r"Run: python .\bin\download-local-llm-models.py")
    else:
        print()
        print("All required Ollama models are installed.")

    print(f"Ollama host: {args.ollama_host}")
    print()
    print("=== Ollama Ready ===")
    print(
        f"Main model endpoint:  {args.ollama_host.rstrip('/')}/v1  ({args.main_model})")
    print(
        f"Small model endpoint: {args.ollama_host.rstrip('/')}/v1  ({args.small_model})")
    print()
    print("Next step: python .\\bin\\apply-local-llm-config.py")
    print("Then start the app services without PowerShell scripts:")
    print("  Backend:  python -m app.main")
    print("  Frontend: change directory to the web folder, then run npm run dev")
    print()
    if ollama_log:
        print("If startup fails, inspect this log file:")
        print(f"  {ollama_log}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
