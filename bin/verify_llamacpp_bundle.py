import argparse
import json
from pathlib import Path
from typing import Any, Dict, Optional

from deploy_desktop_lib import (
    LOG_DIR,
    ROOT,
    DeployError,
    detect_environment,
    detect_llamacpp_build_profile,
    http_ok,
    load_json,
    pick_runtime_bundle,
    run_command,
    save_json,
    setup_logger,
    tail_text_file,
    utc_now,
    verify_sha256,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify llama.cpp desktop runtime bundle, optional source checkout and live endpoint readiness."
    )
    parser.add_argument(
        "--manifest-path",
        default=str(
            (ROOT / "deploy" / "desktop-deployment.manifest.json").resolve()),
        help="Deployment manifest path.",
    )
    parser.add_argument(
        "--source-dir",
        default="",
        help="Optional official llama.cpp source checkout path for git commit verification.",
    )
    parser.add_argument(
        "--endpoint",
        default="",
        help="Optional running llama.cpp endpoint such as http://127.0.0.1:18080/v1/models.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=15,
        help="Subprocess timeout seconds.",
    )
    return parser.parse_args()


def _verify_source_checkout(source_dir: Optional[Path], manifest: Dict[str, Any], logger, timeout: int) -> Dict[str, Any]:
    if not source_dir:
        return {"checked": False, "ok": True, "reason": "source_dir_not_provided"}
    git_dir = source_dir / ".git"
    if not git_dir.exists():
        return {"checked": True, "ok": False, "reason": "source_dir_not_git_repo"}

    source_cfg = (manifest.get("llama_cpp") or {}).get("source") or {}
    expected_commit = str(source_cfg.get(
        "expected_commit") or "").strip().lower()
    expected_repo = str(source_cfg.get("repo") or "").strip().lower()
    current_commit = run_command(["git", "-C", str(source_dir), "rev-parse", "HEAD"],
                                 check=True, timeout=timeout, logger=logger).stdout.strip().lower()
    remote_url = run_command(["git", "-C", str(source_dir), "remote", "get-url", "origin"],
                             check=True, timeout=timeout, logger=logger).stdout.strip().lower()

    ok = True
    if expected_repo and expected_repo not in remote_url:
        ok = False
    if expected_commit and current_commit != expected_commit:
        ok = False
    return {
        "checked": True,
        "ok": ok,
        "expected_commit": expected_commit,
        "current_commit": current_commit,
        "expected_repo": expected_repo,
        "remote_url": remote_url,
    }


def main() -> int:
    args = parse_args()
    log_file = LOG_DIR / \
        f"verify-llamacpp-{utc_now().replace(':', '').replace('+00:00', 'Z')}.log"
    logger = setup_logger("verify_llamacpp_bundle", log_file)
    manifest_path = Path(args.manifest_path).resolve()
    manifest = load_json(manifest_path)

    env_info = detect_environment()
    build_profile = detect_llamacpp_build_profile(env_info)
    bundle = pick_runtime_bundle(manifest, env_info)
    if not bundle:
        raise DeployError(
            "no matching llama.cpp runtime bundle found in manifest")

    target_path = (ROOT / str(bundle.get("relative_path") or "")).resolve()
    binary_check = {
        "name": bundle.get("name", target_path.name),
        "path": str(target_path),
        "exists": target_path.exists(),
        "sha256_ok": verify_sha256(target_path, str(bundle.get("sha256") or "")),
    }

    help_check: Dict[str, Any] = {"checked": False,
                                  "ok": False, "stdout": "", "stderr": ""}
    if target_path.exists():
        result = run_command([str(target_path), "--help"],
                             check=False, timeout=args.timeout, logger=logger)
        help_check = {
            "checked": True,
            "ok": result.returncode == 0,
            "stdout": result.stdout[:2000],
            "stderr": result.stderr[:2000],
        }

    endpoint_check = {
        "checked": bool(args.endpoint),
        "ok": http_ok(args.endpoint, timeout=5) if args.endpoint else True,
        "endpoint": args.endpoint,
    }
    source_check = _verify_source_checkout(
        Path(args.source_dir).resolve() if args.source_dir else None,
        manifest,
        logger,
        args.timeout,
    )

    report = {
        "generated_at": utc_now(),
        "manifest_path": str(manifest_path),
        "environment": env_info,
        "recommended_build_profile": build_profile,
        "runtime_bundle": binary_check,
        "binary_help_check": help_check,
        "source_checkout_check": source_check,
        "endpoint_check": endpoint_check,
        "bundle_log_tail": tail_text_file(log_file),
    }
    report["ok"] = bool(binary_check["exists"] and binary_check["sha256_ok"]
                        and help_check["ok"] and source_check.get("ok", False) and endpoint_check["ok"])
    report_path = ROOT / ".runtime" / "deploy" / "llamacpp-verification-report.json"
    save_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
