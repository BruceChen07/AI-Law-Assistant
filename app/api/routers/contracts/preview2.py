"""Contract document preview endpoint (registered after /source)."""
from __future__ import annotations

import os

from app.api.dependencies import get_current_user
from app.core.utils import extract_text_with_config
from app.services.crud import get_document_by_id_for_user
from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.routers.contracts._cfg import contracts_cfg

router = APIRouter()

@router.get("/contracts/{document_id}/preview")
def preview_contract_document(
    document_id: str,
    clause_limit: int = Query(2000, ge=1, le=10000),
    current_user: dict = Depends(get_current_user),
    cfg: dict = Depends(contracts_cfg),
):
    _ = clause_limit
    doc = get_document_by_id_for_user(cfg, document_id, current_user["id"])
    if not doc:
        raise HTTPException(status_code=404, detail="document not found")
    file_path = str(doc.get("file_path") or "")
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(
            status_code=404, detail="document file not found")
    text, meta = extract_text_with_config(cfg, file_path)
    return {
        "document_id": document_id,
        "filename": doc.get("original_filename") or doc.get("filename"),
        "mime_type": doc.get("mime_type"),
        "meta": {
            "text_length": len(text),
            "ocr_used": bool(meta.get("ocr_used")),
            "ocr_engine": meta.get("ocr_engine", ""),
            "page_count": int(meta.get("page_count") or 0),
            "line_total": len(text.splitlines()),
            "clause_total": 0,
            "preview_mode": "full_text",
        },
        "text": text,
        "clauses": [],
    }
