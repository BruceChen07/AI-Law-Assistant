import os
import uuid
import json
import logging
import threading
import zipfile
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends, Query, Body
from fastapi.responses import FileResponse, JSONResponse
from app.api.dependencies import (
    get_current_user,
    get_app_llm,
    get_app_embedder,
    get_app_reranker,
    get_app_translator,
)
from app.services.crud import (
    insert_document,
    insert_contract_audit,
    get_document_by_id_for_user,
    get_latest_contract_audit_by_document,
    get_contract_audit_by_id,
    get_contract_audit_progress as get_contract_audit_progress_row,
    list_contract_audit_progress_by_document,
    list_audit_queue_items,
    list_contract_audit_progress_pending,
    list_contract_audits_paginated,
    list_running_contract_audit_progress,
    upsert_contract_audit_progress,
)
from app.services.audit_artifacts import (
    artifact_root,
    audit_dir,
    list_artifacts,
    rebuild_artifacts_from_db,
    resolve_artifact_path,
)
from app.services.audit_orchestrator import AuditServices
from app.services.audit_error_messages import (
    audit_error_http_status as _audit_error_http_status_impl,
    build_audit_error_detail as _build_audit_error_detail_impl,
)
from app.services import audit_task_runner
from app.services.contract_preview_assets import build_contract_preview_manifest, find_preview_page
from app.services.docx_renderer import render_tax_audit_docx
from app.services.export_filenames import (
    build_export_filename,
    export_fallback_stem,
    localized_export_suffix,
)
from app.services.audit_utils import _normalize_lang
from app.core.utils import extract_text_with_config
from app.services.contract_audit import build_preview_clauses
from app.core.config import get_config

logger = logging.getLogger("law_assistant")

_audit_progress: Dict[str, Dict[str, Any]] = {}
_audit_progress_lock = threading.Lock()




def _normalize_export_risk_ids(risk_items: list) -> list:
    """Give every exported risk a unique, traceable id.

    Chunk-level audits number their risks locally, so aggregated or legacy results
    can contain several "classic-r1" entries; without this the exported comments
    (and any report) cannot be traced back to one specific risk.
    """
    for index, item in enumerate(list(risk_items or []), start=1):
        if not isinstance(item, dict):
            continue
        location = item.get("location") if isinstance(
            item.get("location"), dict) else {}
        traceable_id = f"risk-{index:03d}"
        source_risk_id = str(
            location.get("risk_id") or item.get("risk_id") or item.get("issue_id") or "")
        location["risk_id"] = traceable_id
        if source_risk_id and source_risk_id != traceable_id:
            location["source_risk_id"] = source_risk_id
        item["location"] = location
        item["risk_id"] = traceable_id
        if not str(item.get("issue_id") or "").strip():
            item["issue_id"] = traceable_id
    return risk_items


def _enrich_comment_locators(cfg: Dict[str, Any], doc: Dict[str, Any], risk_items: list) -> None:
    """Backfill clause locators on export so comments anchor to real paragraphs.

    Results produced before the locator fix carry a chunk-local paragraph number and
    no clause text; re-deriving them from the source document makes old audits
    exportable with correct anchors (and no re-audit).
    """
    if not risk_items:
        return
    needs = [
        item for item in risk_items
        if not str(((item.get("location") or {}).get("clause_text")) or "").strip()
        or str(((item.get("location") or {}).get("locate_source")) or "") != "preview_clause"
    ]
    if not needs:
        return
    file_path = str(doc.get("file_path") or "")
    if not file_path or not os.path.exists(file_path):
        return
    try:
        text, _meta = extract_text_with_config(cfg, file_path)
        clause_index = {
            str(clause.get("clause_id") or ""): clause
            for clause in build_preview_clauses(text)
        }
    except Exception as exc:  # pragma: no cover - enrichment is best effort
        logger.warning(
            "export_comment_locator_enrich_failed file=%s err=%s", file_path, exc)
        return
    for item in needs:
        location = item.get("location") if isinstance(
            item.get("location"), dict) else {}
        clause = item.get("clause") if isinstance(
            item.get("clause"), dict) else {}
        clause_id = str(location.get("clause_id") or clause.get("clause_id") or "")
        parsed = clause_index.get(clause_id)
        if not parsed:
            continue
        location["clause_id"] = clause_id
        location["clause_text"] = str(parsed.get("clause_text") or "")
        location["clause_path"] = str(location.get("clause_path") or parsed.get("clause_path") or "")
        # Overwrite the (chunk-local) paragraph number with the parser-derived one.
        location["paragraph_no"] = str(parsed.get("paragraph_no") or "")
        location["page_no"] = int(parsed.get("page_no") or 0)
        location["locate_source"] = "preview_clause"
        item["location"] = location
        clause["clause_text"] = location["clause_text"]
        clause["clause_path"] = location["clause_path"]
        clause["paragraph_no"] = location["paragraph_no"]
        clause["page_no"] = location["page_no"]
        item["clause"] = clause

def _normalize_theme(v: Optional[str]) -> str:
    s = str(v or "").strip().lower()
    return "light" if s == "light" else "dark"


def _build_audit_error_detail(err: Exception, language: str) -> str:
    return _build_audit_error_detail_impl(err, language)


def _audit_error_http_status(err: Exception) -> int:
    return _audit_error_http_status_impl(err)

def _set_audit_progress(
    audit_id: str,
    status: str,
    progress: int,
    stage: str,
    message: str = "",
    detail: Optional[Dict[str, Any]] = None,
    *,
    cfg: Optional[Dict[str, Any]] = None,
    document_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> None:
    with _audit_progress_lock:
        previous = _audit_progress.get(audit_id) or {}
        next_detail = previous.get("detail") if isinstance(
            previous.get("detail"), dict) else {}
        if isinstance(detail, dict):
            next_detail = {**next_detail, **detail}
        payload = {
            "audit_id": audit_id,
            "status": status,
            "progress": int(progress),
            "stage": stage,
            "message": message,
            "updated_at": datetime.utcnow().isoformat(),
            "detail": next_detail,
        }
        _audit_progress[audit_id] = payload
    if isinstance(cfg, dict):
        try:
            upsert_contract_audit_progress(
                cfg,
                audit_id=audit_id,
                status="running" if status == "running" else status,
                stage=stage,
                percent=int(progress),
                message=message,
                detail=next_detail,
                document_id=document_id,
                user_id=user_id,
            )
        except Exception as exc:  # pragma: no cover - durability is best effort
            logger.warning(
                "audit_progress_persist_failed audit_id=%s error=%s", audit_id, exc)


def _audit_progress_from_row(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(row, dict) or not row:
        return None
    detail: Dict[str, Any] = {}
    raw_detail = row.get("detail_json") or ""
    if raw_detail:
        try:
            parsed = json.loads(raw_detail)
            if isinstance(parsed, dict):
                detail = parsed
        except Exception:
            detail = {}
    return {
        "audit_id": str(row.get("audit_id") or ""),
        "status": str(row.get("status") or "running"),
        "progress": int(row.get("percent") or 0),
        "stage": str(row.get("stage") or "working"),
        "message": str(row.get("message") or ""),
        "updated_at": str(row.get("updated_at") or ""),
        "detail": detail,
        "document_id": str(row.get("document_id") or ""),
        "chunks_total": int(row.get("chunks_total") or 0),
        "durable": True,
    }


def _load_audit_progress(
    cfg: Dict[str, Any], audit_id: str
) -> Optional[Dict[str, Any]]:
    """In-memory progress when this process owns the audit, else the durable row."""
    progress = _get_audit_progress(audit_id)
    try:
        row = get_contract_audit_progress_row(cfg, audit_id)
    except Exception as exc:  # pragma: no cover - lookup is best effort
        logger.warning(
            "audit_progress_load_failed audit_id=%s error=%s", audit_id, exc)
        row = None
    durable = _audit_progress_from_row(row) if row else None
    if not durable:
        return progress
    # The durable row is authoritative: background tasks update it directly, while
    # the in-memory map only covers the submit handshake (and can go stale).
    if progress:
        merged_detail = dict(progress.get("detail") or {})
        merged_detail.update(durable.get("detail") or {})
        durable["detail"] = merged_detail
        durable["document_id"] = str(
            durable.get("document_id") or progress.get("document_id") or "")
        durable["chunks_total"] = max(
            int(durable.get("chunks_total") or 0),
            int(progress.get("chunks_total") or 0),
        )
    return durable


def _get_audit_progress(audit_id: str) -> Optional[Dict[str, Any]]:
    with _audit_progress_lock:
        return _audit_progress.get(audit_id)


def _build_retrieval_options(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "audit_mode": payload.get("audit_mode", "rag"),
        "risk_detection_mode": payload.get("risk_detection_mode", "balanced"),
        "region": payload.get("region", ""),
        "date": payload.get("date", ""),
        "industry": payload.get("industry", ""),
        "tax_focus": payload.get("tax_focus", "true"),
        "use_semantic": payload.get("use_semantic"),
        "semantic_weight": payload.get("semantic_weight"),
        "bm25_weight": payload.get("bm25_weight"),
        "candidate_size": payload.get("candidate_size", "25"),
        "rerank_enabled": payload.get("rerank_enabled"),
        "rerank_top_n": payload.get("rerank_top_n", "25"),
        "rerank_mode": payload.get("rerank_mode", "on"),
        "top_k_evidence": payload.get("top_k_evidence"),
        "query_char_limit": payload.get("query_char_limit"),
        "contract_chunk_size": payload.get("contract_chunk_size"),
        "contract_chunk_max": payload.get("contract_chunk_max", "5"),
        "per_chunk_top_k": payload.get("per_chunk_top_k", "5"),
    }


def _extract_report_risk_items(report: Dict[str, Any]) -> list[Dict[str, Any]]:
    items = report.get("risk_items") if isinstance(report, dict) else []
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def build_router(cfg):
    router = APIRouter()

    @router.post("/contracts/audit")
    async def audit_contract_file(
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
    ):
        language = _normalize_lang(language, default="zh")
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in [".docx", ".pdf"]:
            raise HTTPException(
                status_code=400, detail="unsupported file type, only docx and pdf are allowed")

        audit_id = str(audit_id or "").strip() or str(uuid.uuid4())
        existing_task = get_contract_audit_progress_row(cfg, audit_id)
        if isinstance(existing_task, dict) and str(existing_task.get("status") or "") == "running":
            # Idempotency guard: never start a second audit for an id that is still running.
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "AUDIT_ALREADY_RUNNING",
                    "audit_id": audit_id,
                    "message": (
                        "该审计任务仍在运行，已阻止重复提交。页面会继续跟踪原任务进度。"
                        if str(language or "zh").lower().startswith("zh")
                        else "This audit is still running; the duplicate submission was blocked. The page will keep tracking the original task."
                    ),
                },
            )
        _set_audit_progress(audit_id, "running", 5,
                            "received", "file received",
                            cfg=cfg, user_id=current_user["id"])

        doc_id = str(uuid.uuid4())
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

        _set_audit_progress(audit_id, "running", 10, "saved", "file saved",
                            cfg=cfg, user_id=current_user["id"])

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

        # Same document already queued/running: keep one queue entry per contract and
        # let the caller track the existing audit instead of duplicating the work.
        existing_active_audit = audit_task_runner.active_audit_for_document(doc_id)
        if existing_active_audit:
            try:
                os.remove(save_path)
            except OSError:
                pass
            conflict_message = (
                "该合同已在排队或审计中，已阻止重复提交；页面会继续跟踪原任务。"
                if str(language or "zh").lower().startswith("zh")
                else "This contract is already queued or running; the duplicate submission was blocked and the page keeps tracking the original task."
            )
            _set_audit_progress(audit_id, "cancelled", 0, "rejected",
                                conflict_message, cfg=cfg, document_id=doc_id,
                                user_id=current_user["id"])
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "AUDIT_ALREADY_QUEUED",
                    "audit_id": existing_active_audit,
                    "message": conflict_message,
                },
            )

        _set_audit_progress(audit_id, "running", 20,
                            "document", "document recorded",
                            cfg=cfg, document_id=doc_id, user_id=current_user["id"])
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

        services = AuditServices(
            llm=llm,
            embedder=embedder,
            reranker=reranker,
            translator=translator,
        )
        # P0: return immediately, run the audit in a background task. A client
        # timeout, closed tab or dropped connection can no longer abort it.
        queue_info = audit_task_runner.submit(
            cfg=cfg,
            services=services,
            file_path=save_path,
            lang=language,
            retrieval_options=retrieval_options,
            audit_id=audit_id,
            document_id=doc_id,
            user_id=current_user["id"],
            title=title or os.path.basename(file.filename or ""),
        )

        return JSONResponse(
            status_code=202,
            content={
                "audit_id": audit_id,
                "document_id": doc_id,
                "status": "queued",
                "stage": "queued",
                "progress": 0,
                "mode": "async",
                "queue": queue_info,
            },
        )

    @router.post("/contracts/{document_id}/pipeline/run")
    def rerun_contract_pipeline(
        document_id: str,
        payload: Dict[str, Any] = Body(default={}),
        current_user: dict = Depends(get_current_user),
        llm=Depends(get_app_llm),
        embedder=Depends(get_app_embedder),
        reranker=Depends(get_app_reranker),
        translator=Depends(get_app_translator),
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
        existing_task = get_contract_audit_progress_row(cfg, audit_id)
        if isinstance(existing_task, dict) and str(existing_task.get("status") or "") == "running":
            # Idempotency guard: never start a second audit for an id that is still running.
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "AUDIT_ALREADY_RUNNING",
                    "audit_id": audit_id,
                    "message": (
                        "该审计任务仍在运行，已阻止重复提交。页面会继续跟踪原任务进度。"
                        if str(language or "zh").lower().startswith("zh")
                        else "This audit is still running; the duplicate submission was blocked. The page will keep tracking the original task."
                    ),
                },
            )
        existing_active_audit = audit_task_runner.active_audit_for_document(document_id)
        if existing_active_audit and existing_active_audit != audit_id:
            conflict_message = (
                "该合同已在排队或审计中，已阻止重复提交；页面会继续跟踪原任务。"
                if str(language or "zh").lower().startswith("zh")
                else "This contract is already queued or running; the duplicate submission was blocked and the page keeps tracking the original task."
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "AUDIT_ALREADY_QUEUED",
                    "audit_id": existing_active_audit,
                    "message": conflict_message,
                },
            )

        _set_audit_progress(audit_id, "running", 5,
                            "received", "pipeline rerun started",
                            cfg=cfg, document_id=document_id,
                            user_id=current_user["id"])

        retrieval_options = _build_retrieval_options(payload or {})
        services = AuditServices(
            llm=llm,
            embedder=embedder,
            reranker=reranker,
            translator=translator,
        )
        queue_info = audit_task_runner.submit(
            cfg=cfg,
            services=services,
            file_path=file_path,
            lang=language,
            retrieval_options=retrieval_options,
            audit_id=audit_id,
            document_id=document_id,
            user_id=current_user["id"],
            title=str(doc.get("title") or doc.get("original_filename") or ""),
        )
        return JSONResponse(
            status_code=202,
            content={
                "audit_id": audit_id,
                "document_id": document_id,
                "status": "queued",
                "stage": "queued",
                "progress": 0,
                "mode": "async",
                "queue": queue_info,
            },
        )

    @router.get("/contracts/audit/{audit_id}/progress")
    def get_contract_audit_progress(audit_id: str, current_user: dict = Depends(get_current_user)):
        _ = current_user
        progress = _load_audit_progress(cfg, audit_id)
        if not progress:
            raise HTTPException(
                status_code=404, detail="audit progress not found")
        return progress

    @router.get("/contracts/audit/{audit_id}/task")
    def get_contract_audit_task(audit_id: str, current_user: dict = Depends(get_current_user)):
        """Durable audit state: survives page reloads and backend restarts."""
        _ = current_user
        progress = _load_audit_progress(cfg, audit_id)
        record = get_contract_audit_by_id(cfg, audit_id)
        if not progress and not record:
            raise HTTPException(status_code=404, detail="audit not found")
        record = record or {}
        progress_meta = (progress or {}).get("detail") if isinstance(
            (progress or {}).get("detail"), dict) else {}
        result_meta = progress_meta.get("result_meta") if isinstance(
            progress_meta.get("result_meta"), dict) else {}
        result: Optional[Dict[str, Any]] = None
        if str(record.get("status") or "") == "done" and record.get("result_json"):
            try:
                parsed = json.loads(record["result_json"])
                result = parsed if isinstance(parsed, dict) else None
            except Exception:
                result = None
        return {
            "audit_id": audit_id,
            "status": str(record.get("status") or (progress or {}).get("status") or "unknown"),
            "progress": progress or {},
            "document_id": str(
                (progress or {}).get("document_id")
                or record.get("document_id")
                or ""
            ),
            "has_result": result is not None,
            "result": result,
            "meta": result_meta,
        }

    @router.get("/contracts/audit/queue")
    def get_contract_audit_queue(current_user: dict = Depends(get_current_user)):
        """Current FIFO queue: the running audit plus everything waiting behind it."""
        _ = current_user
        snapshot = audit_task_runner.queue_snapshot()
        snapshot["durable"] = [
            {
                "audit_id": str(row.get("audit_id") or ""),
                "document_id": str(row.get("document_id") or ""),
                "title": str(row.get("title") or ""),
                "status": str(row.get("status") or ""),
                "created_at": str(row.get("created_at") or ""),
            }
            for row in list_audit_queue_items(cfg, statuses=("queued", "running"))
        ]
        return snapshot

    @router.delete("/contracts/audit/queue/{audit_id}")
    def cancel_contract_audit_queue(
        audit_id: str,
        current_user: dict = Depends(get_current_user),
    ):
        """Cancel a queued audit; a running audit cannot be cancelled."""
        _ = current_user
        outcome = audit_task_runner.cancel(audit_id)
        if outcome == "running":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "AUDIT_ALREADY_RUNNING",
                    "audit_id": audit_id,
                    "message": "审计已开始执行，无法取消排队。",
                },
            )
        if outcome == "missing":
            raise HTTPException(status_code=404, detail="audit queue item not found")
        return {"audit_id": audit_id, "status": "cancelled"}

    @router.post("/contracts/audit/queue/{audit_id}/pin")
    def pin_contract_audit_queue(
        audit_id: str,
        current_user: dict = Depends(get_current_user),
    ):
        """Move a queued audit to the front of the queue (it runs next)."""
        _ = current_user
        outcome = audit_task_runner.pin(audit_id)
        if outcome == "running":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "AUDIT_ALREADY_RUNNING",
                    "audit_id": audit_id,
                    "message": "该审计已在执行，无法置顶排队。",
                },
            )
        if outcome == "missing":
            raise HTTPException(status_code=404, detail="audit queue item not found")
        snapshot = audit_task_runner.queue_snapshot()
        return {
            "audit_id": audit_id,
            "status": "queued",
            "pinned": True,
            "position": 1,
            "queue": snapshot,
        }

    @router.get("/contracts/audits/history")
    def list_contract_audit_history(
        limit: int = Query(30, ge=1, le=200),
        offset: int = Query(0, ge=0),
        status: str = Query("", description="done | failed | running | interrupted"),
        current_user: dict = Depends(get_current_user),
    ):
        """Paged audit history (DB rows + archive artifacts) for the history page."""
        _ = current_user
        status_filter = status.strip().lower()
        rows = list_contract_audits_paginated(
            cfg,
            limit=limit,
            offset=offset,
            status=status_filter or None,
        )
        pending = [] if status_filter and status_filter not in {"running", "interrupted"} else (
            list_contract_audit_progress_pending(cfg, limit=limit)
        )
        items: list[Dict[str, Any]] = []
        for row in rows:
            audit_id = str(row.get("audit_id") or "")
            risk_summary: Dict[str, int] = {}
            raw = row.get("result_json") or ""
            if raw:
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict) and isinstance(parsed.get("risk_summary"), dict):
                        risk_summary = {
                            str(k): int(v or 0)
                            for k, v in parsed["risk_summary"].items()
                        }
                except Exception:
                    risk_summary = {}
            artifacts = list_artifacts(cfg, audit_id)
            items.append(
                {
                    "audit_id": audit_id,
                    "document_id": str(row.get("document_id") or ""),
                    "filename": str(row.get("filename") or ""),
                    "status": str(row.get("status") or ""),
                    "created_at": str(row.get("created_at") or ""),
                    "risk_summary": risk_summary,
                    "risk_count": int(sum(risk_summary.values())),
                    "artifact_dir": audit_dir(cfg, audit_id),
                    "artifact_files": artifacts,
                    "has_result": bool(raw) and str(row.get("status") or "") == "done",
                }
            )
        for row in pending:
            audit_id = str(row.get("audit_id") or "")
            items.append(
                {
                    "audit_id": audit_id,
                    "document_id": str(row.get("document_id") or ""),
                    "filename": str(row.get("filename") or ""),
                    "status": str(row.get("status") or "running"),
                    "created_at": str(row.get("updated_at") or ""),
                    "risk_summary": {},
                    "risk_count": 0,
                    "percent": int(row.get("percent") or 0),
                    "message": str(row.get("message") or ""),
                    "artifact_dir": audit_dir(cfg, audit_id),
                    "artifact_files": list_artifacts(cfg, audit_id),
                    "has_result": False,
                }
            )
        items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return {
            "total": len(items),
            "limit": limit,
            "offset": offset,
            "artifact_root": artifact_root(cfg),
            "items": items[:limit],
        }

    @router.get("/contracts/audits/{audit_id}/artifacts")
    def list_contract_audit_artifacts(
        audit_id: str,
        current_user: dict = Depends(get_current_user),
    ):
        _ = current_user
        if not get_contract_audit_by_id(cfg, audit_id) and not get_contract_audit_progress_row(cfg, audit_id):
            raise HTTPException(status_code=404, detail="audit not found")
        return {
            "audit_id": audit_id,
            "artifact_dir": audit_dir(cfg, audit_id),
            "files": list_artifacts(cfg, audit_id),
        }

    @router.post("/contracts/audits/{audit_id}/artifacts/rebuild")
    def rebuild_contract_audit_artifacts(
        audit_id: str,
        current_user: dict = Depends(get_current_user),
    ):
        _ = current_user
        written = rebuild_artifacts_from_db(cfg, audit_id)
        if not written:
            raise HTTPException(status_code=404, detail="audit not found")
        return {
            "audit_id": audit_id,
            "artifact_dir": written.get("dir", ""),
            "files": list_artifacts(cfg, audit_id),
        }

    @router.get("/contracts/audits/{audit_id}/artifacts/{name}")
    def get_contract_audit_artifact(
        audit_id: str,
        name: str,
        current_user: dict = Depends(get_current_user),
    ):
        _ = current_user
        path = resolve_artifact_path(cfg, audit_id, name)
        if not path:
            raise HTTPException(status_code=404, detail="artifact not found")
        media = "application/json" if name.endswith(".json") else (
            "application/x-ndjson" if name.endswith(".jsonl") else "text/markdown")
        return FileResponse(path, media_type=f"{media}; charset=utf-8", filename=f"{audit_id}-{name}")

    @router.get("/contracts/audits/running")
    def list_running_contract_audits(
        limit: int = Query(20, ge=1, le=100),
        current_user: dict = Depends(get_current_user),
    ):
        """Server-side view of audits that are still running (or stalled)."""
        _ = current_user
        rows = list_running_contract_audit_progress(cfg, limit)
        now = datetime.utcnow()
        items = []
        for row in rows:
            updated_at = str(row.get("updated_at") or "")
            stale_seconds = 0
            try:
                parsed = datetime.fromisoformat(updated_at.replace("Z", ""))
                stale_seconds = max(0, int((now - parsed).total_seconds()))
            except Exception:
                stale_seconds = 0
            items.append({**row, "stale_seconds": stale_seconds})
        return {
            "total": len(items),
            "stale_after_seconds": 300,
            "items": items,
        }

    @router.get("/contracts/{document_id}/audits")
    def list_contract_document_audits(
        document_id: str,
        limit: int = Query(10, ge=1, le=50),
        current_user: dict = Depends(get_current_user),
    ):
        doc = get_document_by_id_for_user(cfg, document_id, current_user["id"])
        if not doc:
            raise HTTPException(status_code=404, detail="document not found")
        items = list_contract_audit_progress_by_document(cfg, document_id, limit)
        return {"document_id": document_id, "items": items}

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
        final_report = audit.get("final_report") if isinstance(
            audit.get("final_report"), dict) else {}
        fmt = str((payload or {}).get("export_format") or "json").lower()
        if fmt not in {"json", "docx"}:
            raise HTTPException(
                status_code=400, detail="only json/docx export is supported")
        template_version = str((payload or {}).get(
            "template_version") or "v1.0")
        locale = str((payload or {}).get("locale") or "zh-CN")
        brand = str((payload or {}).get("brand") or "")
        if final_report:
            report = dict(final_report)
            risk_items = list(report.get("risk_items") or [])
            overview = report.get("overview") if isinstance(
                report.get("overview"), dict) else {}
            if not str(overview.get("contract_filename") or "").strip():
                overview["contract_filename"] = doc.get(
                    "original_filename") or doc.get("filename") or ""
            report["overview"] = overview
            report["contract_id"] = str(
                report.get("contract_id") or document_id)
        else:
            risks = audit.get("risks") if isinstance(
                audit.get("risks"), list) else []
            citations = audit.get("citations") if isinstance(
                audit.get("citations"), list) else []
            citation_map = {str(c.get("citation_id") or "")
                                : c for c in citations if isinstance(c, dict)}
            risk_summary = {"high": 0, "medium": 0, "low": 0}
            risk_items = []
            evidence_items = []
            for idx, r in enumerate(risks, start=1):
                if not isinstance(r, dict):
                    continue
                level = str(r.get("level") or "medium").lower()
                level = level if level in {
                    "high", "medium", "low"} else "medium"
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
                    "location": location,
                    "clause": {
                        "clause_id": str(location.get("clause_id") or ""),
                        "clause_path": str(location.get("clause_path") or ""),
                        # Real clause text (not the model's quote) so the exporter can
                        # anchor the comment even when the quote is a paraphrase.
                        "clause_text": str(
                            location.get("clause_text")
                            or location.get("quote") or ""),
                        "quote": str(location.get("quote") or ""),
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
        risk_items = _normalize_export_risk_ids(risk_items)
        report["risk_items"] = risk_items
        report_dir = os.path.join(cfg["files_dir"], "contract_reports")
        os.makedirs(report_dir, exist_ok=True)
        export_mode = str((payload or {}).get("export_mode") or "report").lower()
        comment_anchors: list[Dict[str, Any]] = []
        risk_items = _extract_report_risk_items(report)
        ext = "json" if fmt == "json" else "docx"
        source_filename = str(doc.get("original_filename")
                              or doc.get("filename") or "")
        # Timestamp the file with the export time so repeated exports stay distinct.
        generated_at = datetime.now(timezone.utc).isoformat()
        suffix = localized_export_suffix(
            "data" if fmt == "json" else "report", locale)
        filename = build_export_filename(
            source_filename=source_filename,
            generated_at=generated_at,
            suffix=suffix,
            ext=ext,
            fallback_stem=export_fallback_stem(locale),
        )
        output_path = os.path.join(report_dir, filename)
        if fmt == "json":
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            media_type = "application/json"
        else:
            if export_mode == "comments":
                # Export original contract with comments (M2)
                from app.services.docx_modifier import insert_risk_comments
                original_file = str(doc.get("file_path") or "")
                if not original_file or not os.path.exists(original_file):
                    raise HTTPException(
                        status_code=404, detail="original contract file not found")
                if not str(original_file).lower().endswith(".docx"):
                    raise HTTPException(
                        status_code=400,
                        detail="commented original export only supports docx source files",
                    )
                filename = build_export_filename(
                    source_filename=source_filename,
                    generated_at=generated_at,
                    suffix=localized_export_suffix("comments", locale),
                    ext="docx",
                    fallback_stem=export_fallback_stem(locale),
                )
                output_path = os.path.join(report_dir, filename)
                _enrich_comment_locators(cfg, doc, risk_items)
                try:
                    comment_anchors = insert_risk_comments(
                        original_file, output_path, risk_items, locale=locale) or []
                except zipfile.BadZipFile as exc:
                    logger.warning(
                        "export_commented_original_invalid_docx document_id=%s file=%s err=%s",
                        document_id,
                        original_file,
                        str(exc),
                    )
                    raise HTTPException(
                        status_code=400,
                        detail="original contract file is not a valid docx",
                    ) from exc
                except HTTPException:
                    raise
                except Exception as exc:
                    logger.exception(
                        "export_commented_original_failed document_id=%s file=%s",
                        document_id,
                        original_file,
                    )
                    raise HTTPException(
                        status_code=500,
                        detail=f"failed to generate commented original: {exc}",
                    ) from exc
            else:
                # Export the standard audit report
                output_path = os.path.join(report_dir, filename)
                render_tax_audit_docx(
                    report, output_path, template_version=template_version, locale=locale, brand=brand)

            media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        response_headers: Dict[str, str] = {}
        if export_mode == "comments" and comment_anchors:
            misses = sum(1 for item in comment_anchors if item.get("miss"))
            strategies = sorted({str(item.get("strategy") or "") for item in comment_anchors})
            hit = len(comment_anchors) - misses
            response_headers["X-Comment-Anchor"] = (
                f"total={len(comment_anchors)};anchored={hit};missed={misses};"
                f"strategies={','.join(strategies)}"
            )
            logger.info(
                "contract_export_comment_anchors document_id=%s total=%s anchored=%s missed=%s strategies=%s",
                document_id,
                len(comment_anchors),
                hit,
                misses,
                ",".join(strategies),
            )
            try:
                anchor_report_path = output_path + ".anchors.json"
                with open(anchor_report_path, "w", encoding="utf-8") as handle:
                    json.dump(comment_anchors, handle, ensure_ascii=False, indent=2)
            except Exception as anchor_exc:  # pragma: no cover - sidecar only
                logger.warning(
                    "contract_export_anchor_report_failed document_id=%s err=%s",
                    document_id, anchor_exc,
                )
        return FileResponse(
            output_path,
            media_type=media_type,
            filename=filename,
            headers=response_headers or None,
        )

    @router.post("/contracts/{document_id}/markdown/export")
    def export_contract_markdown(
        document_id: str,
        payload: Dict[str, Any] = Body(default={}),
        current_user: dict = Depends(get_current_user),
    ):
        """
        Export contract text as a structured Markdown file.
        Supports PDF, DOCX, TXT source formats.
        Preserves document structure: headings, clauses, paragraphs.
        """
        from app.services.markdown_export import export_contract_to_markdown

        doc = get_document_by_id_for_user(cfg, document_id, current_user["id"])
        if not doc:
            raise HTTPException(status_code=404, detail="document not found")

        file_path = str(doc.get("file_path") or "")
        if not file_path or not os.path.exists(file_path):
            raise HTTPException(
                status_code=404, detail="contract source file not found on disk")

        # Optional: custom output directory
        output_dir = str((payload or {}).get("output_dir") or "")
        if not output_dir:
            output_dir = os.path.join(cfg["files_dir"], "contract_markdown")
        os.makedirs(output_dir, exist_ok=True)

        source_name = str(doc.get("original_filename") or doc.get("filename") or "")
        language = _normalize_lang(
            str((payload or {}).get("locale") or (payload or {}).get("language") or "zh"),
            default="zh",
        )
        markdown_name = build_export_filename(
            source_filename=source_name,
            generated_at=datetime.now(timezone.utc).isoformat(),
            suffix=localized_export_suffix("markdown", language),
            ext="md",
            fallback_stem=export_fallback_stem(language),
        )
        output_path = os.path.join(output_dir, markdown_name)

        include_header = bool((payload or {}).get("include_metadata", True))

        result = export_contract_to_markdown(
            file_path=file_path,
            output_path=output_path,
            cfg=cfg,
            include_metadata_header=include_header,
        )

        if not result.get("success"):
            raise HTTPException(
                status_code=500,
                detail=f"Markdown export failed: {result.get('error', 'unknown error')}",
            )

        return FileResponse(
            result["output_path"],
            media_type="text/markdown; charset=utf-8",
            filename=os.path.basename(result["output_path"]),
        )

    return router
