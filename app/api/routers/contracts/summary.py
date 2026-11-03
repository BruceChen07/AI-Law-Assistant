"""Contract summary, UI config and pdf-pipeline summary endpoints."""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import uuid

from app.api.dependencies import get_app_llm, get_current_user
from app.core.config import get_config
from app.core.utils import extract_text_with_config
from app.services.audit_utils import _normalize_lang
from app.services.contract_audit import _build_contract_summary, _build_preview_clauses
from app.services.crud import get_document_by_id_for_user
from app.services.pdf_pipeline import build_pdf_pipeline_summary, get_pdf_pipeline_config, resolve_pdf_audit_source
from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.routers.contracts._cfg import contracts_cfg
from app.api.routers.contracts._error_specs import (
    _normalize_theme,
)

logger = logging.getLogger("law_assistant")

router = APIRouter()

_summary_generation_locks: dict = {}
_summary_generation_locks_guard = threading.Lock()


def _get_summary_generation_lock(key: str) -> threading.Lock:
    """Return the single-flight lock for one summary generation key.

    A page refresh can leave two identical summary requests in flight; without
    serialization both would call the LLM, and the slower empty response could
    replace the successful one in the UI.
    """
    with _summary_generation_locks_guard:
        lock = _summary_generation_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _summary_generation_locks[key] = lock
        return lock


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


@router.get("/contracts/pdf-pipeline/summary")
def get_contract_pdf_pipeline_summary(current_user: dict = Depends(get_current_user),
                                      cfg: dict = Depends(contracts_cfg),
                                      ):
    _ = current_user
    return build_pdf_pipeline_summary(cfg)


@router.get("/contracts/{document_id}/summary")
def get_contract_summary(
    document_id: str,
    language: str = Query(default="zh"),
    current_user: dict = Depends(get_current_user),
    llm=Depends(get_app_llm),
    cfg: dict = Depends(contracts_cfg),
):
    doc = get_document_by_id_for_user(cfg, document_id, current_user["id"])
    if not doc:
        raise HTTPException(status_code=404, detail="document not found")
    file_path = str(doc.get("file_path") or "")
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(
            status_code=404, detail="document file not found")

    source_filename = str(
        doc.get("original_filename") or doc.get(
            "filename") or f"{document_id}.docx"
    ).strip()
    audit_input_path = file_path
    ext = str(os.path.splitext(file_path)[1] or "").lower()
    pdf_pipeline_cfg = get_pdf_pipeline_config(cfg)
    if ext == ".pdf" and bool(pdf_pipeline_cfg.get("prefer_digitized_for_audit", True)):
        resolved_source = resolve_pdf_audit_source(
            cfg,
            file_path,
            document_id=document_id,
            original_filename=source_filename,
            prefer_digitized=True,
        )
        audit_input_path = str(resolved_source.get(
            "audit_input_path") or file_path)

    summary_audit_id = f"summary_{document_id}_{uuid.uuid4().hex[:12]}"
    norm_lang = _normalize_lang(str(language or "zh"), default="zh")
    preview_clauses = []
    source_sha256 = ""
    try:
        text, meta = extract_text_with_config(cfg, audit_input_path)
        preview_clauses = _build_preview_clauses(text)
        source_sha256 = hashlib.sha256(
            str(text or "").encode("utf-8")).hexdigest()
        summary_lock = _get_summary_generation_lock(
            f"{document_id}|{source_sha256}|{norm_lang}")
        if summary_lock.locked():
            logger.info(
                "contract_summary_waiting_for_inflight document_id=%s lang=%s",
                document_id,
                norm_lang,
            )
        with summary_lock:
            # The winner of the race writes the idempotent cache; the waiter
            # re-reads it inside _build_contract_summary instead of calling the
            # model a second time.
            summary_result = _build_contract_summary(
                cfg=cfg,
                llm=llm,
                text=text,
                lang=norm_lang,
                preview_clauses=preview_clauses,
                page_count=int(meta.get("page_count") or 0),
                audit_id=summary_audit_id,
                source_filename=source_filename,
                document_id=document_id,
                source_sha256=source_sha256,
            )
    except Exception as exc:
        logger.exception(
            "contract_summary_endpoint_failed document_id=%s file=%s",
            document_id,
            audit_input_path,
        )
        summary_result = {
            "enabled": bool(cfg.get("contract_summary_enabled", False)),
            "title": "",
            "about": "",
            "core": "",
            "tax": "",
            "source_file": source_filename,
            "status": "error",
            "source": "error",
            "error": str(exc),
        }

    summary_payload = {
        "title": str(summary_result.get("title") or "").strip(),
        "about": str(summary_result.get("about") or "").strip(),
        "core": str(summary_result.get("core") or "").strip(),
        "tax": str(summary_result.get("tax") or "").strip(),
        "source_file": str(summary_result.get("source_file") or "").strip(),
    }
    summary_chars = sum(len(v) for v in summary_payload.values())
    return {
        "document_id": document_id,
        "summary": summary_payload,
        "meta": {
            "contract_summary_enabled": bool(summary_result.get("enabled", False)),
            "contract_summary_status": str(summary_result.get("status") or ""),
            "contract_summary_chars": summary_chars,
            "contract_summary_source": str(summary_result.get("source") or ""),
            "contract_summary_prompt_id": str(summary_result.get("prompt_id") or ""),
            "contract_summary_error": str(summary_result.get("error") or ""),
            "contract_summary_language": norm_lang,
            "contract_summary_issues": list(summary_result.get("issues") or []),
            "contract_summary_truncated": bool(summary_result.get("truncated")),
            "contract_summary_source_chars": int(summary_result.get("source_chars") or 0),
            "contract_summary_cache_hit": bool(summary_result.get("cache_hit")),
            "contract_summary_map_reduce": bool(summary_result.get("map_reduce")),
            "contract_summary_map_chunks": int(summary_result.get("map_chunks") or 0),
            "preview_clause_total": len(preview_clauses),
        },
    }
