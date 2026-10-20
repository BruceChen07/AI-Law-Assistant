"""
Contract Audit Facade.
职责: 作为合同审计模块的统一入口，封装 IO 操作并调用拆分后的子模块。
"""
import time
import uuid
import hashlib
import json
import os
import re
import structlog
from typing import Dict, Any, Optional, Callable, List

from app.core.utils import extract_text_with_config
from app.services.audit_utils import _safe_int, _normalize_citation_item, _enrich_citations
from app.services.audit_retrieval import _normalize_retrieval_options, _retrieve_regulation_evidence
from app.services.contract_audit_modules.clause_builder import build_preview_clauses
from app.services.contract_audit_modules.result_assembler import attach_risk_locations
from app.services.contract_audit_modules.trace_writer import write_audit_trace, trace_clip
from app.services.contract_preview_assets import build_contract_preview_manifest
from app.services.prompt_binding import resolve_bound_prompt_text

logger = structlog.get_logger(__name__)

CLASSIC_AUDIT_MAX_TOKENS = 900
CLASSIC_AUDIT_RETRY_MAX_TOKENS = 1400
CLASSIC_MULTIPASS_TRIGGER_CLAUSES = 10
CONTRACT_SUMMARY_SOURCE_MAX_CHARS = 9000
CONTRACT_SUMMARY_MAX_TOKENS = 720
SKILL_AUDIT_MODES = {"skill", "skills", "advanced",
                     "advanced_skill", "skill_orchestrated"}
HybridSearcher = None


def _build_preview_clauses(text: str):
    return build_preview_clauses(text)


def _attach_risk_locations(audit, clauses, preview_manifest=None):
    return attach_risk_locations(audit, clauses, preview_manifest=preview_manifest)


def _chat_with_task_profile(llm, messages, task_profile: str, overrides=None):
    if hasattr(llm, "chat_with_profile"):
        return llm.chat_with_profile(messages, task_profile, overrides=overrides)
    next_overrides = dict(overrides or {})
    next_overrides["_task_profile"] = task_profile
    return llm.chat(messages, overrides=next_overrides)


def _build_contract_summary_source_text(
    text: str,
    preview_clauses: List[Dict[str, Any]],
    max_chars: int = CONTRACT_SUMMARY_SOURCE_MAX_CHARS,
) -> str:
    remaining = max(400, int(max_chars or CONTRACT_SUMMARY_SOURCE_MAX_CHARS))
    blocks: List[str] = []
    for clause in list(preview_clauses or []):
        if remaining <= 180:
            break
        title = str(clause.get("clause_path") or clause.get("title")
                    or clause.get("clause_id") or "").strip()
        body = str(clause.get("clause_text")
                   or clause.get("text") or "").strip()
        if not body:
            continue
        local_limit = min(1200, max(220, remaining - 40))
        snippet = body[:local_limit].strip()
        block = f"[{title}]\n{snippet}" if title else snippet
        if len(block) > remaining:
            block = block[:remaining].strip()
        if not block:
            continue
        blocks.append(block)
        remaining -= len(block) + 2
    source_text = "\n\n".join(blocks).strip()
    if source_text:
        return source_text[:max_chars].strip()
    return str(text or "")[:max_chars].strip()


def _get_memory_embedder(lang: str = "zh", cfg: Optional[Dict[str, Any]] = None):
    try:
        from app.services.contract_audit_modules import memory_pipeline as memory_pipeline_module
    except Exception:
        return None
    getter = getattr(memory_pipeline_module, "get_memory_embedder", None)
    if not callable(getter):
        return None
    try:
        return getter(lang, cfg=cfg)
    except TypeError:
        try:
            return getter(lang)
        except TypeError:
            try:
                return getter()
            except Exception:
                return None
        except Exception:
            return None
    except Exception:
        return None


def _get_memory_runtime_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    raw = cfg.get("memory_runtime_config") if isinstance(
        cfg.get("memory_runtime_config"), dict) else {}
    mode = str(raw.get("memory_mode_when_disabled")
               or "classic").strip().lower()
    if mode not in {"classic"}:
        mode = "classic"
    classic_audit_max_tokens = max(
        300, int(raw.get("classic_audit_max_tokens")
                 or CLASSIC_AUDIT_MAX_TOKENS)
    )
    classic_audit_retry_max_tokens = max(
        classic_audit_max_tokens,
        int(raw.get("classic_audit_retry_max_tokens")
            or CLASSIC_AUDIT_RETRY_MAX_TOKENS),
    )
    return {
        "memory_module_enabled": bool(raw.get("memory_module_enabled", True)),
        "memory_mode_when_disabled": mode,
        "memory_disable_fallback_on_error": bool(raw.get("memory_disable_fallback_on_error", True)),
        "memory_max_llm_calls_per_audit": max(1, int(raw.get("memory_max_llm_calls_per_audit") or 12)),
        "memory_max_prompt_chars_per_clause": max(300, int(raw.get("memory_max_prompt_chars_per_clause") or 2400)),
        "classic_audit_max_tokens": classic_audit_max_tokens,
        "classic_audit_retry_max_tokens": classic_audit_retry_max_tokens,
    }


def _requested_audit_mode(retrieval_options: Optional[Dict[str, Any]]) -> str:
    if not isinstance(retrieval_options, dict):
        return "rag"
    return str(retrieval_options.get("audit_mode") or "rag").strip().lower()


def _build_skill_basis(legal_basis: Dict[str, Any]) -> str:
    if not isinstance(legal_basis, dict):
        return ""
    law_title = str(legal_basis.get("law_title") or "").strip()
    article_no = str(legal_basis.get("article_no") or "").strip()
    parts = [part for part in [law_title, article_no] if part]
    return " ".join(parts).strip()


def _normalize_skill_risk_item(item: Dict[str, Any]) -> Dict[str, Any]:
    legal_basis = item.get("legal_basis") if isinstance(
        item.get("legal_basis"), dict) else {}
    basis_text = _build_skill_basis(legal_basis)
    return {
        "level": str(item.get("risk_level") or "medium").strip().lower(),
        "type": str(item.get("risk_category") or "").strip(),
        "issue": str(item.get("issue") or "").strip(),
        "evidence": str(item.get("original_clause_quote") or "").strip(),
        "suggestion": str(item.get("detailed_suggestion") or item.get("amendment_suggestion") or "").strip(),
        "law_reference": basis_text,
        "basis": basis_text,
        "law_title": str(legal_basis.get("law_title") or "").strip(),
        "article_no": str(legal_basis.get("article_no") or "").strip(),
        "citation_id": str(item.get("citation_id") or "").strip(),
        "source_skills": list(item.get("source_skills") or []),
        "cross_validated": bool(item.get("cross_validated")),
        "cross_validation_note": str(item.get("cross_validation_note") or "").strip(),
        "urgency": str(item.get("urgency") or "").strip(),
        "estimated_impact": str(item.get("estimated_impact") or "").strip(),
        "clause_location": str(item.get("clause_location") or "").strip(),
    }


def _build_skill_citations(risk_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    citations: List[Dict[str, Any]] = []
    seen = set()
    for idx, item in enumerate(list(risk_items or []), start=1):
        if not isinstance(item, dict):
            continue
        legal_basis = item.get("legal_basis") if isinstance(
            item.get("legal_basis"), dict) else {}
        law_title = str(legal_basis.get("law_title") or "").strip()
        article_no = str(legal_basis.get("article_no") or "").strip()
        article_content = str(legal_basis.get("article_content") or "").strip()
        if not law_title and not article_no and not article_content:
            continue
        key = (law_title, article_no, article_content)
        if key in seen:
            continue
        seen.add(key)
        citations.append(
            _normalize_citation_item(
                {
                    "citation_id": f"skill-{idx}",
                    "law_title": law_title,
                    "title": law_title,
                    "article_no": article_no,
                    "excerpt": article_content[:240],
                    "content": article_content,
                }
            )
        )
    return citations


def _run_skill_orchestrated_audit(
    cfg: Dict[str, Any],
    llm,
    file_path: str,
    text: str,
    lang: str,
    preview_clauses: List[Dict[str, Any]],
    preview_manifest: Optional[Dict[str, Any]],
    embedder,
    progress_cb: Optional[Callable[[str, int, str], None]] = None,
) -> Dict[str, Any]:
    from app.contract_audit_skills.core.skill_orchestrator import SkillOrchestrator

    orchestrator = SkillOrchestrator(llm_service=llm, embedder=embedder)
    report = orchestrator.audit(
        text, file_path=file_path, progress_cb=progress_cb)
    raw_risk_items = list(getattr(report, "risk_items", []) or [])
    normalized_risks = [_normalize_skill_risk_item(
        item) for item in raw_risk_items if isinstance(item, dict)]
    audit = {
        "summary": str(getattr(report, "executive_summary", "") or "").strip(),
        "executive_opinion": [],
        "risk_summary": {
            "high": sum(1 for item in normalized_risks if item.get("level") == "high"),
            "medium": sum(1 for item in normalized_risks if item.get("level") == "medium"),
            "low": sum(1 for item in normalized_risks if item.get("level") == "low"),
        },
        "risks": normalized_risks,
        "citations": _build_skill_citations(raw_risk_items),
        "legal_validation": {"ok": True, "issues": []},
    }
    audit = attach_risk_locations(
        audit,
        preview_clauses,
        preview_manifest=preview_manifest,
    )
    audit["cross_validation_notes"] = list(
        getattr(report, "cross_validation_notes", []) or [])
    skill_results = getattr(report, "skill_results", {}) or {}
    total_skill_calls = len(
        [key for key in skill_results.keys() if key != "skill_risk_summary"])
    audit_metadata = getattr(report, "audit_metadata", {}) or {}
    meta = {
        "memory_mode": False,
        "memory_mode_enabled": False,
        "execution_path": "skill_orchestrated",
        "memory_llm_call_count": max(1, total_skill_calls),
        "memory_report_risk_count": len(normalized_risks),
        "memory_validation_ok": True,
        "skill_cross_validation_enabled": bool(audit_metadata.get("cross_validation_enabled", True)),
        "skill_report_id": str(getattr(report, "report_id", "") or ""),
        "skill_contract_type": str((getattr(report, "contract_info", {}) or {}).get("contract_type") or ""),
        "skill_total_time_ms": int(audit_metadata.get("total_time_ms") or 0),
        "skill_total_token_usage": int(audit_metadata.get("total_token_usage") or 0),
        "skill_risk_items": len(raw_risk_items),
    }
    return {
        "audit": audit,
        "meta": meta,
        "raw": {
            "mode": "skill_orchestrated",
            "report_id": str(getattr(report, "report_id", "") or ""),
        },
    }


def _load_llm_json_object(raw_text: str) -> Dict[str, Any]:
    s = str(raw_text or "").strip()
    if not s:
        return {}
    fenced = re.sub(r"^\s*```(?:json)?\s*", "", s,
                    count=1, flags=re.IGNORECASE)
    fenced = re.sub(r"\s*```\s*$", "", fenced, count=1,
                    flags=re.IGNORECASE).strip()
    candidates: List[str] = [x for x in [fenced, s] if x]
    if fenced:
        left = fenced.find("{")
        right = fenced.rfind("}")
        if left >= 0 and right > left:
            obj_text = fenced[left:right + 1].strip()
            if obj_text and obj_text not in candidates:
                candidates.append(obj_text)
    for item in candidates:
        try:
            parsed = json.loads(item)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue
    return {}


def _extract_finish_reason(raw: Dict[str, Any]) -> str:
    if not isinstance(raw, dict):
        return ""
    choices = raw.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], dict) else {}
        return str(first.get("finish_reason") or "").strip().lower()
    return str(raw.get("done_reason") or "").strip().lower()


def _extract_usage(raw: Dict[str, Any]) -> Dict[str, int]:
    usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
    prompt_tokens = _safe_int(usage.get("prompt_tokens", 0), 0)
    completion_tokens = _safe_int(usage.get("completion_tokens", 0), 0)
    total_tokens = _safe_int(
        usage.get("total_tokens", prompt_tokens + completion_tokens),
        prompt_tokens + completion_tokens,
    )
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def _extract_contract_summary_text(raw_text: str) -> str:
    parsed = _load_llm_json_object(raw_text)
    if parsed:
        summary = str(parsed.get("contract_summary")
                      or parsed.get("summary") or "").strip()
        if summary:
            return summary
    text = str(raw_text or "").strip()
    if not text:
        return ""
    text = re.sub(r"^\s*```(?:text|markdown|md)?\s*", "", text,
                  count=1, flags=re.IGNORECASE)
    text = re.sub(r"\s*```\s*$", "", text, count=1,
                  flags=re.IGNORECASE).strip()
    text = re.sub(
        r"^\s*(?:contract\s*summary|summary|合同摘要|合同总结|整份合同总结)\s*[:：]\s*",
        "",
        text,
        count=1,
        flags=re.IGNORECASE,
    ).strip()
    return text


def _build_contract_summary_title(source_filename: str, lang: str) -> str:
    name = str(source_filename or "").strip()
    if not name:
        return "Current Contract" if str(lang or "").lower() == "en" else "当前合同"
    stem = os.path.splitext(os.path.basename(name))[0].strip()
    if not stem:
        return "Current Contract" if str(lang or "").lower() == "en" else "当前合同"
    return stem


def _build_contract_summary(
    cfg: Dict[str, Any],
    llm,
    text: str,
    lang: str,
    preview_clauses: List[Dict[str, Any]],
    page_count: int,
    audit_id: str,
    source_filename: str,
) -> Dict[str, Any]:
    enabled = bool(cfg.get("contract_summary_enabled", False))
    if not enabled:
        return {"enabled": False, "text": "", "source": "disabled", "status": "disabled"}
    source_text = _build_contract_summary_source_text(text, preview_clauses)
    if not source_text:
        return {"enabled": True, "text": "", "source": "empty_input", "status": "empty_input"}
    norm_lang = "en" if str(lang or "").lower() == "en" else "zh"
    source_name = os.path.basename(str(source_filename or "").strip())
    summary_title = _build_contract_summary_title(source_name, norm_lang)
    prompt_context = {
        "lang": norm_lang,
        "contract_page_count": max(0, int(page_count or 0)),
        "contract_clause_count": len(list(preview_clauses or [])),
        "contract_document_count": 1,
        "contract_summary_title": summary_title,
        "source_filename": source_name,
        "contract_text": source_text,
    }
    try:
        bound_prompt = resolve_bound_prompt_text(
            "contract_audit.contract_summary",
            "",
            prompt_context,
            cfg,
        )
    except KeyError:
        bound_prompt = {
            "rendered_system_text": (
                "You are a contract analysis assistant. Summarize the contract faithfully."
                if norm_lang == "en"
                else "你是合同分析助手，请忠实概括整份合同。"
            ),
            "rendered_text": (
                "Summarize the whole contract in 3 to 5 concise sentences for business users.\n\n"
                "Output exactly in the following format:\n"
                "Contract Content Brief Summary\n\n"
                "Quick Read for Tax Reviewers\n\n"
                f"Total {1} Contract / Supplement Agreement\n\n"
                "Instructions\n\n"
                "This document is only used to help reviewers quickly understand the transaction background, key commercial arrangements, and main tax concerns. It does not replace a full review of the original contract, attachments, or actual business process.\n\n"
                f"1. {summary_title}\n\n"
                "What the contract is about: ...\n\n"
                "Core arrangement: ...\n\n"
                "Tax review note: ...\n\n"
                f"Source file: {source_name or 'Current Contract'}\n\n"
                "The following contract context is for summary generation only. Do not copy these helper labels into the final output.\n"
                f"- Page Count: {max(0, int(page_count or 0))}\n"
                f"- Clause Count: {len(list(preview_clauses or []))}\n"
                f"- Contract Content: {source_text}"
                if norm_lang == "en"
                else "请严格按以下格式输出，不要遗漏标题，不要输出 JSON，不要补充格式之外的说明：\n\n"
                "合同内容简要摘要\n\n"
                "税务审核师快速阅读版\n\n"
                f"共 {1} 份合同 / 补充协议\n\n"
                "使用说明\n\n"
                "本文件仅用于帮助审核人员迅速了解合同交易背景、核心商业安排及主要涉税关注点，不替代对合同原文、附件及实际业务流程的完整审核。\n\n"
                f"1. {summary_title}\n\n"
                "合同在说什么：...\n\n"
                "核心安排：...\n\n"
                "税务审核提示：...\n\n"
                f"来源文件：{source_name or '当前合同'}\n\n"
                "以下为摘要使用的合同上下文，请不要把以下提示标题写入最终输出。\n"
                f"- 合同页数：{max(0, int(page_count or 0))}\n"
                f"- 条款数：{len(list(preview_clauses or []))}\n"
                f"- 合同内容：{source_text}"
            ),
            "source": "inline_fallback",
            "prompt_id": "contract_audit.contract_summary",
            "prompt_name": "contract_audit.contract_summary",
        }
    trace_meta = {
        "module": "contract_audit",
        "stage": "contract_summary",
        "audit_id": audit_id,
        "lang": norm_lang,
        "prompt_source": str(bound_prompt.get("source") or "system_default"),
        "prompt_binding_scene": "contract_audit.contract_summary",
        "prompt_binding_id": str(bound_prompt.get("prompt_id") or ""),
        "prompt_binding_name": str(bound_prompt.get("prompt_name") or ""),
    }
    try:
        result_text, _raw = _chat_with_task_profile(
            llm,
            [
                {"role": "system", "content": str(
                    bound_prompt.get("rendered_system_text") or "")},
                {"role": "user", "content": str(
                    bound_prompt.get("rendered_text") or "")},
            ],
            "contract_audit_main",
            overrides={
                "max_tokens": CONTRACT_SUMMARY_MAX_TOKENS,
                "enable_thinking": False,
                "reasoning_effort": "low",
                "thinking_budget_tokens": 0,
                "_trace_meta": trace_meta,
            },
        )
        summary_text = _extract_contract_summary_text(result_text)
        summary_text = re.sub(
            r"\n{3,}", "\n\n", str(summary_text or "").strip())
        if len(summary_text) > 2400:
            summary_text = summary_text[:2400].rstrip()
        status = "ok" if summary_text else "empty"
        logger.info(
            "contract_summary_generation_done",
            audit_id=audit_id,
            status=status,
            chars=len(summary_text),
            source=str(bound_prompt.get("source") or "system_default"),
            prompt_id=str(bound_prompt.get("prompt_id") or ""),
        )
        return {
            "enabled": True,
            "text": summary_text,
            "status": status,
            "source": str(bound_prompt.get("source") or "system_default"),
            "prompt_id": str(bound_prompt.get("prompt_id") or ""),
            "prompt_name": str(bound_prompt.get("prompt_name") or ""),
        }
    except Exception as exc:
        logger.warning(
            "contract_summary_generation_failed",
            audit_id=audit_id,
            error=str(exc),
        )
        return {
            "enabled": True,
            "text": "",
            "status": "error",
            "source": "error",
            "error": str(exc),
        }


def _merge_citations(primary: List[Dict[str, Any]], extra: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen = set()
    for item in list(primary or []) + list(extra or []):
        if not isinstance(item, dict):
            continue
        key = (
            str(item.get("citation_id") or "").strip(),
            str(item.get("law_title") or item.get("title") or "").strip(),
            str(item.get("article_no") or "").strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def _chunk_preview_clauses(
    preview_clauses: List[Dict[str, Any]],
    max_calls: int,
) -> List[List[Dict[str, Any]]]:
    clauses = [item for item in list(
        preview_clauses or []) if isinstance(item, dict)]
    if not clauses:
        return []
    if len(clauses) <= CLASSIC_MULTIPASS_TRIGGER_CLAUSES:
        return [clauses]
    bounded_calls = max(1, int(max_calls or 1))
    chunk_size = max(
        1,
        (len(clauses) + bounded_calls - 1) // bounded_calls,
    )
    return [
        clauses[idx: idx + chunk_size]
        for idx in range(0, len(clauses), chunk_size)
    ]


def _aggregate_classic_results(
    results: List[Dict[str, Any]],
    lang: str,
) -> Dict[str, Any]:
    norm_lang = "en" if str(lang or "").lower() == "en" else "zh"
    merged_risks: List[Dict[str, Any]] = []
    merged_citations: List[Dict[str, Any]] = []
    legal_issues: List[Dict[str, Any]] = []
    seen_risks = set()
    total_llm_calls = 0
    validation_ok = True
    parse_failed_chunks = 0
    response_empty_chunks = 0
    response_truncated_chunks = 0
    completion_tokens = 0
    max_output_tokens = 0
    for chunk_result in list(results or []):
        audit = chunk_result.get("audit") if isinstance(
            chunk_result.get("audit"), dict) else {}
        meta = chunk_result.get("meta") if isinstance(
            chunk_result.get("meta"), dict) else {}
        total_llm_calls += max(1,
                               _safe_int(meta.get("memory_llm_call_count", 1), 1))
        completion_tokens += _safe_int(
            meta.get("classic_completion_tokens", 0), 0)
        max_output_tokens = max(
            max_output_tokens,
            _safe_int(meta.get("classic_max_tokens", 0), 0),
        )
        if bool(meta.get("classic_parse_failed")):
            parse_failed_chunks += 1
        if bool(meta.get("classic_response_empty")):
            response_empty_chunks += 1
        if bool(meta.get("classic_response_truncated")):
            response_truncated_chunks += 1
        validation_ok = validation_ok and bool(
            meta.get("memory_validation_ok", False))
        merged_citations = _merge_citations(
            merged_citations,
            audit.get("citations") if isinstance(
                audit.get("citations"), list) else [],
        )
        for item in list(audit.get("risks") or []):
            if not isinstance(item, dict):
                continue
            location = item.get("location") if isinstance(
                item.get("location"), dict) else {}
            key = (
                str(item.get("level") or "").strip().lower(),
                str(item.get("issue") or "").strip(),
                str(item.get("law_reference") or item.get("basis") or "").strip(),
                str(location.get("clause_id") or "").strip(),
            )
            if key in seen_risks:
                continue
            seen_risks.add(key)
            merged_risks.append(item)
        legal_validation = audit.get("legal_validation") if isinstance(
            audit.get("legal_validation"), dict) else {}
        for issue in list(legal_validation.get("issues") or []):
            if isinstance(issue, dict):
                legal_issues.append(issue)
    risk_summary = {"high": 0, "medium": 0, "low": 0}
    for item in merged_risks:
        level = str(item.get("level") or "low").strip().lower()
        if level not in risk_summary:
            level = "low"
        risk_summary[level] += 1
    audit_incomplete = bool(
        legal_issues) or parse_failed_chunks > 0 or response_empty_chunks > 0 or response_truncated_chunks > 0
    if audit_incomplete:
        summary = (
            f"Classic multipass audit may be incomplete: found {len(merged_risks)} risks across {len(results)} chunk(s), but {parse_failed_chunks} chunk(s) failed to parse and {response_truncated_chunks} chunk(s) were truncated"
            if norm_lang == "en"
            else f"分块经典审计结果可能不完整：共处理 {len(results)} 个片段，当前识别 {len(merged_risks)} 项风险；其中 {parse_failed_chunks} 个片段解析失败，{response_truncated_chunks} 个片段输出被截断"
        )
    else:
        summary = (
            f"Classic multipass audit completed, found {len(merged_risks)} risks across {len(results)} chunk(s)"
            if norm_lang == "en"
            else f"分块经典审计完成，共在 {len(results)} 个片段中识别 {len(merged_risks)} 项风险"
        )
    return {
        "audit": {
            "summary": summary,
            "executive_opinion": [],
            "risk_summary": risk_summary,
            "risks": merged_risks,
            "citations": merged_citations,
            "audit_incomplete": audit_incomplete,
            "legal_validation": {
                "ok": validation_ok and not legal_issues,
                "issues": legal_issues,
            },
        },
        "meta": {
            "memory_mode": False,
            "memory_mode_enabled": False,
            "execution_path": "classic_multipass",
            "memory_llm_call_count": total_llm_calls,
            "memory_report_risk_count": len(merged_risks),
            "memory_validation_ok": validation_ok and not legal_issues,
            "audit_incomplete": audit_incomplete,
            "classic_parse_failed": parse_failed_chunks > 0,
            "classic_response_empty": response_empty_chunks > 0,
            "classic_response_truncated": response_truncated_chunks > 0,
            "classic_completion_tokens": completion_tokens,
            "classic_max_tokens": max_output_tokens,
            "parse_failed_clauses": parse_failed_chunks,
            "classic_chunk_count": len(results),
        },
        "raw": {"mode": "classic_multipass"},
    }


def _build_classic_audit(
    cfg: Dict[str, Any],
    llm,
    text: str,
    lang: str,
    preview_clauses: List[Dict[str, Any]],
    preview_manifest: Optional[Dict[str, Any]],
    evidence_items: List[Dict[str, Any]],
    retrieval_opts: Dict[str, Any],
    audit_id: str,
) -> Dict[str, Any]:
    norm_lang = "en" if str(lang or "").lower() == "en" else "zh"
    runtime_cfg = _get_memory_runtime_config(cfg)
    allowed_citation_ids = {
        str(it.get("citation_id") or "").strip()
        for it in evidence_items
        if str(it.get("citation_id") or "").strip()
    }
    lines = []
    for idx, it in enumerate(list(evidence_items or [])[:24], start=1):
        cid = str(it.get("citation_id") or "").strip()
        law = str(it.get("law_title") or it.get("title") or "").strip()
        article = str(it.get("article_no") or "").strip()
        if not cid or not law:
            continue
        lines.append(f"- C{idx} [{cid}] {law} {article}".strip())
    whitelist_text = "\n".join(lines) if lines else "-"
    max_clause_chars = int(runtime_cfg.get(
        "memory_max_prompt_chars_per_clause") or 2400)
    classic_audit_max_tokens = int(
        runtime_cfg.get("classic_audit_max_tokens") or CLASSIC_AUDIT_MAX_TOKENS
    )
    classic_audit_retry_max_tokens = max(
        classic_audit_max_tokens,
        int(
            runtime_cfg.get("classic_audit_retry_max_tokens")
            or CLASSIC_AUDIT_RETRY_MAX_TOKENS
        ),
    )
    clause_lines = []
    for c in list(preview_clauses or []):
        cid = str(c.get("clause_id") or "")
        title = str(c.get("title") or c.get("clause_path") or "")
        body = str(c.get("clause_text") or c.get("text") or "")
        clause_lines.append(f"[{cid}] {title}\n{body[:max_clause_chars]}")
    clause_text = "\n\n".join(clause_lines) if clause_lines else str(
        text or "")[:max_clause_chars]

    prompt_context = {
        "lang": norm_lang,
        "whitelist_text": whitelist_text,
        "contract_clauses": clause_text,
        "audit_mode": str(retrieval_opts.get("audit_mode") or ""),
        "json_schema": "{\"summary\":\"\",\"risks\":[{\"level\":\"high|medium|low\",\"issue\":\"\",\"suggestion\":\"\",\"citation_id\":\"\",\"law_title\":\"\",\"article_no\":\"\",\"evidence\":\"\",\"confidence\":0.0,\"clause_id\":\"\"}]}",
    }
    try:
        bound_prompt = resolve_bound_prompt_text(
            "contract_audit.classic_json",
            "",
            prompt_context,
            cfg,
        )
    except KeyError:
        bound_prompt = {
            "rendered_system_text": (
                "You are a contract audit assistant. Output strict JSON only."
                if norm_lang == "en"
                else "你是合同审计助手，仅输出严格JSON。"
            ),
            "rendered_text": (
                (
                    "Review the contract clauses with the regulation whitelist and return strict JSON. "
                    f"Regulation whitelist:\n{whitelist_text}\nContract clauses:\n{clause_text}"
                )
                if norm_lang == "en"
                else (
                    "请结合法规白名单审查合同条款，并返回严格 JSON。"
                    f"\n法规白名单：\n{whitelist_text}\n合同条款：\n{clause_text}"
                )
            ),
            "source": "inline_fallback",
            "prompt_id": "contract_audit.classic_json",
            "prompt_name": "contract_audit.classic_json",
        }
    system = str(bound_prompt.get("rendered_system_text") or "")
    user = str(bound_prompt.get("rendered_text") or "")

    trace_meta = {
        "module": "contract_audit",
        "stage": "contract_classic_audit",
        "audit_id": audit_id,
        "lang": norm_lang,
        "audit_mode": str(retrieval_opts.get("audit_mode") or ""),
        "prompt_source": str(bound_prompt.get("source") or "system_default"),
        "prompt_binding_scene": "contract_audit.classic_json",
        "prompt_binding_id": str(bound_prompt.get("prompt_id") or ""),
        "prompt_binding_name": str(bound_prompt.get("prompt_name") or ""),
    }

    def _run_once(max_tokens: int) -> Dict[str, Any]:
        result_text, raw = _chat_with_task_profile(
            llm,
            [{"role": "system", "content": system},
             {"role": "user", "content": user}],
            "contract_audit_main",
            overrides={"max_tokens": max_tokens, "enable_thinking": False, "reasoning_effort": "low",
                       "thinking_budget_tokens": 0, "_trace_meta": trace_meta},
        )
        parsed = _load_llm_json_object(result_text)
        usage = _extract_usage(raw if isinstance(raw, dict) else {})
        finish_reason = _extract_finish_reason(
            raw if isinstance(raw, dict) else {})
        response_empty = not str(result_text or "").strip()
        parse_failed = not bool(parsed)
        response_truncated = finish_reason == "length"
        return {
            "result_text": str(result_text or ""),
            "raw": raw if isinstance(raw, dict) else {},
            "parsed": parsed,
            "usage": usage,
            "parse_failed": parse_failed,
            "response_empty": response_empty,
            "response_truncated": response_truncated,
            "finish_reason": finish_reason,
            "max_tokens": int(max_tokens),
        }

    attempt = _run_once(classic_audit_max_tokens)
    retry_used = False
    if attempt["parse_failed"] or attempt["response_empty"]:
        retry_attempt = _run_once(classic_audit_retry_max_tokens)
        if (not retry_attempt["parse_failed"]) or (
            retry_attempt["usage"]["completion_tokens"] >= attempt["usage"]["completion_tokens"]
        ):
            attempt = retry_attempt
            retry_used = True

    result_text = str(attempt["result_text"] or "")
    parsed = attempt["parsed"] if isinstance(attempt["parsed"], dict) else {}
    usage = attempt["usage"] if isinstance(attempt["usage"], dict) else {}
    completion_tokens = _safe_int(usage.get("completion_tokens", 0), 0)
    prompt_tokens = _safe_int(usage.get("prompt_tokens", 0), 0)
    parse_failed = bool(attempt.get("parse_failed"))
    response_empty = bool(attempt.get("response_empty"))
    response_truncated = bool(attempt.get("response_truncated"))
    audit_incomplete = parse_failed or response_empty or response_truncated
    risks = parsed.get("risks") if isinstance(
        parsed.get("risks"), list) else []
    clause_map = {str(c.get("clause_id") or ""): c for c in preview_clauses}
    normalized_risks = []
    for idx, r in enumerate(risks, start=1):
        if not isinstance(r, dict):
            continue
        level = str(r.get("level") or "medium").strip().lower()
        if level not in {"high", "medium", "low"}:
            level = "medium"
        clause_id = str(r.get("clause_id") or "")
        c = clause_map.get(clause_id) or (
            preview_clauses[0] if preview_clauses else {})
        input_cid = str(r.get("citation_id") or "").strip()
        mapped_cid = input_cid if input_cid in allowed_citation_ids else ""
        law_title = str(r.get("law_title") or "")
        article_no = str(r.get("article_no") or "")
        basis = f"{law_title} {article_no}".strip()
        normalized_risks.append(
            {
                "level": level,
                "issue": str(r.get("issue") or ""),
                "suggestion": str(r.get("suggestion") or ""),
                "basis": basis,
                "law_reference": basis,
                "citation_id": mapped_cid,
                "citation_status": "mapped" if mapped_cid else "unmapped",
                "evidence": str(r.get("evidence") or ""),
                "law_title": law_title,
                "article_no": article_no,
                "location": {
                    "risk_id": f"classic-r{idx}",
                    "clause_id": str(c.get("clause_id") or ""),
                    "anchor_id": str(c.get("anchor_id") or ""),
                    "page_no": 0,
                    "paragraph_no": str(c.get("paragraph_no") or ""),
                    "clause_path": str(c.get("clause_path") or ""),
                    "quote": str(r.get("evidence") or ""),
                    "score": round(float(r.get("confidence") or 0.0), 4),
                },
            }
        )
    risk_summary = {"high": 0, "medium": 0, "low": 0}
    for item in normalized_risks:
        risk_summary[str(item.get("level") or "low")] += 1
    summary = str(parsed.get("summary") or "").strip()
    legal_issues: List[Dict[str, Any]] = []
    if parse_failed or response_empty:
        legal_issues.append(
            {
                "risk_id": "classic_parse",
                "message": (
                    f"LLM 未返回可解析 JSON（completion_tokens={completion_tokens}/{attempt['max_tokens']}），审计结果可能不完整"
                    if norm_lang != "en"
                    else f"LLM did not return parseable JSON (completion_tokens={completion_tokens}/{attempt['max_tokens']}), audit result may be incomplete"
                ),
            }
        )
    if response_truncated:
        legal_issues.append(
            {
                "risk_id": "classic_truncation",
                "message": (
                    f"LLM 响应被 max_tokens={attempt['max_tokens']} 截断，部分风险可能丢失"
                    if norm_lang != "en"
                    else f"LLM response was truncated at max_tokens={attempt['max_tokens']}, some risks may be missing"
                ),
            }
        )
    if audit_incomplete:
        summary = (
            f"经典审计结果可能不完整：当前识别 {len(normalized_risks)} 项风险，请结合日志复核"
            if norm_lang != "en"
            else f"Classic audit may be incomplete: found {len(normalized_risks)} risks, please review logs"
        )
    elif not summary:
        summary = (
            f"经典审计完成，共发现 {len(normalized_risks)} 项风险"
            if norm_lang != "en"
            else f"Classic audit completed, found {len(normalized_risks)} risks"
        )
    audit = {
        "summary": summary,
        "executive_opinion": [],
        "risk_summary": risk_summary,
        "risks": normalized_risks,
        "citations": _enrich_citations(evidence_items, evidence_items),
        "audit_incomplete": audit_incomplete,
        "legal_validation": {
            "ok": len(legal_issues) == 0,
            "issues": legal_issues,
        },
    }
    audit = attach_risk_locations(
        audit,
        preview_clauses,
        preview_manifest=preview_manifest,
    )
    return {
        "audit": audit,
        "meta": {
            "memory_mode": False,
            "memory_mode_enabled": False,
            "execution_path": "classic",
            "memory_llm_call_count": 1 + (1 if retry_used else 0),
            "memory_report_risk_count": len(normalized_risks),
            "memory_validation_ok": len(legal_issues) == 0,
            "audit_incomplete": audit_incomplete,
            "classic_parse_failed": parse_failed,
            "classic_response_empty": response_empty,
            "classic_response_truncated": response_truncated,
            "classic_completion_tokens": completion_tokens,
            "classic_prompt_tokens": prompt_tokens,
            "classic_max_tokens": int(attempt["max_tokens"]),
            "classic_retry_used": retry_used,
            "parse_failed_clauses": 1 if parse_failed else 0,
        },
        "raw": {"mode": "classic"},
    }


def _parse_citation_pack_ref(citation_id: str) -> Dict[str, str]:
    cid = str(citation_id or "").strip()
    if not cid:
        return {"regulation_id": "", "version_id": ""}
    parts = cid.split(":")
    if len(parts) >= 4:
        return {
            "regulation_id": str(parts[1] or "").strip(),
            "version_id": str(parts[2] or "").strip(),
        }
    return {"regulation_id": "", "version_id": ""}


def get_regulation_pack_fingerprint(
    evidence_items: List[Dict[str, Any]],
    retrieval_opts: Optional[Dict[str, Any]] = None,
    lang: str = "zh",
) -> Dict[str, Any]:
    """
    Build deterministic regulation pack identity for memory isolation.
    This function maps to capability: GetRegulationPackFingerprint.
    """
    members: List[str] = []
    citation_refs: List[str] = []
    for item in list(evidence_items or []):
        citation_id = str(item.get("citation_id") or "").strip()
        if citation_id:
            citation_refs.append(citation_id)
        reg_id = str(item.get("regulation_id") or "").strip()
        ver_id = str(item.get("version_id") or "").strip()
        if not reg_id or not ver_id:
            parsed = _parse_citation_pack_ref(citation_id)
            reg_id = reg_id or parsed.get("regulation_id", "")
            ver_id = ver_id or parsed.get("version_id", "")
        if reg_id and ver_id:
            members.append(f"{reg_id}:{ver_id}")
        elif citation_id:
            members.append(f"cid:{citation_id}")
        else:
            law = str(item.get("law_title") or item.get("title") or "").strip()
            article = str(item.get("article_no") or "").strip()
            if law or article:
                members.append(f"law:{law}#{article}")
    members = sorted(set(members))
    citation_refs = sorted(set(citation_refs))

    opts = dict(retrieval_opts or {})
    scope = {
        "lang": str(lang or "zh"),
        "region": str(opts.get("region") or ""),
        "industry": str(opts.get("industry") or ""),
        "date": str(opts.get("date") or ""),
    }
    pack_seed = "|".join(members) if members else f"empty_pack:{scope['lang']}"
    regulation_pack_id = "rp_" + \
        hashlib.sha1(pack_seed.encode("utf-8")).hexdigest()[:20]
    fingerprint_payload = {
        "pack_id": regulation_pack_id,
        "members": members,
        "citations": citation_refs,
        "scope": scope,
    }
    regulation_fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, ensure_ascii=False,
                   sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "regulation_pack_id": regulation_pack_id,
        "regulation_fingerprint": regulation_fingerprint,
        "regulation_pack_members": members,
    }


def _run_classic_multipass_audit(
    cfg: Dict[str, Any],
    llm,
    text: str,
    lang: str,
    preview_clauses: List[Dict[str, Any]],
    preview_manifest: Optional[Dict[str, Any]],
    evidence_items: List[Dict[str, Any]],
    retrieval_opts: Dict[str, Any],
    audit_id: str,
) -> Dict[str, Any]:
    runtime_cfg = _get_memory_runtime_config(cfg)
    max_calls = max(1, int(runtime_cfg.get(
        "memory_max_llm_calls_per_audit") or 12))
    clause_chunks = _chunk_preview_clauses(
        preview_clauses, max_calls=max_calls)
    if len(clause_chunks) <= 1:
        return _build_classic_audit(
            cfg=cfg,
            llm=llm,
            text=text,
            lang=lang,
            preview_clauses=preview_clauses,
            preview_manifest=preview_manifest,
            evidence_items=evidence_items,
            retrieval_opts=retrieval_opts,
            audit_id=audit_id,
        )
    chunk_results: List[Dict[str, Any]] = []
    for index, clause_chunk in enumerate(clause_chunks, start=1):
        chunk_results.append(
            _build_classic_audit(
                cfg=cfg,
                llm=llm,
                text=text,
                lang=lang,
                preview_clauses=clause_chunk,
                preview_manifest=preview_manifest,
                evidence_items=evidence_items,
                retrieval_opts=retrieval_opts,
                audit_id=f"{audit_id}_chunk_{index}",
            )
        )
    return _aggregate_classic_results(chunk_results, lang=lang)


def audit_contract(
    cfg: Dict[str, Any],
    llm,
    file_path: str,
    preview_file_path: Optional[str] = None,
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
    preview_manifest = None
    preview_source_path = str(preview_file_path or file_path or "").strip()
    if preview_source_path:
        try:
            preview_manifest = build_contract_preview_manifest(
                cfg=cfg,
                document_id=audit_id,
                file_path=preview_source_path,
                mime_type="",
            )
        except Exception:
            logger.exception(
                "audit_preview_manifest_build_failed audit_id=%s file=%s",
                audit_id,
                preview_source_path,
            )
    logger.info(
        "audit_extract_done",
        file=file_path,
        text_length=len(text),
        ocr_used=meta.get("ocr_used"),
        ocr_engine=meta.get("ocr_engine"),
        page_count=meta.get("page_count")
    )
    _report("extract_done", 30, "extract complete")
    requested_mode = _requested_audit_mode(retrieval_options)
    if requested_mode in SKILL_AUDIT_MODES:
        logger.info(
            "audit_skill_orchestrated_start",
            file=file_path,
            audit_id=audit_id,
            requested_mode=requested_mode,
            clause_count=len(preview_clauses),
        )
        _report("auditing", 45, "running skill orchestrated audit")
        skill_embedder = embedder
        if skill_embedder is None:
            try:
                skill_embedder = _get_memory_embedder(lang, cfg)
            except TypeError:
                skill_embedder = _get_memory_embedder()
        result = _run_skill_orchestrated_audit(
            cfg=cfg,
            llm=llm,
            file_path=file_path,
            text=text,
            lang=lang,
            preview_clauses=preview_clauses,
            preview_manifest=preview_manifest,
            embedder=skill_embedder,
            progress_cb=progress_cb,
        )
        result_meta = result.get("meta") if isinstance(
            result.get("meta"), dict) else {}
        result_meta.update(
            {
                "retrieval_mode": requested_mode,
                "ocr_used": bool(meta.get("ocr_used")),
                "ocr_engine": str(meta.get("ocr_engine") or ""),
                "page_count": _safe_int(meta.get("page_count"), 0),
                "llm_model": (cfg.get("llm_config") or {}).get("model", ""),
                "preview_clause_total": len(preview_clauses),
                "evidence_count": len((result.get("audit") or {}).get("citations") or []),
                "retrieval_queries": 0,
                "audit_elapsed_ms": int((time.perf_counter() - audit_started_at) * 1000),
            }
        )
        result["meta"] = result_meta
        _report("done", 100, "skill audit complete")
        return result
    opts = _normalize_retrieval_options(retrieval_options)
    _report("retrieval", 40, "retrieving evidence")
    retrieval_embedder = embedder
    if retrieval_embedder is None:
        try:
            retrieval_embedder = _get_memory_embedder(lang, cfg)
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
    regulation_identity = get_regulation_pack_fingerprint(
        evidence_items=evidence_items,
        retrieval_opts=opts,
        lang=lang,
    )
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
            "regulation_pack_id": regulation_identity.get("regulation_pack_id", ""),
            "regulation_fingerprint": regulation_identity.get("regulation_fingerprint", ""),
            "regulation_pack_members": regulation_identity.get("regulation_pack_members", []),
        },
    )
    memory_runtime_cfg = _get_memory_runtime_config(cfg)
    memory_enabled = bool(memory_runtime_cfg.get(
        "memory_module_enabled", True))
    fallback_on_error = bool(memory_runtime_cfg.get(
        "memory_disable_fallback_on_error", True))
    execution_path = "memory"
    memory_result: Dict[str, Any] = {}
    use_classic_multipass = len(
        preview_clauses) > CLASSIC_MULTIPASS_TRIGGER_CLAUSES

    def _run_classic_path(path_name: str) -> Dict[str, Any]:
        if use_classic_multipass:
            result = _run_classic_multipass_audit(
                cfg=cfg,
                llm=llm,
                text=text,
                lang=lang,
                preview_clauses=preview_clauses,
                preview_manifest=preview_manifest,
                evidence_items=evidence_items,
                retrieval_opts=opts,
                audit_id=audit_id,
            )
        else:
            result = _build_classic_audit(
                cfg=cfg,
                llm=llm,
                text=text,
                lang=lang,
                preview_clauses=preview_clauses,
                preview_manifest=preview_manifest,
                evidence_items=evidence_items,
                retrieval_opts=opts,
                audit_id=audit_id,
            )
        result_meta = result.get("meta") if isinstance(
            result.get("meta"), dict) else {}
        result_meta["execution_path"] = path_name
        result["meta"] = result_meta
        raw = result.get("raw") if isinstance(result.get("raw"), dict) else {}
        raw["mode"] = path_name
        result["raw"] = raw
        return result

    _report("auditing", 70, "auditing clauses")
    if memory_enabled:
        logger.info("audit_memory_enabled", file=file_path,
                    clauses=len(preview_clauses))
        try:
            from app.services.contract_audit_modules import memory_pipeline as memory_pipeline_module
            from app.services.contract_audit_modules.memory_pipeline import execute_memory_audit
        except Exception as e:
            if not fallback_on_error:
                raise
            logger.warning("memory_audit_import_failed_fallback_to_classic",
                           audit_id=audit_id, error=str(e))
            execution_path = "classic_multipass_fallback" if use_classic_multipass else "classic_fallback"
            memory_result = _run_classic_path(execution_path)
            _report("audit_done", 90, "audit complete")
            memory_meta = memory_result.get("meta") if isinstance(
                memory_result.get("meta"), dict) else {}
            citation_ids = [
                str(it.get("citation_id", "")).strip()
                for it in evidence_items
                if str(it.get("citation_id", "")).strip()
            ]
            audit_duration_ms = int(
                (time.perf_counter() - audit_started_at) * 1000)
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
                "regulation_pack_id": regulation_identity.get("regulation_pack_id", ""),
                "regulation_fingerprint": regulation_identity.get("regulation_fingerprint", ""),
                "regulation_pack_members": regulation_identity.get("regulation_pack_members", []),
                "memory_module_enabled": memory_enabled,
                "execution_path": execution_path,
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
            episode_meta = {
                "saved": False,
                "reason": "memory_import_failed_fallback",
            }
            output_meta["episode_saved"] = bool(
                episode_meta.get("saved", False))
            output_meta["episode_id"] = str(
                episode_meta.get("episode_id") or "")
            output_meta["episode_status"] = str(
                episode_meta.get("status") or "")
            return {
                "audit": memory_result.get("audit"),
                "meta": output_meta,
                "raw": memory_result.get("raw") if isinstance(memory_result.get("raw"), dict) else {"mode": "memory"}
            }
        custom_embedder = None
        if callable(_get_memory_embedder):
            try:
                custom_embedder = _get_memory_embedder(lang, cfg)
            except TypeError:
                custom_embedder = _get_memory_embedder()
        HybridSearcher = None
        try:
            from app.memory_system.search import HybridSearcher as _HybridSearcher
            HybridSearcher = _HybridSearcher
        except Exception:
            HybridSearcher = None
        if (custom_embedder is not None and hasattr(custom_embedder, "encode")) or HybridSearcher is not None:
            runtime_getter = (lambda _lang="zh", cfg=None: custom_embedder) if (
                custom_embedder is not None and hasattr(
                    custom_embedder, "encode")
            ) else None
            setter = getattr(memory_pipeline_module,
                             "set_runtime_overrides", None)
            if callable(setter):
                setter(
                    get_memory_embedder=runtime_getter,
                    hybrid_searcher=HybridSearcher if HybridSearcher is not None else None,
                )
        try:
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
                    "regulation_pack_id": regulation_identity.get("regulation_pack_id", ""),
                    "regulation_fingerprint": regulation_identity.get("regulation_fingerprint", ""),
                },
            )
            execution_path = "memory"
        except Exception as e:
            if not fallback_on_error:
                raise
            logger.warning("memory_audit_failed_fallback_to_classic",
                           audit_id=audit_id, error=str(e))
            execution_path = "classic_multipass_fallback" if use_classic_multipass else "classic_fallback"
            memory_result = _run_classic_path(execution_path)
    else:
        logger.info("audit_memory_disabled_use_classic",
                    audit_id=audit_id, file=file_path)
        execution_path = "classic_multipass" if use_classic_multipass else "classic"
        memory_result = _run_classic_path(execution_path)
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
        "regulation_pack_id": regulation_identity.get("regulation_pack_id", ""),
        "regulation_fingerprint": regulation_identity.get("regulation_fingerprint", ""),
        "regulation_pack_members": regulation_identity.get("regulation_pack_members", []),
        "memory_module_enabled": memory_enabled,
        "execution_path": execution_path,
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
    episode_meta: Dict[str, Any] = {}
    if execution_path == "memory":
        try:
            from app.memory_system.experience_repo import save_audit_episode
            episode_meta = save_audit_episode(
                cfg=cfg,
                audit_id=audit_id,
                regulation_pack_id=regulation_identity.get(
                    "regulation_pack_id", ""),
                regulation_fingerprint=regulation_identity.get(
                    "regulation_fingerprint", ""),
                retrieval_opts=opts,
                preview_clauses=preview_clauses,
                audit=memory_result.get("audit") if isinstance(
                    memory_result.get("audit"), dict) else {},
                meta=output_meta,
            )
        except Exception as e:
            logger.warning("save_audit_episode_failed",
                           audit_id=audit_id, error=str(e))
            episode_meta = {"saved": False,
                            "reason": "exception", "error": str(e)}
    else:
        episode_meta = {
            "saved": False,
            "reason": "memory_module_disabled_or_fallback",
        }
    output_meta["episode_saved"] = bool(episode_meta.get("saved", False))
    output_meta["episode_id"] = str(episode_meta.get("episode_id") or "")
    output_meta["episode_status"] = str(episode_meta.get("status") or "")
    return {
        "audit": memory_result.get("audit"),
        "meta": output_meta,
        "raw": memory_result.get("raw") if isinstance(memory_result.get("raw"), dict) else {"mode": "memory"}
    }
