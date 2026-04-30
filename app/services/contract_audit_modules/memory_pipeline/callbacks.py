"""Callback builders for memory pipeline clause and flush rounds."""

from typing import Dict, Any, List, Callable, Awaitable, Tuple
import re
import structlog

from app.services.contract_audit_modules.trace_writer import write_audit_trace, trace_clip
from app.services.contract_audit_modules.memory_pipeline.prompt_templates import (
    build_clause_prompt,
    format_case_memories,
    format_failure_patterns,
    load_llm_json_object,
)
from app.services.contract_audit_modules.memory_pipeline.clause_iterator import (
    clause_priority_score,
)
from app.memory_system.rerank import rerank_memory_candidates, apply_context_budget
from app.memory_system.experience_repo import (
    recall_failure_patterns,
    recall_similar_audit_memories,
)

logger = structlog.get_logger(__name__)


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


def create_memory_callbacks(
    *,
    cfg: Dict[str, Any],
    llm: Any,
    norm_lang: str,
    lang: str,
    is_relaxed: bool,
    memory_use_long_hits: bool,
    evidence_whitelist_text: str,
    workflow_memory_block: str,
    workflow_memories: List[Dict[str, Any]],
    global_tax_context_text: str,
    base_trace_meta: Dict[str, Any],
    retrieval_opts: Dict[str, Any],
    memory_dir: str,
    preview_order_map: Dict[str, int],
    preview_priority_orders: List[int],
    preview_priority_clause_ids: set,
    llm_budget: Dict[str, Any],
    clause_parse_state: Dict[str, Any],
    citation_alias_map: Dict[str, str],
    round_runtime: Dict[str, Any],
    write_round: Callable[[str, Dict[str, Any], int], None],
) -> Tuple[Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]], Callable[[str], Awaitable[str]]]:
    risk_detection_mode = str(retrieval_opts.get("risk_detection_mode", "relaxed"))

    async def _clause_cb(payload: Dict[str, Any]) -> Dict[str, Any]:
        clause = payload.get("clause") or {}
        short_memory_full = str(payload.get("short_memory") or "")
        long_memory_hits_full = str(payload.get("long_memory_hits") or "").strip()
        clause_text = str(clause.get("text") or "")
        clause_title = str(clause.get("title") or "")
        case_top_k = max(1, min(int(cfg.get("memory_case_top_k") or 3), 5))
        case_memories = recall_similar_audit_memories(
            cfg=cfg,
            query_text=f"{clause_title}\n{clause_text[:360]}",
            top_k=case_top_k,
            regulation_pack_id=str(base_trace_meta.get("regulation_pack_id") or ""),
            clause_category=str(clause.get("clause_path") or ""),
            jurisdiction=str(retrieval_opts.get("region") or ""),
            industry=str(retrieval_opts.get("industry") or ""),
            contract_type=str(retrieval_opts.get("contract_type") or ""),
        )
        case_memories = rerank_memory_candidates(
            case_memories,
            query_text=f"{clause_title}\n{clause_text[:360]}",
            regulation_pack_id=str(base_trace_meta.get("regulation_pack_id") or ""),
        )
        failure_top_k = max(1, min(int(cfg.get("memory_failure_top_k") or 3), 3))
        failure_patterns = recall_failure_patterns(
            cfg=cfg,
            query_text=f"{clause_title}\n{clause_text[:360]}",
            top_k=failure_top_k,
            regulation_pack_id=str(base_trace_meta.get("regulation_pack_id") or ""),
            clause_category=str(clause.get("clause_path") or ""),
            jurisdiction=str(retrieval_opts.get("region") or ""),
            industry=str(retrieval_opts.get("industry") or ""),
            contract_type=str(retrieval_opts.get("contract_type") or ""),
        )
        failure_patterns = rerank_memory_candidates(
            failure_patterns,
            query_text=f"{clause_title}\n{clause_text[:360]}",
            regulation_pack_id=str(base_trace_meta.get("regulation_pack_id") or ""),
        )
        recall_total_budget_chars = max(500, int(cfg.get("memory_recall_budget_chars") or 1200))
        case_budget_chars = int(recall_total_budget_chars * 0.6)
        failure_budget_chars = max(160, recall_total_budget_chars - case_budget_chars)
        case_memories = apply_context_budget(
            case_memories,
            max_items=max(1, min(int(cfg.get("memory_case_top_k") or 3), 5)),
            max_chars=case_budget_chars,
        )
        failure_patterns = apply_context_budget(
            failure_patterns,
            max_items=max(1, min(int(cfg.get("memory_failure_top_k") or 3), 3)),
            max_chars=failure_budget_chars,
        )
        case_memory_block = format_case_memories(case_memories, norm_lang)
        failure_patterns_block = format_failure_patterns(failure_patterns, norm_lang)
        short_prompt_max_chars = int(cfg.get("memory_prompt_short_max_chars") or (900 if is_relaxed else 760))
        long_prompt_max_chars = int(cfg.get("memory_prompt_long_max_chars") or (700 if is_relaxed else 560))
        long_prompt_max_blocks = int(cfg.get("memory_prompt_long_max_blocks") or 2)
        short_memory = _tail_by_chars(short_memory_full, short_prompt_max_chars)
        long_memory_hits = _dedupe_long_memory_hits(long_memory_hits_full, long_prompt_max_blocks, long_prompt_max_chars)

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

        system, user = build_clause_prompt(
            norm_lang=norm_lang,
            evidence_whitelist_text=evidence_whitelist_text,
            short_memory=short_memory,
            case_memory_block=case_memory_block,
            failure_patterns_block=failure_patterns_block,
            workflow_memory_block=workflow_memory_block,
            global_context_block=global_context_block,
            long_memory_block=long_memory_block,
            clause_title=clause_title,
            clause_text=clause_text,
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
        write_round(
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
        current_order = int(preview_order_map.get(current_clause_id) or int(payload.get("round") or 0) or 0)
        current_priority_score = clause_priority_score(
            current_clause_id,
            clause_title,
            clause_text,
            str(clause.get("clause_path") or ""),
        )
        is_high_priority_clause = bool(current_clause_id in preview_priority_clause_ids or current_priority_score > 0)
        remaining_calls = int(llm_budget.get("limit") or 0) - int(llm_budget.get("calls") or 0)
        unseen_high_priority_calls = sum(1 for o in preview_priority_orders if int(o or 0) > current_order)
        if (not is_high_priority_clause) and unseen_high_priority_calls > 0 and remaining_calls <= unseen_high_priority_calls:
            llm_budget["guard_hit"] = True
            llm_budget["skipped_clause_calls"] = int(llm_budget.get("skipped_clause_calls") or 0) + 1
            llm_budget["skipped_low_priority_calls"] = int(llm_budget.get("skipped_low_priority_calls") or 0) + 1
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
                    "token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                },
                memory_dir=memory_dir,
            )
            write_round(
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
            llm_budget["skipped_clause_calls"] = int(llm_budget.get("skipped_clause_calls") or 0) + 1
            write_audit_trace(
                cfg,
                "clause_round_result",
                {
                    **clause_trace_meta,
                    "ok": False,
                    "reason": "llm_call_budget_exceeded",
                    "llm_call_budget_limit": int(llm_budget.get("limit") or 0),
                    "llm_call_count_actual": int(llm_budget.get("calls") or 0),
                    "token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                },
                memory_dir=memory_dir,
            )
            write_round(
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
            llm_budget["called_high_priority_clauses"] = int(llm_budget.get("called_high_priority_clauses") or 0) + 1
        try:
            result_text, llm_raw = llm.chat(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                overrides={"max_tokens": 600, "enable_thinking": False, "reasoning_effort": "low", "thinking_budget_tokens": 0, "_trace_meta": clause_trace_meta},
            )
        except Exception as e:
            clause_parse_state["count"] += 1
            clause_parse_state["clause_ids"].append(str(clause.get("clause_id") or ""))
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
                    "token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                },
                memory_dir=memory_dir,
            )
            logger.warning("memory_clause_call_failed", round=payload.get("round"), clause_id=str(clause.get("clause_id") or ""), error=str(e))
            write_round("clause_llm_error", {"clause_id": str(clause.get("clause_id") or ""), "error": str(e)})
            return {"summary": "", "risks": []}
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
            "instruction_scaffold": _estimate_text_tokens(user)
            - (
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
                k: {"est_tokens": int(v or 0), "ratio_in_prompt_tokens": _safe_ratio(int(v or 0), prompt_tokens)}
                for k, v in segment_est_tokens.items()
            },
            "overall": {
                "prompt_ratio_in_total": _safe_ratio(prompt_tokens, total_tokens),
                "completion_ratio_in_total": _safe_ratio(completion_tokens, total_tokens),
                "prompt_tokens_est_total": prompt_est_total,
                "prompt_tokens_actual": prompt_tokens,
            },
        }
        try:
            parsed = load_llm_json_object(result_text)
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
                        "token_usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_tokens": total_tokens},
                        "token_share_analysis": token_share_analysis,
                    },
                    memory_dir=memory_dir,
                )
                logger.warning("memory_clause_parse_failed", round=payload.get("round"), clause_id=str(clause.get("clause_id") or ""), reason="non_dict_json")
                write_round(
                    "clause_result_non_dict",
                    {
                        "clause_id": str(clause.get("clause_id") or ""),
                        "llm_response": str(result_text or ""),
                        "token_usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_tokens": total_tokens},
                    },
                )
                return {"summary": "", "risks": []}
            risks_out = parsed.get("risks") if isinstance(parsed.get("risks"), list) else []
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
                    "token_usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_tokens": total_tokens},
                    "token_share_analysis": token_share_analysis,
                },
                memory_dir=memory_dir,
            )
            write_round(
                "clause_result_ok",
                {
                    "clause_id": str(clause.get("clause_id") or ""),
                    "summary": str(parsed.get("summary") or ""),
                    "risks": risks_out,
                    "token_usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_tokens": total_tokens},
                    "llm_response": str(result_text or ""),
                },
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
                    "token_usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_tokens": total_tokens},
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
                raw_preview=str(result_text or "")[:280],
            )
            write_round(
                "clause_result_json_decode_error",
                {
                    "clause_id": str(clause.get("clause_id") or ""),
                    "error": str(e),
                    "llm_response": str(result_text or ""),
                    "token_usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_tokens": total_tokens},
                },
            )
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
            {**flush_trace_meta, "prompt_len": len(prompt), "prompt_preview": trace_clip(prompt, 260)},
            memory_dir=memory_dir,
        )
        write_round(
            "flush_prompt",
            {"clause_id": str(round_runtime.get("clause_id") or ""), "prompt": prompt, "llm_system": "你是记忆压缩助手。", "llm_user": content},
            flush_round,
        )
        if int(llm_budget.get("calls") or 0) >= int(llm_budget.get("limit") or 1):
            llm_budget["guard_hit"] = True
            llm_budget["skipped_flush_calls"] = int(llm_budget.get("skipped_flush_calls") or 0) + 1
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
            write_round("flush_budget_skipped", {"clause_id": str(round_runtime.get("clause_id") or ""), "result": out}, flush_round)
            return out
        llm_budget["calls"] = int(llm_budget.get("calls") or 0) + 1
        result_text, _ = llm.chat(
            [{"role": "system", "content": "你是记忆压缩助手。"}, {"role": "user", "content": content}],
            overrides={"max_tokens": 220, "enable_thinking": False, "reasoning_effort": "low", "thinking_budget_tokens": 0, "_trace_meta": flush_trace_meta},
        )
        out = str(result_text or "").strip()
        write_audit_trace(
            cfg,
            "memory_flush_result",
            {**flush_trace_meta, "result_len": len(out), "result_preview": trace_clip(out, 260)},
            memory_dir=memory_dir,
        )
        write_round("flush_result", {"clause_id": str(round_runtime.get("clause_id") or ""), "result": out}, flush_round)
        return out

    return _clause_cb, _flush_cb
