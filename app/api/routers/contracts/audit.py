"""Contract audit endpoints: run / rerun / progress."""
from __future__ import annotations

import json
import logging
import os
import uuid

from app.api.dependencies import get_app_embedder, get_app_llm, get_app_reranker, get_app_translator, get_current_user
from app.services.audit_orchestrator import AuditServices, run_contract_pipeline_bundle
from app.services.audit_utils import _normalize_lang
from app.services.contract_audit_stage_log import clear_contract_audit_log_context, register_contract_audit_log_context
from app.services.crud import get_document_by_id_for_user, insert_contract_audit, insert_document
from app.services.pdf_pipeline import get_pdf_pipeline_config, resolve_pdf_audit_source
from datetime import datetime, timezone
from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Request, UploadFile
from typing import Any, Dict, Optional

from app.api.routers.contracts._cfg import contracts_cfg
from app.api.routers.contracts._progress import (
    _get_audit_progress,
    _make_audit_progress_writer,
)
from app.api.routers.contracts._support import (
    _build_digitization_meta,
    _resolve_request_ip,
    _build_retrieval_options,
)
from app.api.routers.contracts._error_specs import (
    _build_audit_error_payload,
)

logger = logging.getLogger("law_assistant")

router = APIRouter()

@router.post("/contracts/audit")
async def audit_contract_file(
    request: Request,
    file: UploadFile = File(...),
    title: str = Form(""),
    language: str = Form("zh"),
    audit_mode: str = Form("rag"),
    risk_detection_mode: str = Form("balanced"),
    region: str = Form(""),
    date: str = Form(""),
    industry: str = Form(""),
    tax_focus: str = Form("true"),
    audit_id: str = Form(""),
    use_semantic: Optional[str] = Form(None),
    semantic_weight: Optional[str] = Form(None),
    bm25_weight: Optional[str] = Form(None),
    candidate_size: Optional[str] = Form("25"),
    rerank_enabled: Optional[str] = Form(None),
    rerank_top_n: Optional[str] = Form("25"),
    rerank_mode: str = Form("on"),
    top_k_evidence: Optional[str] = Form(None),
    query_char_limit: Optional[str] = Form(None),
    contract_chunk_size: Optional[str] = Form(None),
    contract_chunk_max: Optional[str] = Form("5"),
    per_chunk_top_k: Optional[str] = Form("5"),
    current_user: dict = Depends(get_current_user),
    llm=Depends(get_app_llm),
    embedder=Depends(get_app_embedder),
    reranker=Depends(get_app_reranker),
    translator=Depends(get_app_translator),
    cfg: dict = Depends(contracts_cfg),
):
    language = _normalize_lang(language, default="zh")
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in [".docx", ".pdf"]:
        raise HTTPException(
            status_code=400, detail="unsupported file type, only docx and pdf are allowed")

    audit_id = str(audit_id or "").strip() or str(uuid.uuid4())
    doc_id = str(uuid.uuid4())
    contract_name = str(title or os.path.basename(
        file.filename or "")).strip() or f"contract-{doc_id}"
    operator_id = str(current_user.get("id") or "")
    register_contract_audit_log_context(
        audit_id,
        document_id=doc_id,
        contract_name=contract_name,
        operator_id=operator_id,
        ip_address=_resolve_request_ip(request),
        source_endpoint="/contracts/audit",
        extra={"entrypoint": "contracts.audit"},
    )
    update_progress = _make_audit_progress_writer(cfg, audit_id)
    update_progress("running", 5, "received", "file received")

    cache_dir = os.path.join(
        cfg.get("data_dir", "./data"), "uploads", "cache")
    os.makedirs(cache_dir, exist_ok=True)
    save_path = os.path.join(cache_dir, f"{doc_id}{ext}")
    with open(save_path, "wb") as f:
        f.write(await file.read())

    # Also insert into new upload_log table
    from app.core.database import get_conn
    with get_conn(cfg) as conn:
        conn.execute(
            "INSERT INTO upload_log (file_id, original_filename, file_path, created_at, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (doc_id, file.filename, save_path)
        )
        conn.commit()

    update_progress("running", 10, "saved", "file saved")

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
        title=contract_name,
        status="active",
        category="contract"
    )

    update_progress("running", 20, "document", "document recorded")
    model_cfg = cfg.get("llm_config") or {}
    try:
        audit_input_path = save_path
        digitization_record: Optional[Dict[str, Any]] = None
        pdf_pipeline_cfg = get_pdf_pipeline_config(cfg)
        if ext == ".pdf" and bool(pdf_pipeline_cfg.get("prefer_digitized_for_audit", True)):
            update_progress(
                "running",
                28,
                "digitizing",
                "digitizing pdf into editable word",
            )
            resolved_source = resolve_pdf_audit_source(
                cfg,
                save_path,
                document_id=doc_id,
                original_filename=file.filename,
                prefer_digitized=True,
            )
            digitization_record = resolved_source.get(
                "digitization_record")
            audit_input_path = str(
                resolved_source.get("audit_input_path") or save_path
            )
            update_progress(
                "running",
                34,
                "digitized",
                "digitized word ready",
            )
        logger.info(
            "contract_audit_start audit_id=%s document_id=%s file=%s audit_input=%s size=%s lang=%s mode=%s risk_detection_mode=%s",
            audit_id,
            doc_id,
            save_path,
            audit_input_path,
            file_size,
            language,
            audit_mode,
            risk_detection_mode
        )
        retrieval_options = _build_retrieval_options({
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
        })

        def _progress_cb(stage: str, percent: int, message: str = "") -> None:
            update_progress("running", percent, stage, message)

        services = AuditServices(
            llm=llm,
            embedder=embedder,
            reranker=reranker,
            translator=translator,
        )
        result = run_contract_pipeline_bundle(
            cfg,
            services,
            file_path=audit_input_path,
            preview_file_path=save_path,
            lang=language,
            retrieval_options=retrieval_options,
            progress_cb=_progress_cb
        )
        result_meta = result.get("meta") if isinstance(
            result.get("meta"), dict) else {}
        result_meta.update(
            {
                "source_document_ext": ext,
                "audit_input_ext": str(os.path.splitext(audit_input_path)[1] or "").lower(),
                **_build_digitization_meta(digitization_record),
            }
        )
        result["meta"] = result_meta
        insert_contract_audit(
            cfg,
            audit_id=audit_id,
            document_id=doc_id,
            status="done",
            result_json=json.dumps(result.get(
                "audit"), ensure_ascii=False),
            model_provider=str(model_cfg.get("provider", "")),
            model_name=str(model_cfg.get("model", "")),
            created_at=datetime.now(timezone.utc).isoformat()
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
        audit_summary = str(
            (result.get("audit") or {}).get("summary") or "").strip()
        update_progress(
            "done",
            100,
            "done",
            "completed",
            stage_result="success",
            audit_opinion=audit_summary,
            payload={
                "risk_summary": result.get("risk_summary") or {},
                "meta": result.get("meta") or {},
            },
        )
    except Exception as e:
        logger.exception(
            "contract_audit_failed audit_id=%s document_id=%s file=%s",
            audit_id,
            doc_id,
            save_path
        )
        error_payload = _build_audit_error_payload(e, language)
        progress_message = str(
            error_payload.get("user_message")
            or error_payload.get("message")
            or "audit failed"
        )
        update_progress(
            "failed",
            100,
            "failed",
            progress_message,
            stage_result=error_payload.get("code", "failed"),
            audit_opinion=progress_message,
            payload={"error_detail": error_payload},
        )
        insert_contract_audit(
            cfg,
            audit_id=audit_id,
            document_id=doc_id,
            status="failed",
            result_json=json.dumps(
                {"error": str(e), "detail": error_payload}, ensure_ascii=False),
            model_provider=str(model_cfg.get("provider", "")),
            model_name=str(model_cfg.get("model", "")),
            created_at=datetime.now(timezone.utc).isoformat()
        )
        raise HTTPException(status_code=500, detail=error_payload)
    finally:
        clear_contract_audit_log_context(audit_id)

    return {
        "audit_id": audit_id,
        "document_id": doc_id,
        "result": result.get("audit"),
        "meta": result.get("meta"),
        "risk_summary": result.get("risk_summary"),
    }


@router.post("/contracts/{document_id}/pipeline/run")
def rerun_contract_pipeline(
    request: Request,
    document_id: str,
    payload: Dict[str, Any] = Body(default={}),
    current_user: dict = Depends(get_current_user),
    llm=Depends(get_app_llm),
    embedder=Depends(get_app_embedder),
    reranker=Depends(get_app_reranker),
    translator=Depends(get_app_translator),
    cfg: dict = Depends(contracts_cfg),
):
    doc = get_document_by_id_for_user(cfg, document_id, current_user["id"])
    if not doc:
        raise HTTPException(status_code=404, detail="document not found")
    file_path = str(doc.get("file_path") or "")
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(
            status_code=404, detail="document file not found")

    language = _normalize_lang(
        str((payload or {}).get("language") or "zh"), default="zh")
    audit_id = str((payload or {}).get("audit_id")
                   or "").strip() or str(uuid.uuid4())
    contract_name = str(doc.get("title") or doc.get(
        "original_filename") or doc.get("filename") or document_id)
    register_contract_audit_log_context(
        audit_id,
        document_id=document_id,
        contract_name=contract_name,
        operator_id=str(current_user.get("id") or ""),
        ip_address=_resolve_request_ip(request),
        source_endpoint=f"/contracts/{document_id}/pipeline/run",
        extra={"entrypoint": "contracts.pipeline_rerun"},
    )
    update_progress = _make_audit_progress_writer(cfg, audit_id)
    update_progress("running", 5, "received", "pipeline rerun started")
    ext = str(os.path.splitext(file_path)[1] or "").lower()
    model_cfg = cfg.get("llm_config") or {}
    try:
        audit_input_path = file_path
        digitization_record: Optional[Dict[str, Any]] = None
        pdf_pipeline_cfg = get_pdf_pipeline_config(cfg)
        if ext == ".pdf" and bool(pdf_pipeline_cfg.get("prefer_digitized_for_audit", True)):
            update_progress(
                "running",
                18,
                "digitizing",
                "digitizing pdf into editable word",
            )
            resolved_source = resolve_pdf_audit_source(
                cfg,
                file_path,
                document_id=document_id,
                original_filename=str(
                    doc.get("original_filename") or doc.get("filename") or ""),
                prefer_digitized=True,
            )
            digitization_record = resolved_source.get(
                "digitization_record")
            audit_input_path = str(
                resolved_source.get("audit_input_path") or file_path
            )
            update_progress(
                "running",
                26,
                "digitized",
                "digitized word ready",
            )

        retrieval_options = _build_retrieval_options(payload or {})

        def _progress_cb(stage: str, percent: int, message: str = "") -> None:
            update_progress("running", percent, stage, message)

        services = AuditServices(
            llm=llm,
            embedder=embedder,
            reranker=reranker,
            translator=translator,
        )
        result = run_contract_pipeline_bundle(
            cfg,
            services,
            file_path=audit_input_path,
            preview_file_path=file_path,
            lang=language,
            retrieval_options=retrieval_options,
            progress_cb=_progress_cb,
        )
        result_meta = result.get("meta") if isinstance(
            result.get("meta"), dict) else {}
        result_meta.update(
            {
                "source_document_ext": ext,
                "audit_input_ext": str(os.path.splitext(audit_input_path)[1] or "").lower(),
                **_build_digitization_meta(digitization_record),
            }
        )
        result["meta"] = result_meta
        insert_contract_audit(
            cfg,
            audit_id=audit_id,
            document_id=document_id,
            status="done",
            result_json=json.dumps(result.get(
                "audit"), ensure_ascii=False),
            model_provider=str(model_cfg.get("provider", "")),
            model_name=str(model_cfg.get("model", "")),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        audit_summary = str(
            (result.get("audit") or {}).get("summary") or "").strip()
        update_progress(
            "done",
            100,
            "done",
            "completed",
            stage_result="success",
            audit_opinion=audit_summary,
            payload={
                "risk_summary": result.get("risk_summary") or {},
                "meta": result.get("meta") or {},
            },
        )
        return {
            "audit_id": audit_id,
            "document_id": document_id,
            "result": result.get("audit"),
            "meta": result.get("meta"),
            "risk_summary": result.get("risk_summary"),
        }
    except Exception as e:
        logger.exception(
            "contract_pipeline_rerun_failed audit_id=%s document_id=%s file=%s",
            audit_id,
            document_id,
            file_path,
        )
        error_payload = _build_audit_error_payload(e, language)
        progress_message = str(
            error_payload.get("user_message")
            or error_payload.get("message")
            or "audit failed"
        )
        update_progress(
            "failed",
            100,
            "failed",
            progress_message,
            stage_result=error_payload.get("code", "failed"),
            audit_opinion=progress_message,
            payload={"error_detail": error_payload},
        )
        insert_contract_audit(
            cfg,
            audit_id=audit_id,
            document_id=document_id,
            status="failed",
            result_json=json.dumps(
                {"error": str(e), "detail": error_payload}, ensure_ascii=False),
            model_provider=str(model_cfg.get("provider", "")),
            model_name=str(model_cfg.get("model", "")),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        raise HTTPException(status_code=500, detail=error_payload)
    finally:
        clear_contract_audit_log_context(audit_id)


@router.get("/contracts/audit/{audit_id}/progress")
def get_contract_audit_progress(audit_id: str, current_user: dict = Depends(get_current_user)):
    _ = current_user
    progress = _get_audit_progress(audit_id)
    if not progress:
        raise HTTPException(
            status_code=404, detail="audit progress not found")
    return progress
