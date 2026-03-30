import os
import uuid
import json
import logging
import threading
from datetime import datetime
from typing import Optional, Dict, Any
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends, Query, Body
from fastapi.responses import FileResponse
from app.api.dependencies import get_current_user
from app.services.crud import insert_document, insert_contract_audit, get_document_by_id_for_user, get_latest_contract_audit_by_document
from app.services.contract_audit import audit_contract
from app.services.contract_preview_assets import build_contract_preview_manifest, find_preview_page
from app.services.docx_renderer import render_tax_audit_docx
from app.services.audit_utils import _normalize_lang
from app.core.utils import extract_text_with_config
from app.core.config import get_config

logger = logging.getLogger("law_assistant")

_audit_progress: Dict[str, Dict[str, Any]] = {}
_audit_progress_lock = threading.Lock()


def _normalize_theme(v: Optional[str]) -> str:
    s = str(v or "").strip().lower()
    return "light" if s == "light" else "dark"


def _build_audit_error_detail(err: Exception, language: str) -> str:
    msg = str(err or "")
    low = msg.lower()
    is_zh = str(language or "zh").lower().startswith("zh")
    if "invalid_api_key" in low or "incorrect api key" in low or "authentication" in low or "401" in low:
        if is_zh:
            return "LLM 鉴权失败：API Key 无效或已过期。请前往【Admin -> 模型配置】更新 API Base、Model、API Key 后重试。"
        return "LLM authentication failed: API key is invalid or expired. Go to Admin -> Model Config and update API Base, Model, and API key, then retry."
    if "timeout" in low or "timed out" in low:
        if is_zh:
            return "LLM 请求超时。请在【Admin -> 模型配置】适当增大 timeout，或更换更稳定的模型后重试。"
        return "LLM request timed out. Increase timeout in Admin -> Model Config or switch to a more stable model, then retry."
    if "keyring" in low:
        if is_zh:
            return "本地安全密钥存储不可用。请安装 keyring 依赖或改用环境变量配置 LLM_API_KEY 后重试。"
        return "Local secure key storage is unavailable. Install keyring or use LLM_API_KEY environment variable, then retry."
    if is_zh:
        return "合同审计失败。请检查【Admin -> 模型配置】中的 LLM 参数后重试。"
    return "Contract audit failed. Check LLM settings in Admin -> Model Config and retry."


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


def build_router(cfg, llm, embedder=None, reranker=None, translator=None):
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
        language = _normalize_lang(language, default="zh")
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in [".docx", ".pdf"]:
            raise HTTPException(
                status_code=400, detail="unsupported file type, only docx and pdf are allowed")

        audit_id = str(audit_id or "").strip() or str(uuid.uuid4())
        _set_audit_progress(audit_id, "running", 5,
                            "received", "file received")

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

        _set_audit_progress(audit_id, "running", 20,
                            "document", "document recorded")
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
                translator=translator,
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
            detail = _build_audit_error_detail(e, language)
            _set_audit_progress(audit_id, "failed", 100, "failed", detail)
            insert_contract_audit(
                cfg,
                audit_id=audit_id,
                document_id=doc_id,
                status="failed",
                result_json=json.dumps(
                    {"error": str(e), "detail": detail}, ensure_ascii=False),
                model_provider=str(model_cfg.get("provider", "")),
                model_name=str(model_cfg.get("model", "")),
                created_at=datetime.utcnow().isoformat()
            )
            raise HTTPException(status_code=500, detail=detail)

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
            raise HTTPException(
                status_code=404, detail="audit progress not found")
        return progress

    @router.get("/contracts/ui-config")
    def get_contract_ui_config(current_user: dict = Depends(get_current_user)):
        _ = current_user
        cfg = get_config()
        ui_cfg = cfg.get("ui_config") if isinstance(
            cfg.get("ui_config"), dict) else {}
        return {
            "show_citation_source": bool(ui_cfg.get("show_citation_source", False)),
            "default_theme": _normalize_theme(ui_cfg.get("default_theme")),
            "preview_continuous_enabled": bool(ui_cfg.get("preview_continuous_enabled", False)),
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

    @router.post("/contracts/{document_id}/report/export")
    def export_contract_report(
        document_id: str,
        payload: Dict[str, Any] = Body(default={}),
        current_user: dict = Depends(get_current_user),
    ):
        doc = get_document_by_id_for_user(cfg, document_id, current_user["id"])
        if not doc:
            raise HTTPException(status_code=404, detail="document not found")
        audit_row = get_latest_contract_audit_by_document(
            cfg, document_id, status="done")
        if not audit_row:
            raise HTTPException(
                status_code=404, detail="contract audit result not found")
        try:
            audit = json.loads(str(audit_row.get("result_json") or "{}"))
        except Exception:
            audit = {}
        if not isinstance(audit, dict):
            audit = {}
        fmt = str((payload or {}).get("export_format") or "json").lower()
        if fmt not in {"json", "docx"}:
            raise HTTPException(
                status_code=400, detail="only json/docx export is supported")
        template_version = str((payload or {}).get(
            "template_version") or "v1.0")
        locale = str((payload or {}).get("locale") or "zh-CN")
        brand = str((payload or {}).get("brand") or "")
        risks = audit.get("risks") if isinstance(
            audit.get("risks"), list) else []
        citations = audit.get("citations") if isinstance(
            audit.get("citations"), list) else []
        citation_map = {str(c.get("citation_id") or ""): c for c in citations if isinstance(c, dict)}
        risk_summary = {"high": 0, "medium": 0, "low": 0}
        risk_items = []
        evidence_items = []
        for idx, r in enumerate(risks, start=1):
            if not isinstance(r, dict):
                continue
            level = str(r.get("level") or "medium").lower()
            level = level if level in {"high", "medium", "low"} else "medium"
            risk_summary[level] += 1
            location = r.get("location") if isinstance(
                r.get("location"), dict) else {}
            issue_id = f"r{idx}"
            citation_id = str(r.get("citation_id") or "")
            citation = citation_map.get(citation_id, {})
            issue_text = str(r.get("issue") or "")
            suggestion = str(r.get("suggestion") or "")
            evidence_text = str(r.get("evidence") or "")
            risk_items.append({
                "issue_id": issue_id,
                "risk_level": level,
                "issue_text": issue_text,
                "suggestion": suggestion,
                "reviewer_status": "pending",
                "reviewer_note": "",
                "clause": {
                    "clause_id": str(location.get("clause_id") or ""),
                    "clause_path": str(location.get("clause_path") or ""),
                    "clause_text": str(location.get("quote") or ""),
                    "page_no": int(location.get("page_no") or 0),
                    "paragraph_no": str(location.get("paragraph_no") or ""),
                },
                "rule": {
                    "rule_id": citation_id,
                    "law_title": str(citation.get("law_title") or r.get("law_title") or ""),
                    "article_no": str(citation.get("article_no") or r.get("article_no") or ""),
                    "source_text": str(citation.get("content") or citation.get("excerpt") or ""),
                },
            })
            if evidence_text:
                evidence_items.append({
                    "issue_id": issue_id,
                    "law_title": str(citation.get("law_title") or r.get("law_title") or ""),
                    "article_no": str(citation.get("article_no") or r.get("article_no") or ""),
                    "source_text": evidence_text,
                    "source_page": int(location.get("page_no") or 0),
                    "source_paragraph": str(location.get("paragraph_no") or ""),
                    "clause_id": str(location.get("clause_id") or ""),
                    "clause_path": str(location.get("clause_path") or ""),
                })
        report = {
            "contract_id": document_id,
            "generated_at": datetime.utcnow().isoformat(),
            "overview": {
                "contract_filename": doc.get("original_filename") or doc.get("filename") or "",
                "contract_parse_status": "done",
                "clause_count": 0,
                "issue_count": len(risk_items),
                "trace_count": 0,
            },
            "risk_summary": risk_summary,
            "review_summary": {"pending": len(risk_items), "confirmed": 0, "exception": 0},
            "risk_items": risk_items,
            "evidence_items": evidence_items,
            "review_conclusions": [],
            "exception_items": [],
        }
        report_dir = os.path.join(cfg["files_dir"], "contract_reports")
        os.makedirs(report_dir, exist_ok=True)
        ext = "json" if fmt == "json" else "docx"
        filename = f"contract_audit_report_{document_id}.{ext}"
        output_path = os.path.join(report_dir, filename)
        if fmt == "json":
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            media_type = "application/json"
        else:
            export_mode = str((payload or {}).get(
                "export_mode") or "report").lower()
            if export_mode == "comments":
                # Export original contract with comments (M2)
                from app.services.docx_modifier import insert_risk_comments
                import shutil
                original_file = str(doc.get("file_path") or "")
                if not original_file or not os.path.exists(original_file):
                    raise HTTPException(
                        status_code=404, detail="original contract file not found")
                # Insert comments into the original file
                insert_risk_comments(original_file, output_path, risk_items)
                filename = f"contract_with_comments_{document_id}.docx"
            else:
                # Export the standard audit report
                render_tax_audit_docx(
                    report, output_path, template_version=template_version, locale=locale, brand=brand)
                filename = f"contract_audit_report_{document_id}.docx"

            media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        return FileResponse(output_path, media_type=media_type, filename=filename)

    return router
