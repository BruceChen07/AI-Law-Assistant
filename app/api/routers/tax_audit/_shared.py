"""Shared helpers for the tax-audit routes (extension check + upload save)."""
from __future__ import annotations

import hashlib
import os
import uuid

from fastapi import HTTPException, UploadFile

ALLOWED_TAX_AUDIT_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".png",
    ".jpg",
    ".jpeg",
    ".tif",
    ".tiff",
}


def _validate_extension(filename: str):
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in ALLOWED_TAX_AUDIT_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="unsupported file type, only pdf/doc/docx/xls/xlsx/images are allowed",
        )
    return ext


async def _save_upload(cfg: dict, file: UploadFile, prefix: str):
    ext = _validate_extension(file.filename)
    file_id = str(uuid.uuid4())
    cache_dir = os.path.join(cfg.get("data_dir", "./data"), "uploads", "cache")
    os.makedirs(cache_dir, exist_ok=True)
    save_path = os.path.join(cache_dir, f"{prefix}_{file_id}{ext}")
    content = await file.read()
    with open(save_path, "wb") as f:
        f.write(content)
    digest = hashlib.md5(content).hexdigest()

    # Also insert into new upload_log table
    from app.core.database import get_conn
    with get_conn(cfg) as conn:
        conn.execute(
            "INSERT INTO upload_log (file_id, original_filename, md5_hash, file_path, created_at, updated_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (file_id, file.filename, digest, save_path)
        )
        conn.commit()

    return file_id, save_path, len(content), ext, digest
