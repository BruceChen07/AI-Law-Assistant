"""
Memory Pipeline.
Responsibilities: Coordinates the MemoryLifecycleManager.
Input/Output: Accepts contract configuration, clauses, and legal catalog, and returns audit results and metadata.
Exception Handling: Logs exceptions and skips clauses that fail, potentially throwing fallback exceptions.
"""
from app.services.contract_audit_modules.risk_suppression import (
    build_global_tax_context,
    format_global_tax_context,
)
from app.services.contract_audit_modules.trace_writer import write_audit_trace, memory_paths, write_round_trace
from app.services.contract_audit_modules.async_bridge import run_coro_sync
from app.services.contract_audit_modules.memory_pipeline.evidence_builder import (
    prepare_evidence_context,
)
from app.services.contract_audit_modules.memory_pipeline.risk_reconciliation import (
    process_report_risks,
)
from app.services.contract_audit_modules.memory_pipeline.prompt_templates import (
    format_workflow_memories,
    load_llm_json_object,
)
from app.services.contract_audit_modules.memory_pipeline.clause_iterator import (
    build_clause_priority_index,
)
from app.services.contract_audit_modules.memory_pipeline.callbacks import (
    create_memory_callbacks,
)
from app.memory_system.manager import MemoryLifecycleManager, MemoryManagerConfig
from app.memory_system.search import HybridSearcher, HybridSearchConfig
from app.memory_system.indexer import IndexerConfig, MemoryIndexer, SentenceTransformerEmbedder
from app.memory_system.rerank import rerank_memory_candidates, apply_context_budget
from app.services.audit_utils import _enrich_citations, _normalize_lang
from app.memory_system.experience_repo import recall_workflow_memories
from typing import Dict, Any, List, Optional
import os
import structlog
from datetime import datetime
from pathlib import Path
from app.core.utils import resolve_path

logger = structlog.get_logger(__name__)
_MEMORY_EMBEDDERS: Dict[str, Any] = {}


def _load_llm_json_object(raw_text: str) -> Dict[str, Any]:
    """Backward-compatible parser export (delegates to prompt_templates)."""
    return load_llm_json_object(raw_text)


def _memory_model_ref(cfg: Optional[Dict[str, Any]], lang: str) -> str:
    norm_lang = _normalize_lang(lang, default="zh")
    c = cfg or {}
    profile = c.get("embedding_profiles") if isinstance(
        c.get("embedding_profiles"), dict) else {}
    p = profile.get(norm_lang) if isinstance(
        profile.get(norm_lang), dict) else {}
    local_dir = str(
        c.get(f"memory_embedding_model_dir_{norm_lang}")
        or c.get("memory_embedding_model_dir")
        or p.get("embedding_tokenizer_dir")
        or ""
    ).strip()
    if local_dir:
        local_dir = resolve_path(local_dir)
        if local_dir and os.path.isdir(local_dir):
            return local_dir
    return "BAAI/bge-small-zh-v1.5" if norm_lang == "zh" else "BAAI/bge-small-en-v1.5"


def get_memory_embedder(lang: str = "zh", cfg: Optional[Dict[str, Any]] = None):
    global _MEMORY_EMBEDDERS
    norm_lang = _normalize_lang(lang, default="zh")
    model_ref = _memory_model_ref(cfg, norm_lang)
    force_local = bool((cfg or {}).get("memory_embedding_force_local", True))
    cache_key = f"{norm_lang}:{model_ref}:{int(force_local)}"
    if cache_key in _MEMORY_EMBEDDERS:
        return _MEMORY_EMBEDDERS[cache_key]
    try:
        if force_local:
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"
        local_only = force_local or os.path.isdir(str(model_ref))
        embedder = SentenceTransformerEmbedder(
            model_ref, local_files_only=local_only)
        _MEMORY_EMBEDDERS[cache_key] = embedder
        logger.info("memory_embedder_ready",
                    provider="sentence_transformers", lang=norm_lang, model=model_ref, local_only=local_only, force_local=force_local)
        return embedder
    except Exception as e:
        logger.warning("memory_embedder_fallback",
                       reason=str(e), lang=norm_lang)

        class _FallbackEmbedder:
            def encode(self, texts):
                import numpy as np
                out = []
                for text in texts:
                    vec = np.zeros(16, dtype=np.float32)
                    for i, ch in enumerate(str(text)[:256]):
                        vec[i % 16] += (ord(ch) % 37) / 37.0
                    norm = np.linalg.norm(vec) + 1e-12
                    out.append(vec / norm)
                return np.vstack(out) if out else np.zeros((0, 16), dtype=np.float32)
        embedder = _FallbackEmbedder()
        _MEMORY_EMBEDDERS[cache_key] = embedder
        return embedder


def execute_memory_audit(
    cfg: Dict[str, Any],
    llm,
    text: str,
    lang: str,
    preview_clauses: List[Dict[str, Any]],
    evidence_items: List[Dict[str, Any]],
    retrieval_opts: Dict[str, Any],
    trace_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Execute clause-level audit with memory."""
    norm_lang = _normalize_lang(lang, default="zh")
    memory_dir, memory_db = memory_paths(cfg)
    memory_enable_long_memory = bool(
        cfg.get("memory_enable_long_memory", True))
    memory_enable_short_memory = bool(
        cfg.get("memory_enable_short_memory", True))
    memory_use_long_hits = bool(
        cfg.get("memory_use_long_hits", True)) and memory_enable_long_memory
    logger.info("memory_pipeline_start", memory_dir=memory_dir,
                memory_db=memory_db, evidence=len(evidence_items), lang=norm_lang,
                enable_long_memory=memory_enable_long_memory, enable_short_memory=memory_enable_short_memory)

    embedder = get_memory_embedder(norm_lang, cfg=cfg)
    indexer = MemoryIndexer(
        IndexerConfig(memory_root=Path(memory_dir), db_path=Path(memory_db)),
        embedder
    )
    indexer.reindex_all()
    searcher = HybridSearcher(indexer, Path(memory_db), HybridSearchConfig(
        vector_weight=0.7, keyword_weight=0.3))

    timeout_sec = min(10.0, max(3.0, float(
        (cfg.get("llm_config") or {}).get("timeout", 8))))
    risk_detection_mode = str(retrieval_opts.get(
        "risk_detection_mode", "relaxed"))
    is_relaxed = risk_detection_mode == "relaxed"

    memory_cfg = MemoryManagerConfig(
        enable_short_memory=memory_enable_short_memory,
        enable_long_memory=memory_enable_long_memory,
        short_memory_token_limit=int(
            cfg.get("memory_short_token_limit") or (1800 if is_relaxed else 1600)),
        flush_soft_threshold=int(
            cfg.get("memory_flush_token_threshold") or (1600 if is_relaxed else 1400)),
        llm_timeout_sec=timeout_sec,
        retrieval_top_k=int(cfg.get("memory_retrieval_top_k")
                            or (3 if is_relaxed else 3)),
        risk_dedup_similarity_threshold=0.93 if is_relaxed else 0.86,
        risk_dedup_enabled=False,
        max_rounds=max(8, min(int(cfg.get("memory_max_rounds") or 28), 30)),
        clause_query_max_chars=int(
            cfg.get("memory_clause_query_max_chars") or 520),
        hit_item_max_chars=int(cfg.get("memory_hit_item_max_chars") or 200),
        short_ctx_turns=int(cfg.get("memory_short_ctx_turns") or 3),
        short_store_clause_chars=int(
            cfg.get("memory_short_store_clause_chars") or 240),
        short_store_risks_chars=int(
            cfg.get("memory_short_store_risks_chars") or 420),
        debug_retention_days=int(
            cfg.get("contract_audit_debug_retention_days") or 7),
        debug_cleanup_interval_sec=int(
            cfg.get("contract_audit_debug_cleanup_interval_sec") or 1800),
        debug_archive_before_delete=bool(
            cfg.get("contract_audit_debug_archive_before_delete", False)),
        debug_archive_dir=str(
            cfg.get("contract_audit_debug_archive_dir") or "").strip(),
    )

    manager = MemoryLifecycleManager(
        Path(memory_dir), indexer, searcher, memory_cfg)

    evidence_ctx = prepare_evidence_context(
        evidence_items=evidence_items,
        whitelist_limit=int(cfg.get("memory_whitelist_limit") or 36),
    )
    legal_catalog = evidence_ctx["legal_catalog"]
    citation_lookup = evidence_ctx["citation_lookup"]
    allowed_citation_ids = evidence_ctx["allowed_citation_ids"]
    evidence_by_cid = evidence_ctx["evidence_by_cid"]
    citation_id_casefold_map = evidence_ctx["citation_id_casefold_map"]
    article_citation_index = evidence_ctx["article_citation_index"]
    filter_unverifiable_risks = bool(
        cfg.get("memory_filter_unverifiable_risks", True))
    filtered_risk_log_limit = max(
        1, int(cfg.get("memory_filtered_risk_log_limit") or 30))
    evidence_whitelist_text = evidence_ctx["evidence_whitelist_text"]
    citation_alias_map: Dict[str, str] = {}
    global_tax_context = build_global_tax_context(preview_clauses)
    global_tax_context_text = format_global_tax_context(
        global_tax_context, per_topic_limit=2)

    trace_seed = dict(trace_context or {})
    seed_pack_id = str(trace_seed.get("regulation_pack_id") or "")
    workflow_top_k = max(1, min(int(cfg.get("memory_workflow_top_k") or 2), 3))
    workflow_memories = recall_workflow_memories(
        cfg=cfg,
        query_text=f"{str(text or '')[:800]}",
        top_k=workflow_top_k,
        regulation_pack_id=seed_pack_id,
        jurisdiction=str(retrieval_opts.get("region") or ""),
        industry=str(retrieval_opts.get("industry") or ""),
        contract_type=str(retrieval_opts.get("contract_type") or ""),
    )
    workflow_memories = rerank_memory_candidates(
        workflow_memories,
        query_text=f"{str(text or '')[:800]}",
        regulation_pack_id=seed_pack_id,
    )
    workflow_memories = apply_context_budget(
        workflow_memories,
        max_items=workflow_top_k,
        max_chars=max(
            160, int(cfg.get("memory_workflow_budget_chars") or 420)),
    )
    workflow_memory_block = format_workflow_memories(
        workflow_memories, norm_lang)
    clause_priority_index = build_clause_priority_index(preview_clauses)
    preview_order_map: Dict[str,
                            int] = clause_priority_index["preview_order_map"]
    preview_priority_orders: List[int] = clause_priority_index["preview_priority_orders"]
    preview_priority_clause_ids = clause_priority_index["preview_priority_clause_ids"]

    clause_parse_state = {"count": 0, "clause_ids": []}
    base_trace_meta = dict(trace_context or {})
    audit_id = str(base_trace_meta.get("audit_id") or "").strip()
    if not audit_id:
        audit_id = f"audit_{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
    base_trace_meta["audit_id"] = audit_id
    round_runtime = {"round": 0, "clause_id": ""}
    runtime_cfg = cfg.get("memory_runtime_config") if isinstance(
        cfg.get("memory_runtime_config"), dict) else {}
    llm_call_budget_limit = int(
        runtime_cfg.get("memory_max_llm_calls_per_audit")
        or cfg.get("memory_max_llm_calls_per_audit")
        or 12
    )
    llm_call_budget_limit = max(1, min(llm_call_budget_limit, 200))
    llm_budget = {
        "limit": llm_call_budget_limit,
        "calls": 0,
        "guard_hit": False,
        "skipped_clause_calls": 0,
        "skipped_flush_calls": 0,
        "skipped_low_priority_calls": 0,
        "called_high_priority_clauses": 0,
    }

    def _write_round(action: str, payload: Dict[str, Any], round_no: int = 0) -> None:
        rn = int(round_no or round_runtime.get("round") or 0)
        if rn <= 0:
            return
        row_payload = dict(payload or {})
        row_payload["audit_id"] = audit_id
        write_round_trace(cfg, rn, action, row_payload, memory_dir=memory_dir)

    write_audit_trace(
        cfg,
        "memory_pipeline_start",
        {
            **base_trace_meta,
            "memory_dir": memory_dir,
            "memory_db": memory_db,
            "memory_use_long_hits": memory_use_long_hits,
            "memory_enable_long_memory": memory_enable_long_memory,
            "memory_enable_short_memory": memory_enable_short_memory,
            "risk_detection_mode": risk_detection_mode,
            "audit_mode": str(retrieval_opts.get("audit_mode") or ""),
            "evidence_count": len(evidence_items),
            "zero_risk_fallback_enabled": bool(cfg.get("memory_zero_risk_fallback_enabled", True)),
            "global_tax_context_topics": len([k for k, v in global_tax_context.items() if isinstance(v, list) and v]),
            "global_tax_context_items": sum(len(v) for v in global_tax_context.values() if isinstance(v, list)),
            "memory_cfg": memory_cfg.model_dump(),
            "memory_filter_unverifiable_risks": filter_unverifiable_risks,
            "memory_llm_call_budget_limit": llm_call_budget_limit,
            "memory_priority_clause_count": len(preview_priority_orders),
        },
        memory_dir=memory_dir,
    )
    write_audit_trace(
        cfg,
        "workflow_memory_recall",
        {
            **base_trace_meta,
            "workflow_memory_count": len(workflow_memories),
            "workflow_memories_preview": workflow_memories,
            "workflow_memory_budget_chars": int(cfg.get("memory_workflow_budget_chars") or 420),
        },
        memory_dir=memory_dir,
    )
    _clause_cb, _flush_cb = create_memory_callbacks(
        cfg=cfg,
        llm=llm,
        norm_lang=norm_lang,
        lang=lang,
        is_relaxed=is_relaxed,
        memory_use_long_hits=memory_use_long_hits,
        evidence_whitelist_text=evidence_whitelist_text,
        workflow_memory_block=workflow_memory_block,
        workflow_memories=workflow_memories,
        global_tax_context_text=global_tax_context_text,
        base_trace_meta=base_trace_meta,
        retrieval_opts=retrieval_opts,
        memory_dir=memory_dir,
        preview_order_map=preview_order_map,
        preview_priority_orders=preview_priority_orders,
        preview_priority_clause_ids=preview_priority_clause_ids,
        llm_budget=llm_budget,
        clause_parse_state=clause_parse_state,
        citation_alias_map=citation_alias_map,
        round_runtime=round_runtime,
        write_round=_write_round,
    )

    report = run_coro_sync(manager.audit_contract(
        text, _clause_cb, _flush_cb, legal_catalog, audit_id=audit_id))

    risks = report.get("risks") if isinstance(
        report.get("risks"), list) else []
    clause_map = {str(c.get("clause_id")): c for c in preview_clauses}
    risk_bundle = process_report_risks(
        risks=risks,
        preview_clauses=preview_clauses,
        clause_map=clause_map,
        evidence_items=evidence_items,
        norm_lang=norm_lang,
        cfg=cfg,
        allowed_citation_ids=allowed_citation_ids,
        citation_lookup=citation_lookup,
        citation_alias_map=citation_alias_map,
        citation_id_casefold_map=citation_id_casefold_map,
        article_citation_index=article_citation_index,
        evidence_by_cid=evidence_by_cid,
        global_tax_context=global_tax_context,
    )
    normalized_risks = risk_bundle["normalized_risks"]
    risk_summary = risk_bundle["risk_summary"]
    dropped_non_whitelist = int(risk_bundle["dropped_non_whitelist"])
    retained_unmapped_risks = int(risk_bundle["retained_unmapped_risks"])
    retained_unmapped_risk_hits = list(
        risk_bundle["retained_unmapped_risk_hits"] or [])
    suppressed_missing_risks = int(risk_bundle["suppressed_missing_risks"])
    suppressed_missing_risk_hits = list(
        risk_bundle["suppressed_missing_risk_hits"] or [])
    reconciled_suppressed_risks = int(
        risk_bundle["reconciled_suppressed_risks"])
    reconciled_removed_hits = list(
        risk_bundle["reconciled_removed_hits"] or [])
    fallback_enabled = bool(risk_bundle["fallback_enabled"])
    fallback_generated_risks = int(risk_bundle["fallback_generated_risks"])
    fallback_generated_risk_hits = list(
        risk_bundle["fallback_generated_risk_hits"] or [])
    filtered_unverifiable_risks = int(
        risk_bundle["filtered_unverifiable_risks"])
    filtered_unverifiable_risk_hits = list(
        risk_bundle["filtered_unverifiable_risk_hits"] or [])
    dedup_similar_risks = bool(risk_bundle["dedupe_similar_risks"])
    deduped_similar_risks = int(risk_bundle["deduped_similar_risks"])
    deduped_similar_risk_hits = list(
        risk_bundle["deduped_similar_risk_hits"] or [])

    parse_failed_count = int(clause_parse_state.get("count") or 0)
    fail_on_parse_and_no_risk = bool(
        cfg.get("memory_fail_on_parse_and_no_risk", False))
    require_full_coverage = bool(
        retrieval_opts.get("require_full_coverage", False))
    if parse_failed_count > 0 and not normalized_risks:
        if fail_on_parse_and_no_risk or require_full_coverage:
            raise RuntimeError(
                "memory audit degraded: llm json parse failed and no risks produced")
        logger.warning(
            "memory_pipeline_degraded_no_risk_after_parse_fail",
            parse_failed_count=parse_failed_count,
            clause_ids=clause_parse_state.get("clause_ids", []),
            fail_on_parse_and_no_risk=fail_on_parse_and_no_risk,
            require_full_coverage=require_full_coverage,
        )

    legal_validation = report.get("legal_validation") if isinstance(
        report.get("legal_validation"), dict) else {"ok": True, "issues": []}
    if parse_failed_count > 0:
        issues = legal_validation.get("issues") if isinstance(
            legal_validation.get("issues"), list) else []
        issues = list(issues)
        issues.append({
            "risk_id": "pipeline",
            "message": f"LLM JSON parse failed in {parse_failed_count} clause(s): {','.join([x for x in clause_parse_state.get('clause_ids', []) if x])}"
        })
        legal_validation = {"ok": False, "issues": issues}

    report_summary = str(report.get("summary") or "").strip()
    if not report_summary:
        report_summary = f"条款级长短记忆审核完成，共发现 {len(normalized_risks)} 项风险"

    audit = {
        "summary": report_summary,
        "executive_opinion": [],
        "risk_summary": risk_summary,
        "risks": normalized_risks,
        "citations": _enrich_citations(evidence_items, evidence_items),
        "legal_validation": legal_validation,
    }

    write_audit_trace(
        cfg,
        "memory_pipeline_done",
        {
            **base_trace_meta,
            "risk_count": len(normalized_risks),
            "validation_ok": bool(legal_validation.get("ok")),
            "parse_failed_count": parse_failed_count,
            "parse_failed_clause_ids": clause_parse_state.get("clause_ids", []),
            "dropped_non_whitelist_risks": dropped_non_whitelist,
            "retained_unmapped_risks": retained_unmapped_risks,
            "unmapped_citation_risks": retained_unmapped_risks,
            "suppressed_missing_risks": suppressed_missing_risks,
            "reconciled_suppressed_risks": reconciled_suppressed_risks,
            "fallback_generated_risks": fallback_generated_risks,
            "zero_risk_fallback_enabled": fallback_enabled,
            "zero_risk_fallback_triggered": fallback_generated_risks > 0,
            "risk_origin_breakdown": {
                "llm_risks_kept": max(0, len(normalized_risks) - fallback_generated_risks),
                "fallback_generated_risks": fallback_generated_risks,
            },
            "retained_unmapped_risk_hits": retained_unmapped_risk_hits[:20],
            "unmapped_citation_risk_hits": retained_unmapped_risk_hits[:20],
            "fallback_generated_risk_hits": fallback_generated_risk_hits[:20],
            "filtered_unverifiable_risks": filtered_unverifiable_risks,
            "filtered_unverifiable_risk_hits": filtered_unverifiable_risk_hits[:20],
            "dedupe_similar_risks": dedup_similar_risks,
            "deduped_similar_risks": deduped_similar_risks,
            "deduped_similar_risk_hits": deduped_similar_risk_hits[:20],
            "suppressed_missing_risk_hits": suppressed_missing_risk_hits[:20],
            "reconciled_suppressed_risk_hits": reconciled_removed_hits[:20],
            "memory_llm_call_budget_limit": int(llm_budget.get("limit") or 0),
            "memory_llm_call_count_actual": int(llm_budget.get("calls") or 0),
            "memory_llm_call_guard_hit": bool(llm_budget.get("guard_hit")),
            "memory_llm_guard_skipped_clause_calls": int(llm_budget.get("skipped_clause_calls") or 0),
            "memory_llm_guard_skipped_flush_calls": int(llm_budget.get("skipped_flush_calls") or 0),
            "memory_llm_guard_skipped_low_priority_calls": int(llm_budget.get("skipped_low_priority_calls") or 0),
            "memory_llm_called_high_priority_clauses": int(llm_budget.get("called_high_priority_clauses") or 0),
        },
        memory_dir=memory_dir,
    )
    logger.info(
        "memory_pipeline_done",
        risks=len(normalized_risks),
        validation_ok=bool(legal_validation.get("ok")),
        parse_failed=parse_failed_count,
        suppressed_missing=suppressed_missing_risks,
        reconciled_suppressed=reconciled_suppressed_risks,
    )
    return {
        "audit": audit,
        "meta": {
            "memory_mode": True,
            "audit_id": audit_id,
            "memory_dir": memory_dir,
            "memory_db": memory_db,
            "memory_use_long_hits": memory_use_long_hits,
            "memory_enable_long_memory": memory_enable_long_memory,
            "memory_enable_short_memory": memory_enable_short_memory,
            "global_tax_context_topics": len([k for k, v in global_tax_context.items() if isinstance(v, list) and v]),
            "global_tax_context_items": sum(len(v) for v in global_tax_context.values() if isinstance(v, list)),
            "memory_report_risk_count": len(normalized_risks),
            "memory_filter_unverifiable_risks": filter_unverifiable_risks,
            "memory_validation_ok": bool(legal_validation.get("ok", False)),
            "risk_dedup_enabled": dedup_similar_risks,
            "dropped_non_whitelist_risks": dropped_non_whitelist,
            "retained_unmapped_risks": retained_unmapped_risks,
            "unmapped_citation_risks": retained_unmapped_risks,
            "retained_unmapped_risk_hits": retained_unmapped_risk_hits[:20],
            "unmapped_citation_risk_hits": retained_unmapped_risk_hits[:20],
            "fallback_generated_risks": fallback_generated_risks,
            "fallback_generated_risk_hits": fallback_generated_risk_hits[:20],
            "filtered_unverifiable_risks": filtered_unverifiable_risks,
            "filtered_unverifiable_risk_hits": filtered_unverifiable_risk_hits[:20],
            "dedupe_similar_risks": dedup_similar_risks,
            "deduped_similar_risks": deduped_similar_risks,
            "deduped_similar_risk_hits": deduped_similar_risk_hits[:20],
            "zero_risk_fallback_enabled": fallback_enabled,
            "zero_risk_fallback_triggered": fallback_generated_risks > 0,
            "risk_origin_breakdown": {
                "llm_risks_kept": max(0, len(normalized_risks) - fallback_generated_risks),
                "fallback_generated_risks": fallback_generated_risks,
            },
            "suppressed_missing_risks": suppressed_missing_risks,
            "suppressed_missing_risk_hits": suppressed_missing_risk_hits[:20],
            "reconciled_suppressed_risks": reconciled_suppressed_risks,
            "reconciled_suppressed_risk_hits": reconciled_removed_hits[:20],
            "parse_failed_clauses": parse_failed_count,
            "memory_degraded": parse_failed_count > 0,
            "memory_llm_call_budget_limit": int(llm_budget.get("limit") or 0),
            "memory_llm_call_count": int(llm_budget.get("calls") or 0),
            "memory_llm_call_count_actual": int(llm_budget.get("calls") or 0),
            "memory_llm_call_guard_hit": bool(llm_budget.get("guard_hit")),
            "memory_llm_guard_skipped_clause_calls": int(llm_budget.get("skipped_clause_calls") or 0),
            "memory_llm_guard_skipped_flush_calls": int(llm_budget.get("skipped_flush_calls") or 0),
            "memory_llm_guard_skipped_low_priority_calls": int(llm_budget.get("skipped_low_priority_calls") or 0),
            "memory_llm_called_high_priority_clauses": int(llm_budget.get("called_high_priority_clauses") or 0),
        },
    }
