import argparse
import json
import time
import urllib.error
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


def pull_model(host: str, model: str) -> bool:
    try:
        ollama_request(host, "/api/pull", {"name": model, "stream": False})
        return True
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        print(f"[FAIL] Pull failed for {model}: HTTP {exc.code} {detail}")
        return False
    except Exception as exc:
        print(f"[FAIL] Pull failed for {model}: {exc}")
        return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ensure Ollama models are pulled and ready for local deployment."
    )
    parser.add_argument(
        "--ollama-host", default=DEFAULT_OLLAMA_HOST, help="Ollama API host.")
    parser.add_argument("--skip-main", action="store_true",
                        help="Skip main model download.")
    parser.add_argument("--skip-small", action="store_true",
                        help="Skip small model download.")
    parser.add_argument(
        "--main-model", default=DEFAULT_MAIN_MODEL, help="Main Ollama model name.")
    parser.add_argument(
        "--small-model", default=DEFAULT_SMALL_MODEL, help="Small Ollama model name.")
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

    ok = True
    installed = get_installed_models(args.ollama_host)

    if not args.skip_main and args.main_model not in installed:
        print(f"[PULL] Main model: {args.main_model}")
        ok = pull_model(args.ollama_host, args.main_model) and ok
        installed = get_installed_models(args.ollama_host)
    elif not args.skip_main:
        print(f"[SKIP] Main model already installed: {args.main_model}")

    if not args.skip_small and args.small_model not in installed:
        print(f"[PULL] Small model: {args.small_model}")
        ok = pull_model(args.ollama_host, args.small_model) and ok
        installed = get_installed_models(args.ollama_host)
    elif not args.skip_small:
        print(f"[SKIP] Small model already installed: {args.small_model}")

    print()
    print("=== Summary ===")
    main_exists = args.main_model in installed
    small_exists = args.small_model in installed

    print(
        f"Main model  ({args.main_model}): {'READY' if main_exists else 'MISSING'}")
    print(
        f"Small model ({args.small_model}): {'READY' if small_exists else 'MISSING'}")
    print()
    print(r"Next step: python .\bin\apply-local-llm-config.py")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
