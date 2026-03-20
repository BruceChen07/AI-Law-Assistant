import os
import uuid
import json
import logging
import threading
from datetime import datetime
from typing import Optional, Dict, Any
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends, Query
from fastapi.responses import FileResponse
from app.api.dependencies import get_current_user
from app.services.crud import insert_document, insert_contract_audit, get_document_by_id_for_user
from app.services.contract_audit import audit_contract
from app.services.contract_preview_assets import build_contract_preview_manifest, find_preview_page
from app.core.utils import extract_text_with_config
from app.core.config import get_config

logger = logging.getLogger("law_assistant")

_audit_progress: Dict[str, Dict[str, Any]] = {}
_audit_progress_lock = threading.Lock()


def _normalize_theme(v: Optional[str]) -> str:
    s = str(v or "").strip().lower()
    return "light" if s == "light" else "dark"


def _set_audit_progress(audit_id: str, status: str, progress: int, stage: str, message: str = "") -> None:
    payload = {
        "audit_id": audit_id,
        "status": status,
        "progress": int(progress),
        "stage": stage,
        "message": message,
        "updated_at": datetime.utcnow().isoformat(),
    }
    with _audit_progress_lock:
        _audit_progress[audit_id] = payload


def _get_audit_progress(audit_id: str) -> Optional[Dict[str, Any]]:
    with _audit_progress_lock:
        return _audit_progress.get(audit_id)


def build_router(cfg, llm, embedder=None, reranker=None):
    router = APIRouter()

    @router.post("/contracts/audit")
    async def audit_contract_file(
        file: UploadFile = File(...),
        title: str = Form(""),
        language: str = Form("zh"),
        audit_mode: str = Form("rag"),
        risk_detection_mode: str = Form("relaxed"),
        region: str = Form(""),
        date: str = Form(""),
        industry: str = Form(""),
        tax_focus: str = Form("true"),
        audit_id: str = Form(""),
        use_semantic: Optional[str] = Form(None),
        semantic_weight: Optional[str] = Form(None),
        bm25_weight: Optional[str] = Form(None),
        candidate_size: Optional[str] = Form(None),
        rerank_enabled: Optional[str] = Form(None),
        rerank_top_n: Optional[str] = Form(None),
        rerank_mode: str = Form("on"),
        top_k_evidence: Optional[str] = Form(None),
        query_char_limit: Optional[str] = Form(None),
        contract_chunk_size: Optional[str] = Form(None),
        contract_chunk_max: Optional[str] = Form(None),
        per_chunk_top_k: Optional[str] = Form(None),
        current_user: dict = Depends(get_current_user),
    ):
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in [".docx", ".pdf"]:
            raise HTTPException(
                status_code=400, detail="unsupported file type, only docx and pdf are allowed")

        audit_id = str(audit_id or "").strip() or str(uuid.uuid4())
        _set_audit_progress(audit_id, "running", 5, "received", "file received")

        doc_id = str(uuid.uuid4())
        save_path = os.path.join(cfg["files_dir"], f"{doc_id}{ext}")
        with open(save_path, "wb") as f:
            f.write(await file.read())

        _set_audit_progress(audit_id, "running", 10, "saved", "file saved")

        file_size = os.path.getsize(save_path)
        doc_id = insert_document(
            cfg,
            doc_id=doc_id,
            filename=os.path.basename(save_path),
            original_filename=file.filename,
            file_path=save_path,
            file_size=file_size,
            mime_type=getattr(file, "content_type", None),
            user_id=current_user["id"],
            title=title or os.path.basename(file.filename),
            status="active",
            category="contract"
        )

        _set_audit_progress(audit_id, "running", 20, "document", "document recorded")
        model_cfg = cfg.get("llm_config") or {}
        logger.info(
            "contract_audit_start audit_id=%s document_id=%s file=%s size=%s lang=%s mode=%s risk_detection_mode=%s",
            audit_id,
            doc_id,
            save_path,
            file_size,
            language,
            audit_mode,
            risk_detection_mode
        )
        retrieval_options = {
            "audit_mode": audit_mode,
            "risk_detection_mode": risk_detection_mode,
            "region": region,
            "date": date,
            "industry": industry,
            "tax_focus": tax_focus,
            "use_semantic": use_semantic,
            "semantic_weight": semantic_weight,
            "bm25_weight": bm25_weight,
            "candidate_size": candidate_size,
            "rerank_enabled": rerank_enabled,
            "rerank_top_n": rerank_top_n,
            "rerank_mode": rerank_mode,
            "top_k_evidence": top_k_evidence,
            "query_char_limit": query_char_limit,
            "contract_chunk_size": contract_chunk_size,
            "contract_chunk_max": contract_chunk_max,
            "per_chunk_top_k": per_chunk_top_k,
        }
        def _progress_cb(stage: str, percent: int, message: str = "") -> None:
            _set_audit_progress(audit_id, "running", percent, stage, message)

        try:
            result = audit_contract(
                cfg,
                llm,
                file_path=save_path,
                lang=language,
                embedder=embedder,
                reranker=reranker,
                retrieval_options=retrieval_options,
                progress_cb=_progress_cb
            )
            insert_contract_audit(
                cfg,
                audit_id=audit_id,
                document_id=doc_id,
                status="done",
                result_json=json.dumps(result.get(
                    "audit"), ensure_ascii=False),
                model_provider=str(model_cfg.get("provider", "")),
                model_name=str(model_cfg.get("model", "")),
                created_at=datetime.utcnow().isoformat()
            )
            logger.info(
                "contract_audit_done audit_id=%s document_id=%s ocr_used=%s ocr_engine=%s page_count=%s text_length=%s",
                audit_id,
                doc_id,
                (result.get("meta") or {}).get("ocr_used"),
                (result.get("meta") or {}).get("ocr_engine"),
                (result.get("meta") or {}).get("page_count"),
                (result.get("meta") or {}).get("text_length")
            )
            _set_audit_progress(audit_id, "done", 100, "done", "completed")
        except Exception as e:
            logger.exception(
                "contract_audit_failed audit_id=%s document_id=%s file=%s",
                audit_id,
                doc_id,
                save_path
            )
            _set_audit_progress(audit_id, "failed", 100, "failed", str(e))
            insert_contract_audit(
                cfg,
                audit_id=audit_id,
                document_id=doc_id,
                status="failed",
                result_json=json.dumps({"error": str(e)}, ensure_ascii=False),
                model_provider=str(model_cfg.get("provider", "")),
                model_name=str(model_cfg.get("model", "")),
                created_at=datetime.utcnow().isoformat()
            )
            raise HTTPException(
                status_code=500, detail="contract audit failed")

        return {
            "audit_id": audit_id,
            "document_id": doc_id,
            "result": result.get("audit"),
            "meta": result.get("meta")
        }

    @router.get("/contracts/audit/{audit_id}/progress")
    def get_contract_audit_progress(audit_id: str, current_user: dict = Depends(get_current_user)):
        _ = current_user
        progress = _get_audit_progress(audit_id)
        if not progress:
            raise HTTPException(status_code=404, detail="audit progress not found")
        return progress

    @router.get("/contracts/ui-config")
    def get_contract_ui_config(current_user: dict = Depends(get_current_user)):
        _ = current_user
        cfg = get_config()
        ui_cfg = cfg.get("ui_config") if isinstance(cfg.get("ui_config"), dict) else {}
        return {
            "show_citation_source": bool(ui_cfg.get("show_citation_source", False)),
            "default_theme": _normalize_theme(ui_cfg.get("default_theme")),
        }

    @router.get("/contracts/{document_id}/preview-manifest")
    def get_contract_preview_manifest(
        document_id: str,
        current_user: dict = Depends(get_current_user),
    ):
        doc = get_document_by_id_for_user(cfg, document_id, current_user["id"])
        if not doc:
            raise HTTPException(status_code=404, detail="document not found")
        file_path = str(doc.get("file_path") or "")
        if not file_path or not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="document file not found")
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
            item["image_api"] = f"/contracts/{document_id}/preview/pages/{page_no}/image" if page_no > 0 and page.get("image_file") else ""
            pages.append(item)
        return {
            "document_id": document_id,
            "filename": doc.get("original_filename") or doc.get("filename"),
            "mime_type": doc.get("mime_type"),
            "mode": manifest.get("mode", "text"),
            "source": manifest.get("source", "text_fallback"),
            "meta": manifest.get("meta") or {},
            "pages": pages,
            "text": manifest.get("text", ""),
        }

    @router.get("/contracts/{document_id}/preview/pages/{page_no}/image")
    def get_contract_preview_page_image(
        document_id: str,
        page_no: int,
        current_user: dict = Depends(get_current_user),
    ):
        doc = get_document_by_id_for_user(cfg, document_id, current_user["id"])
        if not doc:
            raise HTTPException(status_code=404, detail="document not found")
        file_path = str(doc.get("file_path") or "")
        if not file_path or not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="document file not found")
        manifest = build_contract_preview_manifest(
            cfg=cfg,
            document_id=document_id,
            file_path=file_path,
            mime_type=str(doc.get("mime_type") or ""),
        )
        page = find_preview_page(manifest, page_no)
        image_file = str(page.get("image_file") or "")
        if not image_file or not os.path.exists(image_file):
            raise HTTPException(status_code=404, detail="preview image not found")
        return FileResponse(image_file, media_type="image/png")

    @router.get("/contracts/{document_id}/preview")
    def preview_contract_document(
        document_id: str,
        clause_limit: int = Query(2000, ge=1, le=10000),
        current_user: dict = Depends(get_current_user),
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

    return router
