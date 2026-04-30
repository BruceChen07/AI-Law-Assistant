"""Cleanup helpers for memory pipeline debug artifacts."""

import os
import shutil
import time
from datetime import datetime
from typing import Dict, Any

import structlog

logger = structlog.get_logger(__name__)
_LAST_DEBUG_CLEANUP_AT: Dict[str, float] = {}


def cleanup_debug_tree(cfg: Dict[str, Any], base_dir: str) -> None:
    """Clean expired debug day-folders with optional archive."""
    root = os.path.abspath(str(base_dir or "").strip())
    if not root:
        return
    interval = max(
        60, int(cfg.get("contract_audit_debug_cleanup_interval_sec") or 1800))
    now_ts = time.time()
    last_ts = float(_LAST_DEBUG_CLEANUP_AT.get(root) or 0.0)
    if now_ts - last_ts < interval:
        return
    _LAST_DEBUG_CLEANUP_AT[root] = now_ts

    retention_days = max(
        1, int(cfg.get("contract_audit_debug_retention_days") or 7))
    archive_enabled = bool(
        cfg.get("contract_audit_debug_archive_before_delete", False))
    archive_dir = str(
        cfg.get("contract_audit_debug_archive_dir") or "").strip()
    archive_root = archive_dir if archive_dir else os.path.join(
        root, "_archive")
    if archive_enabled:
        os.makedirs(archive_root, exist_ok=True)

    now = datetime.utcnow()
    cutoff = now.toordinal() - retention_days
    if not os.path.isdir(root):
        return

    try:
        names = os.listdir(root)
    except Exception:
        return

    for name in names:
        path = os.path.join(root, name)
        if not os.path.isdir(path):
            continue
        try:
            day = datetime.strptime(name, "%Y-%m-%d")
        except Exception:
            continue
        if day.toordinal() <= cutoff:
            if archive_enabled:
                archive_base = os.path.join(
                    archive_root, f"{os.path.basename(root)}_{name}")
                try:
                    shutil.make_archive(
                        archive_base, "zip", root_dir=root, base_dir=name)
                except Exception as e:
                    logger.warning(
                        "cleanup_debug_archive_failed",
                        base_dir=root,
                        day=name,
                        error=str(e),
                    )
            shutil.rmtree(path, ignore_errors=True)
