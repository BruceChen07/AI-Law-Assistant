import argparse
import json
import sys
from pathlib import Path


DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_LLAMACPP_HOST = "http://127.0.0.1:18080"
DEFAULT_LLAMACPP_SMALL_HOST = "http://127.0.0.1:18081"
DEFAULT_OLLAMA_MAIN_MODEL = "qwen3.6:27b"
DEFAULT_OLLAMA_SMALL_MODEL = "qwen3:4b"
DEFAULT_LLAMACPP_MAIN_MODEL = "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
DEFAULT_LLAMACPP_SMALL_MODEL = "Qwen3.6-27B-Q4_0.gguf"


def _base_url(provider: str, ollama_host: str, llamacpp_host: str) -> str:
    provider_name = str(provider or "").strip().lower()
    if provider_name == "ollama":
        return f"{ollama_host.rstrip('/')}/v1"
    return f"{llamacpp_host.rstrip('/')}/v1"


def _model_block(
    *,
    provider: str,
    api_base: str,
    model: str,
    temperature: float,
    max_tokens: int,
    timeout: int,
) -> dict:
    return {
        "provider": provider,
        "api_base": api_base,
        "api_key": "",
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "timeout": timeout,
        "headers": {},
    }


def build_local_llm_config(
    enabled: bool,
    *,
    main_provider: str,
    main_api_base: str,
    main_model: str,
    small_provider: str,
    small_api_base: str,
    small_model: str,
) -> dict:
    return {
        "enabled": enabled,
        "routing_enabled": True,
        "allow_small_to_main_fallback": True,
        "timeout_sec": 600,
        "json_repair_enabled": True,
        "main_model": _model_block(
            provider=main_provider,
            api_base=main_api_base,
            model=main_model,
            temperature=0.2,
            max_tokens=2048,
            timeout=600,
        ),
        "small_model": _model_block(
            provider=small_provider,
            api_base=small_api_base,
            model=small_model,
            temperature=0.1,
            max_tokens=1024,
            timeout=120,
        ),
        "routing": {
            "task_profiles": {
                "default": "main",
                "contract_audit_main": "main",
                "contract_audit_memory": "main",
                "contract_clause_audit": "main",
                "tax_risk_main": "main",
                "memory_flush": "small",
                "tax_match_small": "small",
                "entity_extract_small": "small",
            },
            "tax_match_use_small_model": True,
            "entity_extract_use_small_model": True,
            "high_risk_force_main": True,
        },
        "execution": {
            "fallback_on_error": True,
            "fallback_on_invalid_json": True,
            "tax_match_main_review_labels": ["non_compliant"],
            "tax_match_min_confidence": 0.65,
            "tax_match_max_workers": 2,
            "tax_risk_max_workers": 2,
            "entity_extract_max_workers": 4,
            "memory_clause_force_main_for_priority": False,
            "memory_flush_force_main": False,
        },
    }


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Enable or disable enterprise offline local_llm configuration in app/config.json."
    )
    parser.add_argument(
        "--config-path",
        default=str(repo_root / "app" / "config.json"),
        help="Path to app/config.json",
    )
    parser.add_argument(
        "--provider",
        choices=["ollama", "llama_cpp"],
        default="llama_cpp",
        help="Main local provider to activate.",
    )
    parser.add_argument(
        "--small-provider",
        choices=["ollama", "llama_cpp"],
        default="llama_cpp",
        help="Provider used by the small model route.",
    )
    parser.add_argument(
        "--ollama-host",
        default=DEFAULT_OLLAMA_HOST,
        help="Base host for the local Ollama service.",
    )
    parser.add_argument(
        "--llama-cpp-host",
        default=DEFAULT_LLAMACPP_HOST,
        help="Base host for the local llama.cpp server.",
    )
    parser.add_argument(
        "--small-llama-cpp-host",
        default=DEFAULT_LLAMACPP_SMALL_HOST,
        help="Base host for the small-model llama.cpp server.",
    )
    parser.add_argument(
        "--single-instance",
        action="store_true",
        default=False,
        help="Auto-configure small model to use the same host and model as main (single-instance mode).",
    )
    parser.add_argument(
        "--main-model",
        default="",
        help="Override the main model name/id.",
    )
    parser.add_argument(
        "--small-model",
        default="",
        help="Override the small model name/id.",
    )
    parser.add_argument("--disable", action="store_true",
                        help="Disable local_llm instead of enabling it.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show the result without writing the file.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = Path(args.config_path).resolve()

    if not config_path.exists():
        print(f"[ERROR] Config not found: {config_path}")
        print("Run repository initialization first, for example: python -m app.main --init")
        return 1

    print("=== Apply Local LLM Config ===")
    print(f"Config: {config_path}")

    with config_path.open("r", encoding="utf-8") as file_obj:
        config = json.load(file_obj)

    previous_enabled = None
    if isinstance(config.get("local_llm"), dict):
        previous_enabled = config["local_llm"].get("enabled")

    main_provider = args.provider
    small_provider = args.small_provider
    main_api_base = _base_url(
        main_provider, args.ollama_host, args.llama_cpp_host)
    small_llamacpp_host = args.small_llama_cpp_host or args.llama_cpp_host
    small_api_base = _base_url(
        small_provider, args.ollama_host, small_llamacpp_host)
    main_model = args.main_model or (
        DEFAULT_LLAMACPP_MAIN_MODEL if main_provider == "llama_cpp" else DEFAULT_OLLAMA_MAIN_MODEL
    )
    small_model = args.small_model or (
        DEFAULT_OLLAMA_SMALL_MODEL if small_provider == "ollama" else DEFAULT_LLAMACPP_SMALL_MODEL
    )

    # Single-instance convenience: override small to match main
    if args.single_instance:
        small_provider = main_provider
        small_api_base = main_api_base
        small_model = main_model

    config["local_llm"] = build_local_llm_config(
        enabled=not args.disable,
        main_provider=main_provider,
        main_api_base=main_api_base,
        main_model=main_model,
        small_provider=small_provider,
        small_api_base=small_api_base,
        small_model=small_model,
    )
    config["llm_config"] = _model_block(
        provider=main_provider,
        api_base=main_api_base,
        model=main_model,
        temperature=0.2,
        max_tokens=2048,
        timeout=600,
    )
    config["network_policy"] = {
        "enabled": True,
        "mode": "offline_strict",
        "allow_private_ip_ranges": True,
        "allowed_hosts": [
            "127.0.0.1",
            "localhost",
            "llm-gateway.intra",
            "ollama.intra",
            "llamacpp.intra",
            "ocr-gateway.intra",
        ],
        "allowed_domain_suffixes": [
            ".intra",
            ".corp.local",
            ".svc.cluster.local",
        ],
    }

    if args.dry_run:
        print()
        print(f"[DRY RUN] Would set local_llm.enabled = {not args.disable}")
        print(f"[DRY RUN] Previous value: {previous_enabled}")
        print(json.dumps(config, ensure_ascii=False, indent=4))
        return 0

    with config_path.open("w", encoding="utf-8") as file_obj:
        json.dump(config, file_obj, ensure_ascii=False, indent=4)
        file_obj.write("\n")

    print()
    print(f"[OK] local_llm.enabled = {not args.disable}")
    print(f"     Previous: {previous_enabled}")

    if not args.disable:
        single_instance = (
            main_provider == "llama_cpp"
            and small_provider == "llama_cpp"
            and main_api_base == small_api_base
        )
        print()
        print("Enterprise offline mode is now ACTIVE:")
        print(f"  Main model:  {main_api_base} ({main_provider}:{main_model})")
        print(
            f"  Small model: {small_api_base} ({small_provider}:{small_model})")
        if single_instance:
            print("  Runtime policy: single-instance (small tasks auto-fallback to main)")
        else:
            print("  Runtime policy: dual llama.cpp or local-only fallback; no public cloud route")
        print()
        if single_instance:
            print(
                r"Next step: start the llama.cpp runtime with python .\bin\start-local-llamacpp-stack.py")
        elif main_provider == "llama_cpp" and small_provider == "llama_cpp":
            print(
                r"Next step: start both local llama.cpp runtimes with python .\bin\start-local-llamacpp-stack.py --no-single-instance")
        elif main_provider == "llama_cpp":
            print(
                r"Next step: stage the GGUF model and run python .\bin\start-local-llamacpp-server.py")
        else:
            print(r"Next step: stage local model assets and run python .\bin\download-local-llm-models.py --verify-only")
    else:
        print("[OK] Local LLM mode disabled. Base internal service route only.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
