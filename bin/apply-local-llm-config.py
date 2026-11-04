import argparse
import json
import sys
from pathlib import Path


DEFAULT_LLAMACPP_HOST = "http://127.0.0.1:18080"
DEFAULT_LLAMACPP_SMALL_HOST = "http://127.0.0.1:18081"
DEFAULT_LLAMACPP_MAIN_MODEL = "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
DEFAULT_LLAMACPP_SMALL_MODEL = "Qwen3.6-27B-Q4_0.gguf"


def _base_url(provider: str, llamacpp_host: str) -> str:
    provider_name = str(provider or "").strip().lower()
    if provider_name != "llama_cpp":
        raise ValueError("Only llama_cpp is supported by this branch.")
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
        "runtime_mode": "local",
        "routing_enabled": True,
        "cloud_fallback_enabled": False,
        "allow_small_to_main_fallback": True,
        "timeout_sec": 30,
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
            "fallback_on_error": False,
            "fallback_on_invalid_json": False,
            "tax_match_main_review_labels": ["non_compliant"],
            "tax_match_min_confidence": 0.65,
            "tax_match_max_workers": 2,
            "tax_risk_max_workers": 2,
            "entity_extract_max_workers": 4,
            "memory_clause_force_main_for_priority": False,
            "memory_flush_force_main": False,
        },
    }


def build_base_llm_config(api_base: str, model: str) -> dict:
    return {
        "provider": "llama_cpp",
        "api_base": api_base,
        "api_key": "",
        "model": model,
        "temperature": 0.2,
        "max_tokens": 2048,
        "timeout": 60,
        "headers": {},
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
        choices=["llama_cpp"],
        default="llama_cpp",
        help="Main local provider to activate.",
    )
    parser.add_argument(
        "--small-provider",
        choices=["llama_cpp"],
        default="llama_cpp",
        help="Provider used by the small model route.",
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
    main_api_base = _base_url(main_provider, args.llama_cpp_host)
    small_host = args.llama_cpp_host if args.single_instance else (
        args.small_llama_cpp_host or args.llama_cpp_host
    )
    small_api_base = _base_url(small_provider, small_host)
    main_model = args.main_model or DEFAULT_LLAMACPP_MAIN_MODEL
    small_model = (
        main_model
        if args.single_instance
        else (args.small_model or DEFAULT_LLAMACPP_SMALL_MODEL)
    )

    config["llm_config"] = build_base_llm_config(main_api_base, main_model)
    config["local_llm"] = build_local_llm_config(
        enabled=not args.disable,
        main_provider=main_provider,
        main_api_base=main_api_base,
        main_model=main_model,
        small_provider=small_provider,
        small_api_base=small_api_base,
        small_model=small_model,
    )

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
        print("Local LLM mode is now ACTIVE:")
        print(f"  Main model:  {main_api_base} ({main_model})")
        print(f"  Small model: {small_api_base} ({small_model})")
        print("  Cloud fallback: DISABLED")
        print("  Base llm_config: points to local llama.cpp main model")
        print()
        if single_instance:
            print(
                r"Next step: start the llama.cpp runtime with python .\bin\start-local-llamacpp-stack.py")
        else:
            print(
                r"Next step: start both local llama.cpp runtimes with python .\bin\start-local-llamacpp-stack.py --no-single-instance")
    else:
        print("[OK] Local routing disabled. Base llm_config remains local llama.cpp.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
