import argparse
import json
import sys
from pathlib import Path


def build_local_llm_config(enabled: bool) -> dict:
    return {
        "enabled": enabled,
        "routing_enabled": True,
        "allow_small_to_main_fallback": True,
        "timeout_sec": 30,
        "json_repair_enabled": True,
        "main_model": {
            "provider": "openai_compatible",
            "api_base": "http://127.0.0.1:18081/v1",
            "api_key": "",
            "model": "qwen3-14b-instruct-awq",
            "temperature": 0.2,
            "max_tokens": 2048,
            "timeout": 30,
            "headers": {},
        },
        "small_model": {
            "provider": "openai_compatible",
            "api_base": "http://127.0.0.1:18082/v1",
            "api_key": "",
            "model": "qwen3-4b-instruct-awq",
            "temperature": 0.1,
            "max_tokens": 1024,
            "timeout": 20,
            "headers": {},
        },
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

    config["local_llm"] = build_local_llm_config(enabled=not args.disable)
    config["llm_config"] = {
        "provider": "openai_compatible",
        "api_base": "http://127.0.0.1:18081/v1",
        "api_key": "",
        "model": "qwen3-14b-instruct-awq",
        "temperature": 0.2,
        "max_tokens": 2048,
        "timeout": 60,
        "headers": {},
    }
    config["network_policy"] = {
        "enabled": True,
        "mode": "offline_strict",
        "allow_private_ip_ranges": True,
        "allowed_hosts": [
            "127.0.0.1",
            "localhost",
            "llm-gateway.intra",
            "ollama.intra",
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
        print()
        print("Enterprise offline mode is now ACTIVE:")
        print("  Main model:  http://127.0.0.1:18081/v1 (qwen3-14b-instruct-awq)")
        print("  Small model: http://127.0.0.1:18082/v1 (qwen3-4b-instruct-awq)")
        print("  Fallback policy: small -> main only; no public cloud route")
        print()
        print(r"Next step: stage local model assets and run python .\bin\download-local-llm-models.py --verify-only")
    else:
        print("[OK] Local LLM mode disabled. Base internal service route only.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
