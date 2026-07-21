import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


DEFAULT_SERVER_HOST = "http://127.0.0.1:18080"


def _scan_gguf_models(model_dir: Path) -> list[Path]:
    """Scan model_dir for *.gguf files, sorted by name."""
    if not model_dir.is_dir():
        return []
    return sorted(
        [p for p in model_dir.iterdir() if p.suffix.lower() == ".gguf"],
        key=lambda p: p.name.lower(),
    )


def _select_model_interactive(candidates: list[Path]) -> Path:
    """Prompt the user to select a model from the list."""
    print("Available GGUF models in models/llm/:")
    for idx, p in enumerate(candidates, start=1):
        size_mb = p.stat().st_size / (1024 * 1024)
        print(f"  [{idx}] {p.name}  ({size_mb:.0f} MB)")
    print()
    while True:
        try:
            choice = input(f"Select model [1-{len(candidates)}]: ").strip()
            idx = int(choice)
            if 1 <= idx <= len(candidates):
                return candidates[idx - 1]
        except ValueError:
            pass
        print(f"Please enter a number between 1 and {len(candidates)}.")


def _build_url(base: str, path: str) -> str:
    return base.rstrip("/") + path


def _parse_host_url(host_url: str) -> tuple[str, int]:
    parsed = urlsplit(host_url)
    hostname = parsed.hostname or "127.0.0.1"
    port = parsed.port or 18080
    if hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError(
            "llama.cpp host must stay on loopback for edge-only deployment"
        )
    return hostname, port


def _server_ready(host_url: str) -> bool:
    for path in ("/health", "/v1/models"):
        request = urllib.request.Request(
            _build_url(host_url, path), method="GET")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                if response.status == 200:
                    return True
        except Exception:
            continue
    return False


def _find_server_executable(explicit_path: str) -> str | None:
    if explicit_path:
        candidate = Path(explicit_path)
        if candidate.exists():
            return str(candidate.resolve())

    repo_root = Path(__file__).resolve().parents[1]
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
        candidate = Path(item)
        if candidate.exists():
            return str(candidate.resolve())
    return None


def _start_process(cmd: list[str], log_path: Path, cwd: Path) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("a", encoding="utf-8")
    creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    return subprocess.Popen(
        cmd,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        cwd=str(cwd),
        env=os.environ.copy(),
        creationflags=creationflags,
    )


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Launch a local llama.cpp server for edge-only inference."
    )
    parser.add_argument("--server-exe", default="",
                        help="Path to llama-server executable.")
    parser.add_argument(
        "--host-url",
        default=DEFAULT_SERVER_HOST,
        help="Loopback host URL for llama.cpp, for example http://127.0.0.1:18080",
    )
    parser.add_argument(
        "--model-path",
        default="",
        help="Path to the GGUF model file. If not provided, interactively select from models/llm/.",
    )
    parser.add_argument(
        "--model-dir",
        default=str(repo_root / "models" / "llm"),
        help="Directory to scan for GGUF models (used when --model-path is empty).",
    )
    parser.add_argument(
        "--alias",
        default="",
        help="Model alias returned by /v1/models. Defaults to the GGUF filename.",
    )
    parser.add_argument("--ctx-size", type=int, default=16384,
                        help="Context window size.")
    parser.add_argument("--threads", type=int, default=max(4,
                        (os.cpu_count() or 8) - 2), help="Inference threads.")
    parser.add_argument("--threads-http", type=int,
                        default=2, help="HTTP worker threads.")
    parser.add_argument("--batch-size", type=int,
                        default=1024, help="Batch size.")
    parser.add_argument("--ubatch-size", type=int,
                        default=512, help="Micro batch size.")
    parser.add_argument("--gpu-layers", type=int, default=0,
                        help="Number of layers offloaded to GPU.")
    parser.add_argument("--parallel", type=int, default=1,
                        help="Parallel request slots.")
    parser.add_argument("--no-mmap", action="store_true",
                        help="Disable mmap when storage is unstable.")
    parser.add_argument(
        "--wait-seconds",
        type=int,
        default=180,
        help="Seconds to wait for the model to finish loading.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    try:
        host, port = _parse_host_url(args.host_url)
    except ValueError as exc:
        print(f"[ERROR] {exc}")
        return 1

    # Resolve model path: explicit arg, or interactive scan
    if args.model_path:
        model_path = Path(args.model_path).resolve()
    else:
        model_dir = Path(args.model_dir).resolve()
        candidates = _scan_gguf_models(model_dir)
        if not candidates:
            print(f"[ERROR] No GGUF files found in: {model_dir}")
            print("Place a GGUF model in models/llm/ or use --model-path.")
            return 1
        if len(candidates) == 1:
            model_path = candidates[0]
            print(f"Auto-selected single model: {model_path.name}")
        else:
            model_path = _select_model_interactive(candidates)
    if not model_path.exists():
        print(f"[ERROR] GGUF model not found: {model_path}")
        print("Place the local model on disk first. No cloud download route is used by this script.")
        return 1

    # Default alias to filename without .gguf extension
    alias = args.alias.strip() or model_path.stem

    server_exe = _find_server_executable(args.server_exe)
    if not server_exe:
        print("[ERROR] llama-server executable was not found.")
        print("Build llama.cpp locally or pass --server-exe with the full path.")
        return 1

    if _server_ready(args.host_url):
        print("=== llama.cpp Startup ===")
        print("llama.cpp API is already reachable.")
        print(f"Host: {args.host_url}")
        print(f"Model alias: {alias}")
        return 0

    log_dir = repo_root / "logs" / "local-llm"
    server_log = log_dir / "llama-cpp-server.log"
    cmd = [
        server_exe,
        "--host",
        host,
        "--port",
        str(port),
        "--model",
        str(model_path),
        "--alias",
        alias,
        "--ctx-size",
        str(max(1024, args.ctx_size)),
        "--threads",
        str(max(1, args.threads)),
        "--threads-http",
        str(max(1, args.threads_http)),
        "--batch-size",
        str(max(32, args.batch_size)),
        "--ubatch-size",
        str(max(32, args.ubatch_size)),
        "--parallel",
        str(max(1, args.parallel)),
        "--n-gpu-layers",
        str(max(0, args.gpu_layers)),
        "--jinja",
        "--cont-batching",
        "--metrics",
    ]
    if args.no_mmap:
        cmd.append("--no-mmap")

    print("=== llama.cpp Startup ===")
    print(f"Executable: {server_exe}")
    print(f"Host: {args.host_url}")
    print(f"Model: {model_path}")
    print(f"Alias: {alias}")
    print(
        json.dumps(
            {
                "ctx_size": max(1024, args.ctx_size),
                "threads": max(1, args.threads),
                "threads_http": max(1, args.threads_http),
                "batch_size": max(32, args.batch_size),
                "ubatch_size": max(32, args.ubatch_size),
                "gpu_layers": max(0, args.gpu_layers),
                "parallel": max(1, args.parallel),
                "edge_only": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    proc = _start_process(cmd, server_log, repo_root)
    print(f"[START] PID: {proc.pid}")
    print(f"[START] Log: {server_log}")

    deadline = time.time() + max(1, args.wait_seconds)
    while time.time() <= deadline:
        if _server_ready(args.host_url):
            break
        time.sleep(2)

    if not _server_ready(args.host_url):
        print("[ERROR] llama.cpp API did not become ready in time.")
        print(f"Check log: {server_log}")
        return 1

    print()
    print("=== llama.cpp Ready ===")
    print(f"Endpoint: {args.host_url.rstrip('/')}/v1")
    print(f"Model alias: {alias}")
    print(r"Next step: python .\bin\apply-local-llm-config.py --provider llama_cpp")
    print("Then start the app services:")
    print("  Backend:  python -m app.main")
    print("  Frontend: change directory to the web folder, then run npm run dev")
    return 0


if __name__ == "__main__":
    sys.exit(main())
