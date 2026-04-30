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
from app.services.contract_audit_modules.trace_writer import write_audit_trace, trace_clip, memory_paths, write_round_trace
from app.services.contract_audit_modules.async_bridge import run_coro_sync
from app.services.contract_audit_modules.memory_pipeline.evidence_builder import (
    prepare_evidence_context,
)
from app.services.contract_audit_modules.memory_pipeline.risk_reconciliation import (
    process_report_risks,
)
from app.memory_system.manager import MemoryLifecycleManager, MemoryManagerConfig
from app.memory_system.search import HybridSearcher, HybridSearchConfig
from app.memory_system.indexer import IndexerConfig, MemoryIndexer, SentenceTransformerEmbedder
from app.memory_system.rerank import rerank_memory_candidates, apply_context_budget
from app.services.audit_utils import _enrich_citations, _normalize_lang
from app.memory_system.experience_repo import recall_failure_patterns, recall_similar_audit_memories, recall_workflow_memories
from typing import Dict, Any, List, Optional
import json
import os
import re
import structlog
from datetime import datetime
from pathlib import Path
from app.core.utils import resolve_path

logger = structlog.get_logger(__name__)
_MEMORY_EMBEDDERS: Dict[str, Any] = {}


def _format_failure_patterns(patterns: List[Dict[str, Any]], lang: str) -> str:
    rows: List[str] = []
    for idx, item in enumerate(list(patterns or []), start=1):
        status = str(item.get("reviewer_status")
                     or item.get("outcome") or "").strip()
        label = str(item.get("risk_label") or "").strip()
        text = str(item.get("pattern_text") or "").strip()
        if not text:
            continue
        rows.append(f"- F{idx} [{status}/{label}] {text}")
    if not rows:
        return ""
    if str(lang or "").lower() == "en":
        return "Failure Memory Patterns:\n" + "\n".join(rows) + "\n\n"
    return "失败经验模式:\n" + "\n".join(rows) + "\n\n"


def _format_case_memories(hits: List[Dict[str, Any]], lang: str) -> str:
    rows: List[str] = []
    for idx, item in enumerate(list(hits or []), start=1):
        label = str(item.get("risk_label") or "").strip()
        excerpt = str(item.get("clause_text_excerpt") or "").strip()
        reasoning = str(item.get("risk_reasoning") or "").strip()
        basis = ",".join([str(x).strip() for x in (
            item.get("legal_basis") or []) if str(x).strip()][:2])
        text = " | ".join([x for x in [excerpt, reasoning, basis] if x])
        if not text:
            continue
        rows.append(f"- M{idx} [{label}] {text}")
    if not rows:
        return ""
    if str(lang or "").lower() == "en":
        return "Case Memory Hits:\n" + "\n".join(rows) + "\n\n"
    return "案例记忆命中:\n" + "\n".join(rows) + "\n\n"


def _format_workflow_memories(hits: List[Dict[str, Any]], lang: str) -> str:
    rows: List[str] = []
    for idx, item in enumerate(list(hits or []), start=1):
        title = str(item.get("workflow_title") or "").strip()
        steps = str(item.get("workflow_steps") or "").strip()
        if not title and not steps:
            continue
        text = " | ".join([x for x in [title, steps] if x])
        rows.append(f"- W{idx} {text}")
    if not rows:
        return ""
    if str(lang or "").lower() == "en":
        return "Workflow Memory (Planning):\n" + "\n".join(rows) + "\n\n"
    return "工作流记忆(审计规划):\n" + "\n".join(rows) + "\n\n"


def _estimate_text_tokens(text: str) -> int:
    s = str(text or "")
    if not s:
        return 0
    cjk = len(re.findall(r"[\u4e00-\u9fff]", s))
    non_cjk = max(0, len(s) - cjk)
    return max(1, int(cjk * 1.1 + non_cjk / 3.8))


def _safe_ratio(numerator: int, denominator: int) -> float:
    if int(denominator or 0) <= 0:
        return 0.0
    return round(float(numerator or 0) / float(denominator), 6)


def _tail_by_chars(text: str, max_chars: int) -> str:
    s = str(text or "")
    limit = max(0, int(max_chars or 0))
    if limit <= 0 or len(s) <= limit:
        return s
    trimmed = s[-limit:]
    idx = trimmed.find("\nc")
    if idx > 0:
        return trimmed[idx + 1:]
    return trimmed


def _dedupe_long_memory_hits(text: str, max_blocks: int, max_chars: int) -> str:
    s = str(text or "").strip()
    if not s:
        return ""
    chunks = re.split(r"(?=## Clause (?:Review|Facts) )", s)
    seen = set()
    kept = []
    for c in chunks:
        chunk = str(c or "").strip()
        if not chunk:
            continue
        m = re.search(r"## Clause (?:Review|Facts)\s+([^\[]+)\[", chunk)
        key = str(m.group(1)).strip().lower() if m else ""
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        kept.append(chunk)
        if len(kept) >= max(1, int(max_blocks or 1)):
            break
    out = "\n\n".join(kept).strip()
    limit = max(0, int(max_chars or 0))
    if limit > 0 and len(out) > limit:
        out = out[:limit]
    return out


def _load_llm_json_object(raw_text: str) -> Dict[str, Any]:
    s = str(raw_text or "").strip()
    if not s:
        return json.loads(s)
    fenced = re.sub(r"^\s*```(?:json)?\s*", "", s,
                    count=1, flags=re.IGNORECASE)
    fenced = re.sub(r"\s*```\s*$", "", fenced, count=1,
                    flags=re.IGNORECASE).strip()
    candidates: List[str] = []
    if fenced:
        candidates.append(fenced)
        left = fenced.find("{")
        right = fenced.rfind("}")
        if left >= 0 and right > left:
            obj_text = fenced[left:right + 1].strip()
            if obj_text and obj_text != fenced:
                candidates.append(obj_text)
    if s and s not in candidates:
        candidates.append(s)
    last_error: Optional[Exception] = None
    for item in candidates:
        try:
            out = json.loads(item)
            if isinstance(out, dict):
                return out
        except Exception as e:
            last_error = e
    if last_error is not None:
        raise last_error
    return json.loads(fenced)


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
    workflow_memory_block = _format_workflow_memories(
        workflow_memories, norm_lang)

    def _clause_priority_score(clause_id: str, title: str, body: str, clause_path: str) -> int:
        text = " ".join([str(clause_id or ""), str(title or ""),
                        str(body or ""), str(clause_path or "")]).lower()
        keywords = [
            "税", "tax", "vat", "invoice", "发票", "税率", "计税", "纳税",
            "付款", "payment", "结算", "liability", "违约", "赔偿",
            "termination", "解除", "jurisdiction", "管辖",
        ]
        return sum(1 for k in keywords if k in text)

    preview_order_map: Dict[str, int] = {}
    preview_priority_orders: List[int] = []
    preview_priority_clause_ids = set()
    for idx, pc in enumerate(list(preview_clauses or []), start=1):
        pcid = str(pc.get("clause_id") or "").strip()
        if pcid:
            preview_order_map[pcid] = idx
        pscore = _clause_priority_score(
            pcid,
            str(pc.get("title") or ""),
            str(pc.get("clause_text") or pc.get("text") or ""),
            str(pc.get("clause_path") or ""),
        )
        if pscore > 0:
            preview_priority_orders.append(idx)
            if pcid:
                preview_priority_clause_ids.add(pcid)

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

    async def _clause_cb(payload: Dict[str, Any]) -> Dict[str, Any]:
        clause = payload.get("clause") or {}
        short_memory_full = str(payload.get("short_memory") or "")
        long_memory_hits_full = str(
            payload.get("long_memory_hits") or "").strip()
        clause_text = str(clause.get("text") or "")
        clause_title = str(clause.get("title") or "")
        case_top_k = max(1, min(int(cfg.get("memory_case_top_k") or 3), 5))
        case_memories = recall_similar_audit_memories(
            cfg=cfg,
            query_text=f"{clause_title}\n{clause_text[:360]}",
            top_k=case_top_k,
            regulation_pack_id=str(
                base_trace_meta.get("regulation_pack_id") or ""),
            clause_category=str(clause.get("clause_path") or ""),
            jurisdiction=str(retrieval_opts.get("region") or ""),
            industry=str(retrieval_opts.get("industry") or ""),
            contract_type=str(retrieval_opts.get("contract_type") or ""),
        )
        case_memories = rerank_memory_candidates(
            case_memories,
            query_text=f"{clause_title}\n{clause_text[:360]}",
            regulation_pack_id=str(
                base_trace_meta.get("regulation_pack_id") or ""),
        )
        failure_top_k = max(
            1, min(int(cfg.get("memory_failure_top_k") or 3), 3))
        failure_patterns = recall_failure_patterns(
            cfg=cfg,
            query_text=f"{clause_title}\n{clause_text[:360]}",
            top_k=failure_top_k,
            regulation_pack_id=str(
                base_trace_meta.get("regulation_pack_id") or ""),
            clause_category=str(clause.get("clause_path") or ""),
            jurisdiction=str(retrieval_opts.get("region") or ""),
            industry=str(retrieval_opts.get("industry") or ""),
            contract_type=str(retrieval_opts.get("contract_type") or ""),
        )
        failure_patterns = rerank_memory_candidates(
            failure_patterns,
            query_text=f"{clause_title}\n{clause_text[:360]}",
            regulation_pack_id=str(
                base_trace_meta.get("regulation_pack_id") or ""),
        )
        recall_total_budget_chars = max(
            500, int(cfg.get("memory_recall_budget_chars") or 1200))
        case_budget_chars = int(recall_total_budget_chars * 0.6)
        failure_budget_chars = max(
            160, recall_total_budget_chars - case_budget_chars)
        case_memories = apply_context_budget(
            case_memories,
            max_items=max(1, min(int(cfg.get("memory_case_top_k") or 3), 5)),
            max_chars=case_budget_chars,
        )
        failure_patterns = apply_context_budget(
            failure_patterns,
            max_items=max(
                1, min(int(cfg.get("memory_failure_top_k") or 3), 3)),
            max_chars=failure_budget_chars,
        )
        case_memory_block = _format_case_memories(case_memories, norm_lang)
        failure_patterns_block = _format_failure_patterns(
            failure_patterns, norm_lang)
        short_prompt_max_chars = int(
            cfg.get("memory_prompt_short_max_chars") or (900 if is_relaxed else 760))
        long_prompt_max_chars = int(
            cfg.get("memory_prompt_long_max_chars") or (700 if is_relaxed else 560))
        long_prompt_max_blocks = int(
            cfg.get("memory_prompt_long_max_blocks") or 2)
        short_memory = _tail_by_chars(
            short_memory_full, short_prompt_max_chars)
        long_memory_hits = _dedupe_long_memory_hits(
            long_memory_hits_full, long_prompt_max_blocks, long_prompt_max_chars)

        long_memory_block = ""
        if memory_use_long_hits and long_memory_hits:
            if norm_lang == "en":
                long_memory_block = f"Long-term Memory Hits:\n{long_memory_hits}\n\n"
            else:
                long_memory_block = f"长期记忆命中:\n{long_memory_hits}\n\n"

        global_context_block = ""
        if global_tax_context_text:
            if norm_lang == "en":
                global_context_block = f"Global Tax Fact Profile (from the entire contract):\n{global_tax_context_text}\n\n"
            else:
                global_context_block = f"全局涉税事实画像(来自整份合同):\n{global_tax_context_text}\n\n"

        if norm_lang == "en":
            system = "You are a senior contract audit lawyer. Please output ONLY in JSON format."
            user = (
                f"Language: {norm_lang}\n"
                "Audit strictly based on the input; prioritize the whitelisted laws; output MUST be JSON.\n"
                "Check whether this clause matches any known false-positive or false-negative pattern before finalizing.\n"
                "Short memory and long-term hits are for factual reference only; do not directly reuse their historical conclusions.\n"
                "For risk items, if a whitelist item can be matched, you MUST fill citation_id with an exact whitelist ID and MUST NOT invent any ID; only leave citation_id blank when no match can be determined, and still fill law_title and article_no for post-processing mapping and verification.\n"
                "If outputting 'unclear/unspecified/unmentioned/missing' risks, refer to the short memory, long-term hits, and the current clause; suppress this risk ONLY if the same element has been covered by explicit and enforceable provisions.\n"
                "Do NOT output reasoning processes, analysis processes, or extra explanations. ONLY output the final JSON.\n"
                f"Whitelist:\n{evidence_whitelist_text}\n\n"
                f"Short Memory:\n{short_memory}\n\n"
                f"{case_memory_block}"
                f"{failure_patterns_block}"
                f"{workflow_memory_block}"
                f"{global_context_block}"
                f"{long_memory_block}"
                f"Clause Title: {clause_title}\n"
                f"Clause Text:\n{clause_text}\n\n"
                "JSON: {\"summary\":\"\",\"risks\":[{\"level\":\"high|medium|low\",\"issue\":\"\",\"suggestion\":\"\",\"citation_id\":\"\",\"law_title\":\"\",\"article_no\":\"\",\"evidence\":\"\",\"confidence\":0.0}]}"
            )
        else:
            system = "你是资深合同审计律师。请只输出JSON。"
            user = (
                f"语言:{norm_lang}\n"
                "仅根据输入审计；优先参考白名单法条；输出必须是JSON。\n"
                "Check whether this clause matches any known false-positive or false-negative pattern before finalizing.\n"
                "短记忆和长期命中仅作为事实参考，不可直接复用其中历史结论。\n"
                "风险项如能匹配白名单，必须填写与白名单完全一致的 citation_id，且不得编造ID；仅在确实无法匹配时才可留空，并必须尽量填写 law_title 与 article_no 供后处理映射校验。\n"
                "若要输出‘未明确/未约定/未提及/缺失’风险，可参考短记忆、长期命中与当前条款；仅在同一要素已被明确且可执行约定覆盖时才抑制该风险。\n"
                "禁止输出推理过程、分析过程与额外解释，只输出最终JSON。\n"
                f"白名单:\n{evidence_whitelist_text}\n\n"
                f"短记忆:\n{short_memory}\n\n"
                f"{case_memory_block}"
                f"{failure_patterns_block}"
                f"{workflow_memory_block}"
                f"{global_context_block}"
                f"{long_memory_block}"
                f"条款标题:{clause_title}\n"
                f"条款正文:\n{clause_text}\n\n"
                "JSON: {\"summary\":\"\",\"risks\":[{\"level\":\"high|medium|low\",\"issue\":\"\",\"suggestion\":\"\",\"citation_id\":\"\",\"law_title\":\"\",\"article_no\":\"\",\"evidence\":\"\",\"confidence\":0.0}]}"
            )
        clause_trace_meta = {
            **base_trace_meta,
            "stage": "contract_clause_audit",
            "round": payload.get("round"),
            "clause_id": str(clause.get("clause_id") or ""),
            "clause_path": str(clause.get("clause_path") or ""),
            "risk_detection_mode": risk_detection_mode,
            "audit_mode": str(retrieval_opts.get("audit_mode") or ""),
            "lang": norm_lang,
        }
        round_runtime["round"] = int(payload.get("round") or 0)
        round_runtime["clause_id"] = str(clause.get("clause_id") or "")
        write_audit_trace(
            cfg,
            "clause_round_prompt",
            {
                **clause_trace_meta,
                "clause_title": clause_title,
                "clause_text_len": len(clause_text),
                "short_memory_len": len(short_memory_full),
                "long_memory_hits_len": len(long_memory_hits_full),
                "global_context_len": len(global_tax_context_text),
                "memory_recall_budget_chars": recall_total_budget_chars,
                "case_memory_count": len(case_memories),
                "case_memories_preview": case_memories,
                "failure_pattern_count": len(failure_patterns),
                "failure_patterns_preview": failure_patterns,
                "workflow_memory_count": len(workflow_memories),
                "workflow_memories_preview": workflow_memories,
                "short_memory_preview": short_memory_full,
                "long_memory_preview": long_memory_hits_full,
                "prompt_short_memory_len": len(short_memory),
                "prompt_long_memory_hits_len": len(long_memory_hits),
                "short_memory_saved_chars": max(0, len(short_memory_full) - len(short_memory)),
                "long_memory_saved_chars": max(0, len(long_memory_hits_full) - len(long_memory_hits)),
                "user_input": user,
            },
            memory_dir=memory_dir,
        )
        _write_round(
            "clause_prompt",
            {
                "clause_id": str(clause.get("clause_id") or ""),
                "clause_title": clause_title,
                "clause_text": clause_text,
                "short_memory_full": short_memory_full,
                "short_memory_used": short_memory,
                "long_memory_hits_full": long_memory_hits_full,
                "long_memory_hits_used": long_memory_hits,
                "global_tax_context": global_tax_context_text,
                "llm_system": system,
                "llm_user": user,
            },
            int(payload.get("round") or 0),
        )
        current_clause_id = str(clause.get("clause_id") or "").strip()
        current_order = int(preview_order_map.get(
            current_clause_id) or int(payload.get("round") or 0) or 0)
        current_priority_score = _clause_priority_score(
            current_clause_id,
            clause_title,
            clause_text,
            str(clause.get("clause_path") or ""),
        )
        is_high_priority_clause = bool(
            current_clause_id in preview_priority_clause_ids or current_priority_score > 0)
        remaining_calls = int(llm_budget.get("limit") or 0) - \
            int(llm_budget.get("calls") or 0)
        unseen_high_priority_calls = sum(
            1 for o in preview_priority_orders if int(o or 0) > current_order
        )
        if (not is_high_priority_clause) and unseen_high_priority_calls > 0 and remaining_calls <= unseen_high_priority_calls:
            llm_budget["guard_hit"] = True
            llm_budget["skipped_clause_calls"] = int(
                llm_budget.get("skipped_clause_calls") or 0) + 1
            llm_budget["skipped_low_priority_calls"] = int(
                llm_budget.get("skipped_low_priority_calls") or 0) + 1
            write_audit_trace(
                cfg,
                "clause_round_result",
                {
                    **clause_trace_meta,
                    "ok": False,
                    "reason": "llm_call_budget_reserved_for_high_priority",
                    "llm_call_budget_limit": int(llm_budget.get("limit") or 0),
                    "llm_call_count_actual": int(llm_budget.get("calls") or 0),
                    "remaining_calls": remaining_calls,
                    "unseen_high_priority_calls": unseen_high_priority_calls,
                    "token_usage": {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                    },
                },
                memory_dir=memory_dir,
            )
            _write_round(
                "clause_budget_reserved_skip",
                {
                    "clause_id": current_clause_id,
                    "llm_call_budget_limit": int(llm_budget.get("limit") or 0),
                    "llm_call_count_actual": int(llm_budget.get("calls") or 0),
                    "remaining_calls": remaining_calls,
                    "unseen_high_priority_calls": unseen_high_priority_calls,
                },
                int(payload.get("round") or 0),
            )
            return {"summary": "", "risks": []}
        if int(llm_budget.get("calls") or 0) >= int(llm_budget.get("limit") or 1):
            llm_budget["guard_hit"] = True
            llm_budget["skipped_clause_calls"] = int(
                llm_budget.get("skipped_clause_calls") or 0) + 1
            write_audit_trace(
                cfg,
                "clause_round_result",
                {
                    **clause_trace_meta,
                    "ok": False,
                    "reason": "llm_call_budget_exceeded",
                    "llm_call_budget_limit": int(llm_budget.get("limit") or 0),
                    "llm_call_count_actual": int(llm_budget.get("calls") or 0),
                    "token_usage": {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                    },
                },
                memory_dir=memory_dir,
            )
            _write_round(
                "clause_budget_skipped",
                {
                    "clause_id": str(clause.get("clause_id") or ""),
                    "llm_call_budget_limit": int(llm_budget.get("limit") or 0),
                    "llm_call_count_actual": int(llm_budget.get("calls") or 0),
                },
                int(payload.get("round") or 0),
            )
            return {"summary": "", "risks": []}
        llm_budget["calls"] = int(llm_budget.get("calls") or 0) + 1
        if is_high_priority_clause:
            llm_budget["called_high_priority_clauses"] = int(
                llm_budget.get("called_high_priority_clauses") or 0) + 1
        try:
            result_text, llm_raw = llm.chat([{"role": "system", "content": system}, {
                "role": "user", "content": user}], overrides={"max_tokens": 600 if is_relaxed else 600, "enable_thinking": False, "reasoning_effort": "low", "thinking_budget_tokens": 0, "_trace_meta": clause_trace_meta})
        except Exception as e:
            clause_parse_state["count"] += 1
            clause_parse_state["clause_ids"].append(
                str(clause.get("clause_id") or ""))
            write_audit_trace(
                cfg,
                "clause_round_result",
                {
                    **clause_trace_meta,
                    "ok": False,
                    "reason": "llm_call_failed",
                    "error": str(e),
                    "raw_len": 0,
                    "llm_response": "",
                    "token_usage": {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                    },
                },
                memory_dir=memory_dir,
            )
            logger.warning(
                "memory_clause_call_failed",
                round=payload.get("round"),
                clause_id=str(clause.get("clause_id") or ""),
                error=str(e),
            )
            _write_round("clause_llm_error", {"clause_id": str(
                clause.get("clause_id") or ""), "error": str(e)})
            return {"summary": "", "risks": []}
        usage = llm_raw.get("usage") if isinstance(
            llm_raw.get("usage"), dict) else {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or (
            prompt_tokens + completion_tokens))
        segment_est_tokens = {
            "short_memory": _estimate_text_tokens(short_memory),
            "long_memory_hits": _estimate_text_tokens(long_memory_hits),
            "global_context": _estimate_text_tokens(global_tax_context_text),
            "clause_title": _estimate_text_tokens(clause_title),
            "clause_text": _estimate_text_tokens(clause_text),
            "instruction_scaffold": _estimate_text_tokens(user) - (
                _estimate_text_tokens(short_memory)
                + _estimate_text_tokens(long_memory_hits)
                + _estimate_text_tokens(global_tax_context_text)
                + _estimate_text_tokens(clause_title)
                + _estimate_text_tokens(clause_text)
            ),
        }
        segment_est_tokens["instruction_scaffold"] = max(
            0, int(segment_est_tokens["instruction_scaffold"] or 0))
        prompt_est_total = sum(int(v or 0)
                               for v in segment_est_tokens.values())
        token_share_analysis = {
            "prompt_side": {
                k: {
                    "est_tokens": int(v or 0),
                    "ratio_in_prompt_tokens": _safe_ratio(int(v or 0), prompt_tokens),
                }
                for k, v in segment_est_tokens.items()
            },
            "overall": {
                "prompt_ratio_in_total": _safe_ratio(prompt_tokens, total_tokens),
                "completion_ratio_in_total": _safe_ratio(completion_tokens, total_tokens),
                "prompt_tokens_est_total": prompt_est_total,
                "prompt_tokens_actual": prompt_tokens,
            }
        }
        try:
            parsed = _load_llm_json_object(result_text)
            if not isinstance(parsed, dict):
                clause_parse_state["count"] += 1
                clause_parse_state["clause_ids"].append(
                    str(clause.get("clause_id") or ""))
                write_audit_trace(
                    cfg,
                    "clause_round_result",
                    {
                        **clause_trace_meta,
                        "ok": False,
                        "reason": "non_dict_json",
                        "raw_len": len(str(result_text or "")),
                        "llm_response": str(result_text or ""),
                        "token_usage": {
                            "prompt_tokens": prompt_tokens,
                            "completion_tokens": completion_tokens,
                            "total_tokens": total_tokens,
                        },
                        "token_share_analysis": token_share_analysis,
                    },
                    memory_dir=memory_dir,
                )
                logger.warning(
                    "memory_clause_parse_failed",
                    round=payload.get("round"),
                    clause_id=str(clause.get("clause_id") or ""),
                    reason="non_dict_json"
                )
                _write_round("clause_result_non_dict", {"clause_id": str(clause.get("clause_id") or ""), "llm_response": str(
                    result_text or ""), "token_usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_tokens": total_tokens}})
                return {"summary": "", "risks": []}
            risks_out = parsed.get("risks") if isinstance(
                parsed.get("risks"), list) else []
            for r in risks_out:
                if not isinstance(r, dict):
                    continue
                cid_raw = str(r.get("citation_id") or "").strip()
                if cid_raw in citation_alias_map:
                    r["citation_id"] = citation_alias_map[cid_raw]
            write_audit_trace(
                cfg,
                "clause_round_result",
                {
                    **clause_trace_meta,
                    "ok": True,
                    "raw_len": len(str(result_text or "")),
                    "summary_len": len(str(parsed.get("summary") or "")),
                    "risk_count": len(risks_out),
                    "llm_response": str(result_text or ""),
                    "token_usage": {
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": total_tokens,
                    },
                    "token_share_analysis": token_share_analysis,
                },
                memory_dir=memory_dir,
            )
            _write_round("clause_result_ok", {"clause_id": str(clause.get("clause_id") or ""), "summary": str(parsed.get("summary") or ""), "risks": risks_out, "token_usage": {
                         "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_tokens": total_tokens}, "llm_response": str(result_text or "")})
            return parsed
        except Exception as e:
            clause_parse_state["count"] += 1
            clause_parse_state["clause_ids"].append(
                str(clause.get("clause_id") or ""))
            write_audit_trace(
                cfg,
                "clause_round_result",
                {
                    **clause_trace_meta,
                    "ok": False,
                    "reason": "json_decode_error",
                    "error": str(e),
                    "llm_response": str(result_text or ""),
                    "token_usage": {
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": total_tokens,
                    },
                    "token_share_analysis": token_share_analysis,
                },
                memory_dir=memory_dir,
            )
            logger.warning(
                "memory_clause_parse_failed",
                round=payload.get("round"),
                clause_id=str(clause.get("clause_id") or ""),
                reason="json_decode_error",
                error=str(e),
                raw_preview=str(result_text or "")[:280]
            )
            _write_round("clause_result_json_decode_error", {"clause_id": str(clause.get("clause_id") or ""), "error": str(e), "llm_response": str(
                result_text or ""), "token_usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_tokens": total_tokens}})
            return {"summary": "", "risks": []}

    async def _flush_cb(prompt: str) -> str:
        content = f"请把以下上下文压缩为可持久化的Markdown记忆要点：\n\n{prompt}"
        flush_round = int(round_runtime.get("round") or 0)
        flush_trace_meta = {
            **base_trace_meta,
            "stage": "contract_memory_flush",
            "round": flush_round,
            "clause_id": str(round_runtime.get("clause_id") or ""),
            "risk_detection_mode": risk_detection_mode,
            "audit_mode": str(retrieval_opts.get("audit_mode") or ""),
            "lang": lang,
        }
        write_audit_trace(
            cfg,
            "memory_flush_prompt",
            {**flush_trace_meta,
                "prompt_len": len(prompt), "prompt_preview": trace_clip(prompt, 260)},
            memory_dir=memory_dir,
        )
        _write_round("flush_prompt", {"clause_id": str(round_runtime.get(
            "clause_id") or ""), "prompt": prompt, "llm_system": "你是记忆压缩助手。", "llm_user": content}, flush_round)
        if int(llm_budget.get("calls") or 0) >= int(llm_budget.get("limit") or 1):
            llm_budget["guard_hit"] = True
            llm_budget["skipped_flush_calls"] = int(
                llm_budget.get("skipped_flush_calls") or 0) + 1
            out = str(prompt or "")[:220]
            write_audit_trace(
                cfg,
                "memory_flush_result",
                {
                    **flush_trace_meta,
                    "result_len": len(out),
                    "result_preview": trace_clip(out, 260),
                    "reason": "llm_call_budget_exceeded",
                    "llm_call_budget_limit": int(llm_budget.get("limit") or 0),
                    "llm_call_count_actual": int(llm_budget.get("calls") or 0),
                },
                memory_dir=memory_dir,
            )
            _write_round("flush_budget_skipped", {"clause_id": str(
                round_runtime.get("clause_id") or ""), "result": out}, flush_round)
            return out
        llm_budget["calls"] = int(llm_budget.get("calls") or 0) + 1
        result_text, _ = llm.chat([{"role": "system", "content": "你是记忆压缩助手。"}, {
            "role": "user", "content": content}], overrides={"max_tokens": 220, "enable_thinking": False, "reasoning_effort": "low", "thinking_budget_tokens": 0, "_trace_meta": flush_trace_meta})
        out = str(result_text or "").strip()
        write_audit_trace(
            cfg,
            "memory_flush_result",
            {**flush_trace_meta,
                "result_len": len(out), "result_preview": trace_clip(out, 260)},
            memory_dir=memory_dir,
        )
        _write_round("flush_result", {"clause_id": str(
            round_runtime.get("clause_id") or ""), "result": out}, flush_round)
        return out

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
