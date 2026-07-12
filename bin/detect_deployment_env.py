import argparse
import json
from pathlib import Path

from deploy_desktop_lib import LOG_DIR, detect_environment, detect_llamacpp_build_profile, save_json, setup_logger, utc_now


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Detect desktop deployment environment, GPU capabilities and recommended llama.cpp build profile."
    )
    parser.add_argument(
        "--json-out",
        default=str((repo_root / ".runtime" / "deploy" / "env-detection-report.json").resolve()),
        help="Output path for the JSON report.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    log_file = LOG_DIR / f"detect-env-{utc_now().replace(':', '').replace('+00:00', 'Z')}.log"
    logger = setup_logger("detect_deployment_env", log_file)
    env_info = detect_environment()
    env_info["llama_cpp_build_profile"] = detect_llamacpp_build_profile(env_info)

    output_path = Path(args.json_out).resolve()
    save_json(output_path, env_info)
    logger.info("environment detection report saved: %s", output_path)
    print(json.dumps(env_info, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
