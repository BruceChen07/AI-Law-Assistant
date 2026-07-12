import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from deploy_desktop_lib import LOG_DIR, ROOT, load_json, save_json, setup_logger, utc_now, verify_sha256


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify GGUF models and llama.cpp runtime assets against deployment manifest."
    )
    parser.add_argument(
        "--manifest-path",
        default=str((ROOT / "deploy" / "desktop-deployment.manifest.json").resolve()),
        help="Deployment manifest path.",
    )
    parser.add_argument(
        "--asset-kind",
        choices=["models", "llama_cpp", "all"],
        default="all",
        help="Which asset group to verify.",
    )
    return parser.parse_args()


def _iter_assets(manifest: Dict[str, Any], asset_kind: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if asset_kind in {"models", "all"}:
        rows.extend(list(manifest.get("models") or []))
    if asset_kind in {"llama_cpp", "all"}:
        rows.extend(list((manifest.get("llama_cpp") or {}).get("runtime_bundles") or []))
    return rows


def main() -> int:
    args = parse_args()
    log_file = LOG_DIR / f"verify-assets-{utc_now().replace(':', '').replace('+00:00', 'Z')}.log"
    logger = setup_logger("verify_deployment_assets", log_file)
    manifest_path = Path(args.manifest_path).resolve()
    manifest = load_json(manifest_path)

    results = []
    for asset in _iter_assets(manifest, args.asset_kind):
        relative_path = str(asset.get("relative_path") or "").strip()
        target_path = (ROOT / relative_path).resolve()
        expected_sha = str(asset.get("sha256") or "").strip().lower()
        exists = target_path.exists()
        sha_ok = verify_sha256(target_path, expected_sha) if exists else False
        row = {
            "name": asset.get("name", target_path.name),
            "path": str(target_path),
            "exists": exists,
            "sha256_ok": sha_ok,
        }
        logger.info("verify asset name=%s exists=%s sha256_ok=%s", row["name"], exists, sha_ok)
        results.append(row)

    report = {
        "generated_at": utc_now(),
        "manifest_path": str(manifest_path),
        "asset_kind": args.asset_kind,
        "results": results,
        "ok": all(item["exists"] and item["sha256_ok"] for item in results),
    }
    report_path = ROOT / ".runtime" / "deploy" / "asset-verification-report.json"
    save_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
