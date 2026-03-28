"""
Memory Pipeline.
Responsibilities: Coordinates the MemoryLifecycleManager.
Input/Output: Accepts contract configuration, clauses, and legal catalog, and returns audit results and metadata.
Exception Handling: Logs exceptions and skips clauses that fail, potentially throwing fallback exceptions.
"""
import json
import re
import structlog
from pathlib import Path
from typing import Dict, Any, List, Optional
from app.services.audit_utils import _normalize_risk_level, _enrich_citations, _normalize_lang
from app.memory_system.indexer import IndexerConfig, MemoryIndexer, SentenceTransformerEmbedder
from app.memory_system.search import HybridSearcher, HybridSearchConfig
from app.memory_system.manager import MemoryLifecycleManager, MemoryManagerConfig
from app.services.contract_audit_modules.async_bridge import run_coro_sync
from app.services.contract_audit_modules.trace_writer import write_audit_trace, trace_clip, memory_paths
from app.services.contract_audit_modules.citation_catalog import build_legal_catalog, build_citation_lookup, build_evidence_whitelist_text
from app.services.contract_audit_modules.risk_suppression import (
    build_global_tax_context,
    format_global_tax_context,
    should_suppress_missing_risk,
    reconcile_cross_clause_conflicts,
    detect_zero_risk_fallback_hit,
)

logger = structlog.get_logger(__name__)
_MEMORY_EMBEDDERS: Dict[str, Any] = {}


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


def _build_compact_whitelist(evidence_items: List[Dict[str, Any]], limit: int = 40):
    lines: List[str] = []
    alias_to_cid: Dict[str, str] = {}
    n = 0
    for it in evidence_items:
        cid = str(it.get("citation_id") or "").strip()
        if not cid:
            continue
        law = str(it.get("law_title") or it.get("title") or "").strip()
        article = str(it.get("article_no") or "").strip()
        if not law or not article:
            continue
        n += 1
        alias = f"C{n}"
        alias_to_cid[alias] = cid
        lines.append(f"- {alias}: {law} {article}")
        if n >= max(1, int(limit or 1)):
            break
    return "\n".join(lines), alias_to_cid


def get_memory_embedder(lang: str = "zh"):
    global _MEMORY_EMBEDDERS
    norm_lang = _normalize_lang(lang, default="zh")
    if norm_lang in _MEMORY_EMBEDDERS:
        return _MEMORY_EMBEDDERS[norm_lang]
    try:
        model_name = "BAAI/bge-small-zh-v1.5" if norm_lang == "zh" else "BAAI/bge-small-en-v1.5"
        embedder = SentenceTransformerEmbedder(model_name)
        _MEMORY_EMBEDDERS[norm_lang] = embedder
        logger.info("memory_embedder_ready",
                    provider="sentence_transformers", lang=norm_lang, model=model_name)
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
        _MEMORY_EMBEDDERS[norm_lang] = embedder
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
    memory_use_long_hits = bool(cfg.get("memory_use_long_hits", True))
    logger.info("memory_pipeline_start", memory_dir=memory_dir,
                memory_db=memory_db, evidence=len(evidence_items), lang=norm_lang)

    embedder = get_memory_embedder(norm_lang)
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
    )

    manager = MemoryLifecycleManager(
        Path(memory_dir), indexer, searcher, memory_cfg)

    legal_catalog = build_legal_catalog(evidence_items)
    citation_lookup = build_citation_lookup(evidence_items)
    allowed_citation_ids = {
        str(it.get("citation_id") or "").strip()
        for it in evidence_items
        if str(it.get("citation_id") or "").strip()
    }
    evidence_by_cid = {
        str(it.get("citation_id") or "").strip(): it
        for it in evidence_items
        if str(it.get("citation_id") or "").strip()
    }
    filter_unverifiable_risks = bool(
        cfg.get("memory_filter_unverifiable_risks", True))
    filtered_risk_log_limit = max(
        1, int(cfg.get("memory_filtered_risk_log_limit") or 30))
    evidence_whitelist_text = build_evidence_whitelist_text(
        evidence_items, limit=int(cfg.get("memory_whitelist_limit") or 36))
    citation_alias_map: Dict[str, str] = {}
    global_tax_context = build_global_tax_context(preview_clauses)
    global_tax_context_text = format_global_tax_context(
        global_tax_context, per_topic_limit=2)

    clause_parse_state = {"count": 0, "clause_ids": []}
    base_trace_meta = dict(trace_context or {})

    write_audit_trace(
        cfg,
        "memory_pipeline_start",
        {
            **base_trace_meta,
            "memory_dir": memory_dir,
            "memory_db": memory_db,
            "memory_use_long_hits": memory_use_long_hits,
            "risk_detection_mode": risk_detection_mode,
            "audit_mode": str(retrieval_opts.get("audit_mode") or ""),
            "evidence_count": len(evidence_items),
            "zero_risk_fallback_enabled": bool(cfg.get("memory_zero_risk_fallback_enabled", True)),
            "global_tax_context_topics": len([k for k, v in global_tax_context.items() if isinstance(v, list) and v]),
            "global_tax_context_items": sum(len(v) for v in global_tax_context.values() if isinstance(v, list)),
            "memory_cfg": memory_cfg.model_dump(),
            "memory_filter_unverifiable_risks": filter_unverifiable_risks,
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
                "Short memory and long-term hits are for factual reference only; do not directly reuse their historical conclusions.\n"
                "For risk items, prioritize filling in the whitelisted citation_id; if the citation_id cannot be determined, it can be left blank, but try to fill in the law_title and article_no for post-processing mapping and verification.\n"
                "If outputting 'unclear/unspecified/unmentioned/missing' risks, refer to the short memory, long-term hits, and the current clause; suppress this risk ONLY if the same element has been covered by explicit and enforceable provisions.\n"
                "Do NOT output reasoning processes, analysis processes, or extra explanations. ONLY output the final JSON.\n"
                f"Whitelist:\n{evidence_whitelist_text}\n\n"
                f"Short Memory:\n{short_memory}\n\n"
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
                "短记忆和长期命中仅作为事实参考，不可直接复用其中历史结论。\n"
                "风险项优先填写白名单 citation_id；如无法确定 citation_id，可留空，但必须尽量填写 law_title 与 article_no 供后处理映射校验。\n"
                "若要输出‘未明确/未约定/未提及/缺失’风险，可参考短记忆、长期命中与当前条款；仅在同一要素已被明确且可执行约定覆盖时才抑制该风险。\n"
                "禁止输出推理过程、分析过程与额外解释，只输出最终JSON。\n"
                f"白名单:\n{evidence_whitelist_text}\n\n"
                f"短记忆:\n{short_memory}\n\n"
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
            parsed = json.loads(result_text)
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
            return {"summary": "", "risks": []}

    async def _flush_cb(prompt: str) -> str:
        content = f"请把以下上下文压缩为可持久化的Markdown记忆要点：\n\n{prompt}"
        flush_trace_meta = {
            **base_trace_meta,
            "stage": "contract_memory_flush",
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
        return out

    report = run_coro_sync(manager.audit_contract(
        text, _clause_cb, _flush_cb, legal_catalog))

    risks = report.get("risks") if isinstance(
        report.get("risks"), list) else []
    clause_map = {str(c.get("clause_id")): c for c in preview_clauses}
    normalized_risks = []
    dropped_non_whitelist = 0
    retained_unmapped_risks = 0
    retained_unmapped_risk_hits: List[Dict[str, Any]] = []
    suppressed_missing_risks = 0
    suppressed_missing_risk_hits: List[Dict[str, Any]] = []
    fallback_generated_risks = 0
    fallback_generated_risk_hits: List[Dict[str, Any]] = []
    filtered_unverifiable_risks = 0
    filtered_unverifiable_risk_hits: List[Dict[str, Any]] = []
    from app.services.utils.contract_audit_utils import citation_match_key

    for r in risks:
        if not isinstance(r, dict):
            continue
        cid = str(r.get("clause_id") or "")
        c = clause_map.get(cid) or {}
        law_title = str(r.get("law_title") or "")
        article_no = str(r.get("article_no") or "")
        citation_id = str(r.get("citation_id") or "").strip()
        if citation_id and citation_id not in allowed_citation_ids:
            dropped_non_whitelist += 1
            citation_id = ""
        if not citation_id:
            citation_id = citation_lookup.get(
                citation_match_key(law_title, article_no), "")
        has_mapping = bool(citation_id and citation_id in allowed_citation_ids)
        if not has_mapping:
            retained_unmapped_risks += 1
            retained_unmapped_risk_hits.append(
                {
                    "risk_id": str(r.get("risk_id") or ""),
                    "clause_id": cid,
                    "law_title": law_title,
                    "article_no": article_no,
                }
            )
        basis = f"{law_title} {article_no}".strip()
        risk_item = {
            "level": _normalize_risk_level(r.get("level")),
            "issue": str(r.get("issue") or ""),
            "suggestion": str(r.get("suggestion") or ""),
            "basis": basis,
            "law_reference": basis,
            "citation_id": citation_id if has_mapping else "",
            "citation_status": "mapped" if has_mapping else "unmapped",
            "evidence": str(r.get("evidence") or ""),
            "law_title": law_title,
            "article_no": article_no,
            "location": {
                "risk_id": str(r.get("risk_id") or ""),
                "clause_id": cid,
                "anchor_id": str(c.get("anchor_id") or ""),
                "page_no": int(c.get("page_no") or 0),
                "paragraph_no": str(c.get("paragraph_no") or ""),
                "clause_path": str(c.get("clause_path") or ""),
                "quote": str(r.get("evidence") or ""),
                "score": round(float(r.get("confidence") or 0.0), 4),
            },
        }
        suppress, hit = should_suppress_missing_risk(
            risk_item,
            preview_clauses,
            cid,
            global_tax_context=global_tax_context,
        )
        if suppress:
            suppressed_missing_risks += 1
            suppressed_missing_risk_hits.append(
                {
                    "risk_id": str(r.get("risk_id") or ""),
                    "clause_id": cid,
                    "topic": str(hit.get("topic") or ""),
                    "counter_clause_id": str(hit.get("clause_id") or ""),
                    "counter_clause_path": str(hit.get("clause_path") or ""),
                    "counter_page_no": int(hit.get("page_no") or 0),
                    "counter_paragraph_no": str(hit.get("paragraph_no") or ""),
                    "counter_source": str(hit.get("source") or ""),
                }
            )
            continue
        normalized_risks.append(risk_item)

    reconciled_removed_hits: List[Dict[str, Any]] = []
    normalized_risks, reconciled_removed_hits = reconcile_cross_clause_conflicts(
        normalized_risks,
        preview_clauses,
        global_tax_context,
    )
    reconciled_suppressed_risks = len(reconciled_removed_hits)
    fallback_enabled = bool(cfg.get("memory_zero_risk_fallback_enabled", True))
    if fallback_enabled and not normalized_risks:
        fallback_hit, hit_info = detect_zero_risk_fallback_hit(
            preview_clauses, global_tax_context)
        if fallback_hit:
            fallback_generated_risks = 1
            fallback_generated_risk_hits.append(hit_info)
            fallback_law_title = "中华人民共和国增值税法"
            fallback_article_no = "第三条"
            mapped_fallback_citation_id = citation_lookup.get(
                citation_match_key(fallback_law_title, fallback_article_no), ""
            )
            mapped_ok = bool(
                mapped_fallback_citation_id and mapped_fallback_citation_id in allowed_citation_ids)
            fallback_quote = str(hit_info.get("quote") or "")
            fallback_clause_id = str(hit_info.get("clause_id") or "")
            fallback_clause = clause_map.get(fallback_clause_id) or {}
            normalized_risks.append(
                {
                    "level": "medium",
                    "issue": "合同存在免税/税率与服务场景组合，适用条件和计税依据需进一步复核。",
                    "suggestion": "补充免税适用依据、计税口径和留存资料要求，并确认开票与纳税义务安排。",
                    "basis": f"{fallback_law_title} {fallback_article_no}",
                    "law_reference": f"{fallback_law_title} {fallback_article_no}",
                    "citation_id": mapped_fallback_citation_id if mapped_ok else "",
                    "citation_status": "mapped" if mapped_ok else "unmapped",
                    "evidence": fallback_quote,
                    "law_title": fallback_law_title,
                    "article_no": fallback_article_no,
                    "location": {
                        "risk_id": "fallback-r1",
                        "clause_id": fallback_clause_id,
                        "anchor_id": str(fallback_clause.get("anchor_id") or ""),
                        "page_no": int(hit_info.get("page_no") or fallback_clause.get("page_no") or 0),
                        "paragraph_no": str(hit_info.get("paragraph_no") or fallback_clause.get("paragraph_no") or ""),
                        "clause_path": str(hit_info.get("clause_path") or fallback_clause.get("clause_path") or ""),
                        "quote": fallback_quote,
                        "score": 0.6,
                    },
                }
            )

    if filter_unverifiable_risks and normalized_risks:
        display_risks: List[Dict[str, Any]] = []
        for item in normalized_risks:
            cid = str(item.get("citation_id") or "").strip()
            mapped_ok = bool(cid and cid in allowed_citation_ids)
            ev = evidence_by_cid.get(cid) if mapped_ok else {}
            full_text = str((ev or {}).get("content") or (
                ev or {}).get("excerpt") or "").strip()
            if mapped_ok and full_text:
                display_risks.append(item)
                continue
            filtered_unverifiable_risks += 1
            if len(filtered_unverifiable_risk_hits) < filtered_risk_log_limit:
                filtered_unverifiable_risk_hits.append(
                    {
                        "risk_id": str(((item.get("location") or {}).get("risk_id") or "")),
                        "clause_id": str(((item.get("location") or {}).get("clause_id") or "")),
                        "citation_id": cid,
                        "citation_status": str(item.get("citation_status") or ""),
                        "reason": "unmapped_or_no_fulltext",
                        "law_title": str(item.get("law_title") or ""),
                        "article_no": str(item.get("article_no") or ""),
                    }
                )
        normalized_risks = display_risks

    risk_summary = {"high": 0, "medium": 0, "low": 0}
    for r in normalized_risks:
        risk_summary[r.get("level", "low")] += 1

    parse_failed_count = int(clause_parse_state.get("count") or 0)
    if parse_failed_count > 0 and not normalized_risks:
        raise RuntimeError(
            "memory audit degraded: llm json parse failed and no risks produced")

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
            "suppressed_missing_risk_hits": suppressed_missing_risk_hits[:20],
            "reconciled_suppressed_risk_hits": reconciled_removed_hits[:20],
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
            "memory_dir": memory_dir,
            "memory_db": memory_db,
            "memory_use_long_hits": memory_use_long_hits,
            "global_tax_context_topics": len([k for k, v in global_tax_context.items() if isinstance(v, list) and v]),
            "global_tax_context_items": sum(len(v) for v in global_tax_context.values() if isinstance(v, list)),
            "memory_report_risk_count": len(normalized_risks),
            "memory_filter_unverifiable_risks": filter_unverifiable_risks,
            "memory_validation_ok": bool(legal_validation.get("ok", False)),
            "risk_dedup_enabled": False,
            "dropped_non_whitelist_risks": dropped_non_whitelist,
            "retained_unmapped_risks": retained_unmapped_risks,
            "unmapped_citation_risks": retained_unmapped_risks,
            "retained_unmapped_risk_hits": retained_unmapped_risk_hits[:20],
            "unmapped_citation_risk_hits": retained_unmapped_risk_hits[:20],
            "fallback_generated_risks": fallback_generated_risks,
            "fallback_generated_risk_hits": fallback_generated_risk_hits[:20],
            "filtered_unverifiable_risks": filtered_unverifiable_risks,
            "filtered_unverifiable_risk_hits": filtered_unverifiable_risk_hits[:20],
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
        },
    }
