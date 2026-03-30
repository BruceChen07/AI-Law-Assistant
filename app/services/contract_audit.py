"""
Contract Audit Facade.
职责: 作为合同审计模块的统一入口，封装 IO 操作并调用拆分后的子模块。
"""
import time
import uuid
import structlog
from typing import Dict, Any, Optional, Callable

from app.core.utils import extract_text_with_config
from app.services.audit_utils import _safe_int, _normalize_citation_item
from app.services.audit_retrieval import _normalize_retrieval_options, _retrieve_regulation_evidence
from app.services.contract_audit_modules.clause_builder import build_preview_clauses
from app.services.contract_audit_modules import memory_pipeline as memory_pipeline_module
from app.services.contract_audit_modules.memory_pipeline import execute_memory_audit
from app.services.contract_audit_modules.result_assembler import attach_risk_locations
from app.services.contract_audit_modules.trace_writer import write_audit_trace, trace_clip
from app.memory_system.search import HybridSearcher

logger = structlog.get_logger(__name__)


def _build_preview_clauses(text: str):
    return build_preview_clauses(text)


def _attach_risk_locations(audit, clauses):
    return attach_risk_locations(audit, clauses)


def _get_memory_embedder(lang: str = "zh"):
    getter = getattr(memory_pipeline_module, "get_memory_embedder", None)
    if not callable(getter):
        return None
    try:
        return getter(lang)
    except TypeError:
        try:
            return getter()
        except Exception:
            return None
    except Exception:
        return None


def audit_contract(
    cfg: Dict[str, Any],
    llm,
    file_path: str,
    lang: str = "zh",
    embedder=None,
    reranker=None,
    translator=None,
    retrieval_options: Optional[Dict[str, Any]] = None,
    progress_cb: Optional[Callable[[str, int, str], None]] = None
) -> Dict[str, Any]:
    """
    A unified facade function for contract auditing.
    It integrates text extraction, clause preview, evidence retrieval, and LLM clause-level auditing with memory.
    It remains completely transparent to upper-level calls.
    """
    def _report(stage: str, percent: int, message: str = "") -> None:
        if not callable(progress_cb):
            return
        try:
            progress_cb(stage, percent, message)
        except Exception:
            return

    audit_started_at = time.perf_counter()
    audit_id = f"audit_{uuid.uuid4().hex[:12]}"
    _report("extracting", 15, "extracting text")
    logger.info("audit_extract_start", file=file_path,
                lang=lang, audit_id=audit_id)
    text, meta = extract_text_with_config(cfg, file_path)
    preview_clauses = build_preview_clauses(text)
    logger.info(
        "audit_extract_done",
        file=file_path,
        text_length=len(text),
        ocr_used=meta.get("ocr_used"),
        ocr_engine=meta.get("ocr_engine"),
        page_count=meta.get("page_count")
    )
    _report("extract_done", 30, "extract complete")
    opts = _normalize_retrieval_options(retrieval_options)
    _report("retrieval", 40, "retrieving evidence")
    retrieval_embedder = embedder
    if retrieval_embedder is None:
        try:
            retrieval_embedder = _get_memory_embedder(lang)
        except TypeError:
            retrieval_embedder = _get_memory_embedder()
    if translator is None:
        retrieved = _retrieve_regulation_evidence(
            cfg, text, lang, opts, embedder=retrieval_embedder, reranker=reranker)
    else:
        retrieved = _retrieve_regulation_evidence(
            cfg, text, lang, opts, embedder=retrieval_embedder, reranker=reranker, translator=translator)
    logger.info(
        "audit_retrieval_done",
        file=file_path,
        mode=opts.get("audit_mode"),
        used=retrieved.get("used"),
        queries=retrieved.get("queries"),
        success=retrieved.get("query_success", 0),
        failed=retrieved.get("query_failed", 0),
        evidence_count=len(retrieved.get("items") or []),
        degraded=retrieved.get("retrieval_degraded", False),
        degraded_reasons=retrieved.get("retrieval_degraded_reasons", []),
    )
    _report("retrieval_done", 55, "evidence ready")
    if opts.get("require_full_coverage") and _safe_int(retrieved.get("query_failed", 0), 0) > 0:
        raise RuntimeError("retrieval coverage incomplete")
    evidence_items = [_normalize_citation_item(
        it) for it in (retrieved.get("items") or [])]
    write_audit_trace(
        cfg,
        "contract_split",
        {
            "audit_id": audit_id,
            "file_path": file_path,
            "lang": lang,
            "text_length": len(text),
            "clause_count": len(preview_clauses),
            "clauses": [
                {
                    "clause_id": str(c.get("clause_id") or ""),
                    "clause_path": str(c.get("clause_path") or ""),
                    "page_no": int(c.get("page_no") or 0),
                    "paragraph_no": str(c.get("paragraph_no") or ""),
                    "text_len": len(str(c.get("clause_text") or "")),
                    "text_preview": trace_clip(c.get("clause_text"), 220),
                }
                for c in preview_clauses[:120]
            ],
        },
    )
    logger.info("audit_memory_enabled", file=file_path,
                clauses=len(preview_clauses))
    custom_embedder = None
    if callable(_get_memory_embedder):
        try:
            custom_embedder = _get_memory_embedder(lang)
        except TypeError:
            custom_embedder = _get_memory_embedder()
    if custom_embedder is not None and hasattr(custom_embedder, "encode"):
        memory_pipeline_module.get_memory_embedder = lambda _lang="zh": custom_embedder
    if HybridSearcher is not None:
        memory_pipeline_module.HybridSearcher = HybridSearcher
    _report("auditing", 70, "auditing clauses")
    memory_result = execute_memory_audit(
        cfg=cfg,
        llm=llm,
        text=text,
        lang=lang,
        preview_clauses=preview_clauses,
        evidence_items=evidence_items,
        retrieval_opts=opts,
        trace_context={
            "module": "contract_audit",
            "file_path": file_path,
            "audit_id": audit_id,
        },
    )
    _report("audit_done", 90, "audit complete")
    memory_meta = memory_result.get("meta") if isinstance(
        memory_result.get("meta"), dict) else {}
    citation_ids = [
        str(it.get("citation_id", "")).strip()
        for it in evidence_items
        if str(it.get("citation_id", "")).strip()
    ]
    audit_duration_ms = int((time.perf_counter() - audit_started_at) * 1000)
    output_meta = {
        "language": "en" if str(lang or "").lower() == "en" else "zh",
        "audit_id": audit_id,
        "text_length": len(text),
        "ocr_used": meta.get("ocr_used"),
        "ocr_engine": meta.get("ocr_engine"),
        "page_count": meta.get("page_count"),
        "llm_model": (cfg.get("llm_config") or {}).get("model", ""),
        "retrieval_mode": opts.get("audit_mode"),
        "risk_detection_mode": opts.get("risk_detection_mode"),
        "retrieval_used": retrieved.get("used"),
        "retrieval_queries": retrieved.get("queries"),
        "retrieval_chunk_total": retrieved.get("chunk_total", 0),
        "retrieval_query_success": retrieved.get("query_success", 0),
        "retrieval_query_failed": retrieved.get("query_failed", 0),
        "retrieval_degraded": bool(retrieved.get("retrieval_degraded", False)),
        "retrieval_degraded_reasons": list(retrieved.get("retrieval_degraded_reasons") or []),
        "retrieval_coverage": 0.0 if _safe_int(retrieved.get("chunk_total", 0), 0) == 0 else round(_safe_int(retrieved.get("query_success", 0), 0) / _safe_int(retrieved.get("chunk_total", 0), 0), 4),
        "retrieval_failed_chunks": retrieved.get("failed_chunks", []),
        "retrieved_chunks": len(evidence_items),
        "evidence_count": len(evidence_items),
        "citation_ids": citation_ids,
        "retrieval_filters": {
            "region": opts.get("region"),
            "date": opts.get("date"),
            "industry": opts.get("industry"),
            "tax_focus": opts.get("tax_focus")
        },
        "tax_focus": opts.get("tax_focus"),
        "require_full_coverage": opts.get("require_full_coverage"),
        "tax_evidence_count": len([
            it for it in evidence_items
            if _safe_int(it.get("tax_relevance", 0), 0) > 0
        ]),
        "preview_clause_total": len(preview_clauses),
        "audit_duration_ms": audit_duration_ms,
        **memory_meta,
    }
    write_audit_trace(
        cfg,
        "audit_done",
        {
            "audit_id": audit_id,
            "file_path": file_path,
            "duration_ms": audit_duration_ms,
            "preview_clause_total": len(preview_clauses),
            "memory_rounds": output_meta.get("memory_clause_rounds", 0),
            "memory_llm_call_count": output_meta.get("memory_llm_call_count", 0),
            "memory_llm_total_tokens": output_meta.get("memory_llm_total_tokens", 0),
            "risk_count": output_meta.get("memory_report_risk_count", 0),
            "suppressed_missing_risks": output_meta.get("suppressed_missing_risks", 0),
            "parse_failed_clauses": output_meta.get("parse_failed_clauses", 0),
        },
    )
    logger.info(
        "audit_metrics_done",
        file=file_path,
        duration_ms=audit_duration_ms,
        audit_id=audit_id,
        preview_clauses=len(preview_clauses),
        memory_rounds=output_meta.get("memory_clause_rounds", 0),
        llm_calls=output_meta.get("memory_llm_call_count", 0),
        llm_total_tokens=output_meta.get("memory_llm_total_tokens", 0),
        risks=output_meta.get("memory_report_risk_count", 0),
    )
    return {
        "audit": memory_result.get("audit"),
        "meta": output_meta,
        "raw": {"mode": "memory"}
    }
