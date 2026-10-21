"""Contract source-file download endpoint."""
from __future__ import annotations

import os

from app.api.dependencies import get_current_user
from app.services.crud import get_document_by_id_for_user, get_latest_successful_document_digitization_by_document
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from app.api.routers.contracts._cfg import contracts_cfg

router = APIRouter()

@router.get("/contracts/{document_id}/source")
def download_contract_source(
    document_id: str,
    variant: str = Query("original"),
    current_user: dict = Depends(get_current_user),
    cfg: dict = Depends(contracts_cfg),
):
    doc = get_document_by_id_for_user(cfg, document_id, current_user["id"])
    if not doc:
        raise HTTPException(status_code=404, detail="document not found")
    source_variant = str(variant or "original").strip().lower()
    if source_variant not in {"original", "digitized"}:
        raise HTTPException(
            status_code=400, detail="variant must be original or digitized")

    if source_variant == "digitized":
        digitization_record = get_latest_successful_document_digitization_by_document(
            cfg, document_id
        )
        digitized_path = str(
            (digitization_record or {}).get("digitized_docx_path") or ""
        ).strip()
        if not digitized_path or not os.path.exists(digitized_path):
            raise HTTPException(
                status_code=404, detail="digitized word document not found")
        return FileResponse(
            digitized_path,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=f"contract_digitized_source_{document_id}.docx",
        )

    file_path = str(doc.get("file_path") or "")
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(
            status_code=404, detail="document file not found")
    mime_type = str(doc.get("mime_type") or "").strip() or None
    filename = str(doc.get("original_filename") or doc.get(
        "filename") or os.path.basename(file_path))
    return FileResponse(file_path, media_type=mime_type, filename=filename)
