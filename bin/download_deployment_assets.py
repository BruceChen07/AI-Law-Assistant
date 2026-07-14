import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from deploy_desktop_lib import (
    LOG_DIR,
    ROOT,
    DeployError,
    ensure_dir,
    extract_archive,
    is_allowed_download_url,
    load_json,
    merge_parts,
    request_with_resume,
    save_json,
    setup_logger,
    utc_now,
    verify_sha256,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download desktop deployment assets with retry, resume and SHA256 verification."
    )
    parser.add_argument(
        "--manifest-path",
        default=str(
            (ROOT / "deploy" / "desktop-deployment.manifest.json").resolve()),
        help="Deployment manifest path.",
    )
    parser.add_argument(
        "--asset-kind",
        choices=["models", "llama_cpp", "all"],
        default="all",
        help="Which asset group to process.",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Download retries per URL.",
    )
    return parser.parse_args()


def _download_single_asset(
    asset: Dict[str, Any],
    allowed_hosts: List[str],
    logger,
    retries: int,
) -> Dict[str, Any]:
    relative_path = str(asset.get("relative_path") or "").strip()
    if not relative_path:
        raise DeployError(f"asset relative_path missing: {asset}")
    target_path = (ROOT / relative_path).resolve()
    expected_sha = str(asset.get("sha256") or "").strip().lower()
    archive_cfg = asset.get("archive") if isinstance(
        asset.get("archive"), dict) else {}

    if target_path.exists() and verify_sha256(target_path, expected_sha):
        logger.info("asset already verified: %s", target_path)
        return {
            "name": asset.get("name", target_path.name),
            "path": str(target_path),
            "downloaded": False,
            "verified": True,
            "source": "already_staged",
        }

    # === Local archive priority: use bundled zip instead of downloading ===
    local_archive_path_str = str(archive_cfg.get("local_path") or "").strip()
    if local_archive_path_str:
        local_archive = (ROOT / local_archive_path_str).resolve()
        if local_archive.exists():
            archive_sha = str(archive_cfg.get("sha256") or "").strip().lower()
            if not archive_sha or verify_sha256(local_archive, archive_sha):
                logger.info("using local archive: %s", local_archive)
                extract_archive(local_archive, target_path.parent)
                if target_path.exists() and verify_sha256(target_path, expected_sha):
                    return {
                        "name": asset.get("name", target_path.name),
                        "path": str(target_path),
                        "downloaded": False,
                        "verified": True,
                        "source": "local_archive",
                    }
                raise DeployError(
                    f"local archive extracted but target binary verification failed: {target_path}"
                )
            logger.warning(
                "local archive SHA256 mismatch, will fall back to download: %s", local_archive
            )

    parts = asset.get("parts") if isinstance(asset.get("parts"), list) else []
    if parts:
        logger.info("downloading multipart asset: %s",
                    asset.get("name", target_path.name))
        downloaded_parts: List[Path] = []
        for index, part in enumerate(parts, start=1):
            if not isinstance(part, dict):
                raise DeployError(
                    f"invalid part item in asset {asset.get('name')}")
            part_urls = [str(item).strip() for item in (
                part.get("download_urls") or []) if str(item).strip()]
            if not part_urls:
                raise DeployError(
                    f"multipart asset part missing urls: {asset.get('name')}")
            for url in part_urls:
                if not is_allowed_download_url(url, allowed_hosts):
                    raise DeployError(f"download url host not allowed: {url}")
            part_name = str(part.get("filename")
                            or f"{target_path.name}.part{index}")
            part_path = target_path.parent / part_name
            for url in part_urls:
                try:
                    request_with_resume(
                        url, part_path, retries=retries, logger=logger)
                    break
                except Exception as exc:
                    logger.warning(
                        "part download failed url=%s err=%s", url, exc)
            if not part_path.exists():
                raise DeployError(
                    f"failed to fetch multipart asset part: {part_name}")
            part_sha = str(part.get("sha256") or "").strip().lower()
            if part_sha and not verify_sha256(part_path, part_sha):
                raise DeployError(
                    f"multipart asset sha256 mismatch: {part_path}")
            downloaded_parts.append(part_path)
        merge_parts(downloaded_parts, target_path)
    else:
        urls = [str(item).strip() for item in (
            asset.get("download_urls") or []) if str(item).strip()]
        if not urls and not target_path.exists():
            archive_urls = [str(item).strip() for item in (
                archive_cfg.get("download_urls") or []) if str(item).strip()]
            if not archive_urls:
                raise DeployError(
                    f"asset missing both local file and download urls: {asset.get('name')}")
        for url in urls:
            if not is_allowed_download_url(url, allowed_hosts):
                raise DeployError(f"download url host not allowed: {url}")
        if urls:
            ensure_dir(target_path.parent)
            for url in urls:
                try:
                    request_with_resume(
                        url, target_path, retries=retries, logger=logger)
                    break
                except Exception as exc:
                    logger.warning(
                        "asset download failed url=%s err=%s", url, exc)
        elif archive_cfg:
            archive_urls = [str(item).strip() for item in (
                archive_cfg.get("download_urls") or []) if str(item).strip()]
            for url in archive_urls:
                if not is_allowed_download_url(url, allowed_hosts):
                    raise DeployError(f"download url host not allowed: {url}")
            archive_name = str(archive_cfg.get(
                "filename") or f"{asset.get('name', target_path.name)}.archive")
            archive_path = target_path.parent / archive_name
            archive_expected_sha = str(
                archive_cfg.get("sha256") or "").strip().lower()
            if not archive_path.exists() or not verify_sha256(archive_path, archive_expected_sha):
                ensure_dir(archive_path.parent)
                for url in archive_urls:
                    try:
                        request_with_resume(
                            url, archive_path, retries=retries, logger=logger)
                        break
                    except Exception as exc:
                        logger.warning(
                            "archive download failed url=%s err=%s", url, exc)
            if not archive_path.exists():
                raise DeployError(
                    f"failed to fetch runtime archive for asset: {asset.get('name')}")
            if archive_expected_sha and not verify_sha256(archive_path, archive_expected_sha):
                raise DeployError(
                    f"archive sha256 verification failed: {archive_path}")
            extract_archive(archive_path, target_path.parent)

    if not verify_sha256(target_path, expected_sha):
        raise DeployError(f"sha256 verification failed: {target_path}")

    return {
        "name": asset.get("name", target_path.name),
        "path": str(target_path),
        "downloaded": True,
        "verified": True,
    }


def _model_assets(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = manifest.get("models")
    return list(rows) if isinstance(rows, list) else []


def _llamacpp_assets(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = (manifest.get("llama_cpp") or {}).get("runtime_bundles")
    return list(rows) if isinstance(rows, list) else []


def main() -> int:
    args = parse_args()
    log_file = LOG_DIR / \
        f"download-assets-{utc_now().replace(':', '').replace('+00:00', 'Z')}.log"
    logger = setup_logger("download_deployment_assets", log_file)
    manifest_path = Path(args.manifest_path).resolve()
    manifest = load_json(manifest_path)
    allowed_hosts = [str(item).strip() for item in (
        manifest.get("allowed_download_hosts") or []) if str(item).strip()]

    assets: List[Dict[str, Any]] = []
    if args.asset_kind in {"models", "all"}:
        assets.extend(_model_assets(manifest))
    if args.asset_kind in {"llama_cpp", "all"}:
        assets.extend(_llamacpp_assets(manifest))

    report = {
        "generated_at": utc_now(),
        "manifest_path": str(manifest_path),
        "asset_kind": args.asset_kind,
        "results": [],
    }
    for asset in assets:
        result = _download_single_asset(
            asset, allowed_hosts, logger, args.retries)
        report["results"].append(result)

    report["ok"] = all(bool(item.get("verified"))
                       for item in report["results"])
    report_path = ROOT / ".runtime" / "deploy" / "download-report.json"
    save_json(report_path, report)
    logger.info("download report saved: %s", report_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
