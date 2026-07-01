import argparse
import json
import time
import urllib.request
import sys


DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_MAIN_MODEL = "qwen3.6:27b"
DEFAULT_SMALL_MODEL = "llama3.2:3b"


def build_url(host: str, path: str) -> str:
    return host.rstrip("/") + path


def ollama_request(host: str, path: str, payload: dict | None = None) -> dict:
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        build_url(host, path),
        data=data,
        headers=headers,
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_for_ollama(host: str, wait_seconds: int) -> bool:
    deadline = time.time() + max(0, wait_seconds)
    while time.time() <= deadline:
        try:
            ollama_request(host, "/api/version")
            return True
        except Exception:
            time.sleep(1)
    return False


def get_installed_models(host: str) -> set[str]:
    data = ollama_request(host, "/api/tags")
    models = data.get("models") if isinstance(data.get("models"), list) else []
    return {str(item.get("name") or "").strip() for item in models if isinstance(item, dict)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate local Ollama models for enterprise offline deployment."
    )
    parser.add_argument(
        "--ollama-host", default=DEFAULT_OLLAMA_HOST, help="Ollama API host.")
    parser.add_argument(
        "--main-model", default=DEFAULT_MAIN_MODEL, help="Main Ollama model name.")
    parser.add_argument(
        "--small-model", default=DEFAULT_SMALL_MODEL, help="Small Ollama model name.")
    parser.add_argument("--verify-only", action="store_true",
                        help="Compatibility flag. Validation-only is always enforced in offline mode.")
    parser.add_argument(
        "--wait-seconds",
        type=int,
        default=10,
        help="Seconds to wait for Ollama API to become reachable.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    print("=== Ollama Model Preparation ===")
    print(f"Ollama host: {args.ollama_host}")
    print()

    if not wait_for_ollama(args.ollama_host, args.wait_seconds):
        print("[ERROR] Ollama API is not reachable.")
        print(r"Start it first with: python .\bin\start-local-llm-servers.py")
        return 1

    installed = get_installed_models(args.ollama_host)
    main_exists = args.main_model in installed
    small_exists = args.small_model in installed
    if main_exists:
        print(f"[OK] Main model already installed: {args.main_model}")
    else:
        print(f"[MISSING] Main model not found: {args.main_model}")
    if small_exists:
        print(f"[OK] Small model already installed: {args.small_model}")
    else:
        print(f"[MISSING] Small model not found: {args.small_model}")

    print()
    print("=== Summary ===")

    print(
        f"Main model  ({args.main_model}): {'READY' if main_exists else 'MISSING'}")
    print(
        f"Small model ({args.small_model}): {'READY' if small_exists else 'MISSING'}")
    print()
    print("Offline policy: no remote pull is attempted by this script.")
    print("If a model is missing, import it from the internal artifact repository before retrying.")
    print(r"Next step: python .\bin\apply-local-llm-config.py")

    return 0 if main_exists and small_exists else 1


if __name__ == "__main__":
    sys.exit(main())
