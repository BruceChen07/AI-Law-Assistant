import argparse
import json
import sys
from pathlib import Path


def build_local_llm_config(enabled: bool) -> dict:
    return {
        "enabled": enabled,
        "routing_enabled": True,
        "cloud_fallback_enabled": False,
        "timeout_sec": 30,
        "json_repair_enabled": True,
        "main_model": {
            "provider": "ollama",
            "api_base": "http://127.0.0.1:11434/v1",
            "api_key": "",
            "model": "qwen3.6:27b",
            "temperature": 0.2,
            "max_tokens": 2048,
            "timeout": 30,
            "headers": {},
        },
        "small_model": {
            "provider": "ollama",
            "api_base": "http://127.0.0.1:11434/v1",
            "api_key": "",
            "model": "llama3.2:3b",
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
            "high_risk_force_cloud": False,
        },
        "execution": {
            "fallback_on_error": False,
            "fallback_on_invalid_json": False,
            "tax_match_cloud_review_labels": ["non_compliant"],
            "tax_match_min_confidence": 0.65,
            "tax_match_max_workers": 2,
            "tax_risk_max_workers": 2,
            "entity_extract_max_workers": 4,
            "memory_clause_force_cloud_for_priority": False,
            "memory_flush_force_cloud": False,
        },
    }


def build_base_llm_config() -> dict:
    return {
        "provider": "ollama",
        "api_base": "http://127.0.0.1:11434/v1",
        "api_key": "",
        "model": "qwen3.6:27b",
        "temperature": 0.2,
        "max_tokens": 2048,
        "timeout": 60,
        "headers": {},
    }


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Enable or disable local_llm configuration in app/config.json."
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

    config["llm_config"] = build_base_llm_config()
    config["local_llm"] = build_local_llm_config(enabled=not args.disable)

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
        print("Local LLM mode is now ACTIVE:")
        print("  Main model:  http://127.0.0.1:11434/v1 (qwen3.6:27b)")
        print("  Small model: http://127.0.0.1:11434/v1 (llama3.2:3b)")
        print("  Cloud fallback: DISABLED")
        print("  Base llm_config: points to local Ollama main model")
        print()
        print(r"Next step: python .\bin\download-local-llm-models.py")
    else:
        print("[OK] Local routing disabled. Base llm_config still points to local Ollama.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
