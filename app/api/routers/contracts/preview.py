"""Contract preview manifest and page-image endpoints."""
from __future__ import annotations

import os

from app.api.dependencies import get_current_user
from app.services.contract_preview_assets import build_contract_preview_manifest, find_preview_page
from app.services.crud import get_document_by_id_for_user
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.api.routers.contracts._cfg import contracts_cfg

router = APIRouter()

@router.get("/contracts/{document_id}/preview-manifest")
def get_contract_preview_manifest(
    document_id: str,
    current_user: dict = Depends(get_current_user),
    cfg: dict = Depends(contracts_cfg),
):
    doc = get_document_by_id_for_user(cfg, document_id, current_user["id"])
    if not doc:
        raise HTTPException(status_code=404, detail="document not found")
    file_path = str(doc.get("file_path") or "")
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(
            status_code=404, detail="document file not found")
    manifest = build_contract_preview_manifest(
        cfg=cfg,
        document_id=document_id,
        file_path=file_path,
        mime_type=str(doc.get("mime_type") or ""),
    )
    pages = []
    for page in manifest.get("pages") or []:
        page_no = int(page.get("page_no") or 0)
        item = {k: v for k, v in page.items() if k != "image_file"}
        item["image_api"] = f"/contracts/{document_id}/preview/pages/{page_no}/image" if page_no > 0 and page.get(
            "image_file") else ""
        pages.append(item)
    return {
        "document_id": document_id,
        "filename": doc.get("original_filename") or doc.get("filename"),
        "mime_type": doc.get("mime_type"),
        "mode": manifest.get("mode", "text"),
        "source": manifest.get("source", "text_fallback"),
        "meta": manifest.get("meta") or {},
        "coord_unit": (manifest.get("meta") or {}).get("coord_unit", ""),
        "coord_origin": (manifest.get("meta") or {}).get("coord_origin", ""),
        "coord_provider": (manifest.get("meta") or {}).get("coord_provider", ""),
        "pages": pages,
        "text": manifest.get("text", ""),
    }


@router.get("/contracts/{document_id}/preview/pages/{page_no}/image")
def get_contract_preview_page_image(
    document_id: str,
    page_no: int,
    current_user: dict = Depends(get_current_user),
    cfg: dict = Depends(contracts_cfg),
):
    if page_no <= 0:
        raise HTTPException(status_code=400, detail="invalid page number")
    doc = get_document_by_id_for_user(cfg, document_id, current_user["id"])
    if not doc:
        raise HTTPException(status_code=404, detail="document not found")
    file_path = str(doc.get("file_path") or "")
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(
            status_code=404, detail="document file not found")
    manifest = build_contract_preview_manifest(
        cfg=cfg,
        document_id=document_id,
        file_path=file_path,
        mime_type=str(doc.get("mime_type") or ""),
    )
    try:
        page = find_preview_page(manifest, page_no)
    except ValueError:
        raise HTTPException(
            status_code=404, detail="preview page not found")
    image_file = str(page.get("image_file") or "")
    if not image_file or not os.path.exists(image_file):
        raise HTTPException(
            status_code=404, detail="preview image not found")
    return FileResponse(image_file, media_type="image/png")
