import argparse
import subprocess
import sys
from pathlib import Path


DEFAULT_MAIN_MODEL = "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
DEFAULT_SMALL_MODEL = "Qwen3.6-27B-Q4_0.gguf"
DEFAULT_MAIN_PORT = 18080
DEFAULT_SMALL_PORT = 18081


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Launch dual local llama.cpp runtimes without any Ollama dependency."
    )
    parser.add_argument(
        "--python-exe",
        default=sys.executable,
        help="Python executable used to invoke the single-runtime launcher.",
    )
    parser.add_argument(
        "--server-script",
        default=str((repo_root / "bin" / "start-local-llamacpp-server.py").resolve()),
        help="Path to the single-runtime llama.cpp launcher script.",
    )
    parser.add_argument(
        "--server-exe",
        default="",
        help="Optional explicit path to llama-server.",
    )
    parser.add_argument(
        "--models-dir",
        default=str((repo_root / "models" / "llm").resolve()),
        help="Directory containing GGUF models.",
    )
    parser.add_argument("--main-model", default=DEFAULT_MAIN_MODEL, help="Main llama.cpp model file.")
    parser.add_argument("--small-model", default=DEFAULT_SMALL_MODEL, help="Small llama.cpp model file.")
    parser.add_argument("--main-port", type=int, default=DEFAULT_MAIN_PORT, help="Main llama.cpp HTTP port.")
    parser.add_argument("--small-port", type=int, default=DEFAULT_SMALL_PORT, help="Small llama.cpp HTTP port.")
    parser.add_argument("--main-ctx-size", type=int, default=8192, help="Main model context size.")
    parser.add_argument("--small-ctx-size", type=int, default=4096, help="Small model context size.")
    parser.add_argument("--main-threads", type=int, default=0, help="Main model threads; 0 keeps launcher default.")
    parser.add_argument("--small-threads", type=int, default=0, help="Small model threads; 0 keeps launcher default.")
    parser.add_argument("--main-gpu-layers", type=int, default=0, help="Main model GPU layers.")
    parser.add_argument("--small-gpu-layers", type=int, default=0, help="Small model GPU layers.")
    parser.add_argument("--wait-seconds", type=int, default=240, help="Ready wait per runtime.")
    return parser.parse_args()


def _runtime_cmd(
    *,
    python_exe: str,
    server_script: str,
    server_exe: str,
    model_path: Path,
    alias: str,
    port: int,
    ctx_size: int,
    threads: int,
    gpu_layers: int,
    wait_seconds: int,
) -> list[str]:
    cmd = [
        python_exe,
        server_script,
        "--host-url",
        f"http://127.0.0.1:{port}",
        "--model-path",
        str(model_path),
        "--alias",
        alias,
        "--ctx-size",
        str(ctx_size),
        "--gpu-layers",
        str(max(0, gpu_layers)),
        "--wait-seconds",
        str(max(30, wait_seconds)),
    ]
    if server_exe:
        cmd.extend(["--server-exe", server_exe])
    if threads > 0:
        cmd.extend(["--threads", str(threads)])
    return cmd


def main() -> int:
    args = parse_args()
    models_dir = Path(args.models_dir).resolve()
    main_model_path = (models_dir / args.main_model).resolve()
    small_model_path = (models_dir / args.small_model).resolve()

    missing = [str(path) for path in (main_model_path, small_model_path) if not path.exists()]
    if missing:
        print("[ERROR] Missing GGUF files for dual llama.cpp startup:")
        for item in missing:
            print(f"  - {item}")
        return 1

    print("=== Dual llama.cpp Startup ===")
    print("Runtime policy: pure llama.cpp, no Ollama dependency")
    print(f"Main model:  {main_model_path}")
    print(f"Small model: {small_model_path}")

    main_cmd = _runtime_cmd(
        python_exe=args.python_exe,
        server_script=args.server_script,
        server_exe=args.server_exe,
        model_path=main_model_path,
        alias=args.main_model,
        port=args.main_port,
        ctx_size=args.main_ctx_size,
        threads=args.main_threads,
        gpu_layers=args.main_gpu_layers,
        wait_seconds=args.wait_seconds,
    )
    small_cmd = _runtime_cmd(
        python_exe=args.python_exe,
        server_script=args.server_script,
        server_exe=args.server_exe,
        model_path=small_model_path,
        alias=args.small_model,
        port=args.small_port,
        ctx_size=args.small_ctx_size,
        threads=args.small_threads,
        gpu_layers=args.small_gpu_layers,
        wait_seconds=args.wait_seconds,
    )

    print()
    print("Starting main llama.cpp runtime...")
    main_result = subprocess.run(main_cmd, check=False)
    if main_result.returncode != 0:
        print("[ERROR] Main llama.cpp runtime failed to start.")
        return main_result.returncode

    print()
    print("Starting small llama.cpp runtime...")
    small_result = subprocess.run(small_cmd, check=False)
    if small_result.returncode != 0:
        print("[ERROR] Small llama.cpp runtime failed to start.")
        return small_result.returncode

    print()
    print("=== Dual llama.cpp Ready ===")
    print(f"Main endpoint:  http://127.0.0.1:{args.main_port}/v1")
    print(f"Small endpoint: http://127.0.0.1:{args.small_port}/v1")
    print(r"Next step: python .\bin\apply-local-llm-config.py --provider llama_cpp --small-provider llama_cpp")
    return 0


if __name__ == "__main__":
    sys.exit(main())
