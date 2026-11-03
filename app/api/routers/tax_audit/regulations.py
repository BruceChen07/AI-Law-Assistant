"""Tax regulation import / parse endpoints."""
from __future__ import annotations

from app.api.dependencies import get_current_user
from app.api.schemas import TaxAuditRegulationImportResponse, TaxAuditRegulationParseResponse
from app.services.crud import create_tax_regulation_document
from app.services.tax_parser import parse_regulation_document
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api.routers.tax_audit._cfg import tax_cfg
from app.api.routers.tax_audit._shared import (
    _save_upload,
)

router = APIRouter()

@router.post("/tax-audit/regulations/import", response_model=TaxAuditRegulationImportResponse)
async def import_tax_regulation(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
    cfg: dict = Depends(tax_cfg),
):
    doc_id, save_path, file_size, ext, digest = await _save_upload(cfg, file, "tax_reg")
    create_tax_regulation_document(
        cfg=cfg,
        document_id=doc_id,
        original_filename=file.filename,
        file_path=save_path,
        file_type=ext.lstrip("."),
        file_size=file_size,
        uploaded_by=current_user["id"],
        checksum=digest,
        parse_status="pending",
    )
    return TaxAuditRegulationImportResponse(document_id=doc_id, parse_status="pending")


@router.post("/tax-audit/regulations/{document_id}/parse", response_model=TaxAuditRegulationParseResponse)
def parse_tax_regulation(document_id: str, current_user: dict = Depends(get_current_user),
    cfg: dict = Depends(tax_cfg),
):
    try:
        parsed = parse_regulation_document(
            cfg,
            document_id=document_id,
            operator_id=current_user["id"],
        )
        return TaxAuditRegulationParseResponse(**parsed)
    except ValueError as e:
        msg = str(e)
        if "not found" in msg:
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
