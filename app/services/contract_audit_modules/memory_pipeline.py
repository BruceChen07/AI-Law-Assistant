"""
Memory Pipeline.
职责: 协调 MemoryLifecycleManager 运行长短记忆条款级审计流。
输入输出: 接收合同配置、条款和法条库，返回审计结果及统计元信息。
异常场景: LLM 超时或解析失败时，记录异常并跳过出错条款，最终可能抛出退级异常。
"""
import json
import re
import structlog
from pathlib import Path
from typing import Dict, Any, List, Optional
from app.services.audit_utils import _normalize_risk_level, _enrich_citations
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
    reconcile_cross_clause_conflicts
)

logger = structlog.get_logger(__name__)
_MEMORY_EMBEDDER = None

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
    chunks = re.split(r"(?=## Clause Review )", s)
    seen = set()
    kept = []
    for c in chunks:
        chunk = str(c or "").strip()
        if not chunk:
            continue
        m = re.search(r"## Clause Review\s+([^\[]+)\[", chunk)
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

def get_memory_embedder():
    global _MEMORY_EMBEDDER
    if _MEMORY_EMBEDDER is not None:
        return _MEMORY_EMBEDDER
    try:
        _MEMORY_EMBEDDER = SentenceTransformerEmbedder("all-MiniLM-L6-v2")
        logger.info("memory_embedder_ready", provider="sentence_transformers")
        return _MEMORY_EMBEDDER
    except Exception as e:
        logger.warning("memory_embedder_fallback", reason=str(e))

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
        _MEMORY_EMBEDDER = _FallbackEmbedder()
        return _MEMORY_EMBEDDER

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
    """执行带记忆库的条款级审计。"""
    memory_dir, memory_db = memory_paths(cfg)
    memory_use_long_hits = bool(cfg.get("memory_use_long_hits", True))
    logger.info("memory_pipeline_start", memory_dir=memory_dir, memory_db=memory_db, evidence=len(evidence_items))
    
    embedder = get_memory_embedder()
    indexer = MemoryIndexer(
        IndexerConfig(memory_root=Path(memory_dir), db_path=Path(memory_db)),
        embedder
    )
    indexer.reindex_all()
    searcher = HybridSearcher(indexer, Path(memory_db), HybridSearchConfig(vector_weight=0.7, keyword_weight=0.3))
    
    timeout_sec = min(10.0, max(3.0, float((cfg.get("llm_config") or {}).get("timeout", 8))))
    risk_detection_mode = str(retrieval_opts.get("risk_detection_mode", "relaxed"))
    is_relaxed = risk_detection_mode == "relaxed"
    
    memory_cfg = MemoryManagerConfig(
        short_memory_token_limit=int(cfg.get("memory_short_token_limit") or (3200 if is_relaxed else 2200)),
        flush_soft_threshold=int(cfg.get("memory_flush_token_threshold") or (3200 if is_relaxed else 2200)),
        llm_timeout_sec=timeout_sec,
        retrieval_top_k=int(cfg.get("memory_retrieval_top_k") or (6 if is_relaxed else 4)),
        risk_dedup_similarity_threshold=0.93 if is_relaxed else 0.86,
        risk_dedup_enabled=False,
        max_rounds=int(cfg.get("memory_max_rounds") or 16),
        clause_query_max_chars=int(cfg.get("memory_clause_query_max_chars") or 520),
        hit_item_max_chars=int(cfg.get("memory_hit_item_max_chars") or 240),
        short_ctx_turns=int(cfg.get("memory_short_ctx_turns") or 4),
        short_store_clause_chars=int(cfg.get("memory_short_store_clause_chars") or 320),
        short_store_risks_chars=int(cfg.get("memory_short_store_risks_chars") or 600),
    )
    
    manager = MemoryLifecycleManager(Path(memory_dir), indexer, searcher, memory_cfg)
    
    legal_catalog = build_legal_catalog(evidence_items)
    citation_lookup = build_citation_lookup(evidence_items)
    allowed_citation_ids = {
        str(it.get("citation_id") or "").strip()
        for it in evidence_items
        if str(it.get("citation_id") or "").strip()
    }
    evidence_whitelist_text = build_evidence_whitelist_text(evidence_items)
    global_tax_context = build_global_tax_context(preview_clauses)
    global_tax_context_text = format_global_tax_context(global_tax_context)
    
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
            "global_tax_context_topics": len([k for k, v in global_tax_context.items() if isinstance(v, list) and v]),
            "global_tax_context_items": sum(len(v) for v in global_tax_context.values() if isinstance(v, list)),
            "memory_cfg": memory_cfg.model_dump(),
        },
        memory_dir=memory_dir,
    )

    async def _clause_cb(payload: Dict[str, Any]) -> Dict[str, Any]:
        clause = payload.get("clause") or {}
        short_memory_full = str(payload.get("short_memory") or "")
        long_memory_hits_full = str(payload.get("long_memory_hits") or "").strip()
        clause_text = str(clause.get("text") or "")
        clause_title = str(clause.get("title") or "")
        short_prompt_max_chars = int(cfg.get("memory_prompt_short_max_chars") or (2200 if is_relaxed else 1400))
        long_prompt_max_chars = int(cfg.get("memory_prompt_long_max_chars") or (2200 if is_relaxed else 1400))
        long_prompt_max_blocks = int(cfg.get("memory_prompt_long_max_blocks") or (6 if is_relaxed else 4))
        short_memory = _tail_by_chars(short_memory_full, short_prompt_max_chars)
        long_memory_hits = _dedupe_long_memory_hits(long_memory_hits_full, long_prompt_max_blocks, long_prompt_max_chars)
        
        long_memory_block = ""
        if memory_use_long_hits and long_memory_hits:
            long_memory_block = f"长期记忆命中:\n{long_memory_hits}\n\n"
        global_context_block = ""
        if global_tax_context_text:
            global_context_block = f"全局涉税事实画像(来自整份合同):\n{global_tax_context_text}\n\n"
            
        system = "你是资深合同审计律师。请只输出JSON。"
        user = (
            f"语言:{lang}\n"
            "仅根据输入审计；仅可使用白名单法条；输出必须是JSON。\n"
            "每条风险必须包含 citation_id 且在白名单内。\n"
            "若要输出‘未明确/未约定/未提及/缺失’风险，先核查短记忆、长期命中、当前条款；任一处已明确则禁止输出该缺失风险。\n"
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
            "lang": lang,
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
        result_text, llm_raw = llm.chat([{"role": "system", "content": system}, {
                                       "role": "user", "content": user}], overrides={"max_tokens": 1800 if is_relaxed else 1200, "_trace_meta": clause_trace_meta})
        usage = llm_raw.get("usage") if isinstance(llm_raw.get("usage"), dict) else {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
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
        segment_est_tokens["instruction_scaffold"] = max(0, int(segment_est_tokens["instruction_scaffold"] or 0))
        prompt_est_total = sum(int(v or 0) for v in segment_est_tokens.values())
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
                clause_parse_state["clause_ids"].append(str(clause.get("clause_id") or ""))
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
            risks_out = parsed.get("risks") if isinstance(parsed.get("risks"), list) else []
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
            clause_parse_state["clause_ids"].append(str(clause.get("clause_id") or ""))
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
            {**flush_trace_meta, "prompt_len": len(prompt), "prompt_preview": trace_clip(prompt, 260)},
            memory_dir=memory_dir,
        )
        result_text, _ = llm.chat([{"role": "system", "content": "你是记忆压缩助手。"}, {
                                  "role": "user", "content": content}], overrides={"max_tokens": 600, "_trace_meta": flush_trace_meta})
        out = str(result_text or "").strip()
        write_audit_trace(
            cfg,
            "memory_flush_result",
            {**flush_trace_meta, "result_len": len(out), "result_preview": trace_clip(out, 260)},
            memory_dir=memory_dir,
        )
        return out

    report = run_coro_sync(manager.audit_contract(text, _clause_cb, _flush_cb, legal_catalog))
    
    risks = report.get("risks") if isinstance(report.get("risks"), list) else []
    clause_map = {str(c.get("clause_id")): c for c in preview_clauses}
    normalized_risks = []
    dropped_non_whitelist = 0
    suppressed_missing_risks = 0
    suppressed_missing_risk_hits: List[Dict[str, Any]] = []
    
    for r in risks:
        if not isinstance(r, dict):
            continue
        cid = str(r.get("clause_id") or "")
        c = clause_map.get(cid) or {}
        law_title = str(r.get("law_title") or "")
        article_no = str(r.get("article_no") or "")
        citation_id = str(r.get("citation_id") or "").strip()
        if not citation_id:
            from app.services.utils.contract_audit_utils import citation_match_key
            citation_id = citation_lookup.get(citation_match_key(law_title, article_no), "")
        if not citation_id or citation_id not in allowed_citation_ids:
            dropped_non_whitelist += 1
            continue
        basis = f"{law_title} {article_no}".strip()
        risk_item = {
            "level": _normalize_risk_level(r.get("level")),
            "issue": str(r.get("issue") or ""),
            "suggestion": str(r.get("suggestion") or ""),
            "basis": basis,
            "law_reference": basis,
            "citation_id": citation_id,
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
    
    risk_summary = {"high": 0, "medium": 0, "low": 0}
    for r in normalized_risks:
        risk_summary[r.get("level", "low")] += 1
        
    parse_failed_count = int(clause_parse_state.get("count") or 0)
    if parse_failed_count > 0 and not normalized_risks:
        raise RuntimeError("memory audit degraded: llm json parse failed and no risks produced")
        
    legal_validation = report.get("legal_validation") if isinstance(report.get("legal_validation"), dict) else {"ok": True, "issues": []}
    if parse_failed_count > 0:
        issues = legal_validation.get("issues") if isinstance(legal_validation.get("issues"), list) else []
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
            "suppressed_missing_risks": suppressed_missing_risks,
            "reconciled_suppressed_risks": reconciled_suppressed_risks,
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
            "memory_validation_ok": bool(legal_validation.get("ok", False)),
            "risk_dedup_enabled": False,
            "dropped_non_whitelist_risks": dropped_non_whitelist,
            "suppressed_missing_risks": suppressed_missing_risks,
            "suppressed_missing_risk_hits": suppressed_missing_risk_hits[:20],
            "reconciled_suppressed_risks": reconciled_suppressed_risks,
            "reconciled_suppressed_risk_hits": reconciled_removed_hits[:20],
            "parse_failed_clauses": parse_failed_count,
            "memory_degraded": parse_failed_count > 0,
        },
    }
