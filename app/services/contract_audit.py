"""
Contract Audit Facade.
职责: 作为合同审计模块的统一入口，封装 IO 操作并调用拆分后的子模块。
"""
import os
import time
import uuid
import hashlib
import json
import re
import socket
import structlog
from typing import Dict, Any, Optional, Callable, List

from app.core.token_utils import estimate_text_tokens
from app.services.final_report_service import build_contract_final_report
from app.services.result_aggregator import aggregate_chunk_audit_results
from app.services.review_retry import build_reliability_review_plan, build_reliability_summary
from app.core.utils import extract_text_with_config
from app.services.audit_token_policy import build_contract_audit_budget, plan_clause_groups
from app.services.audit_utils import _safe_int, _normalize_citation_item, _enrich_citations
from app.services.audit_retrieval import _normalize_retrieval_options, _retrieve_regulation_evidence
from app.services.contract_audit_modules.clause_builder import build_preview_clauses
from app.services.contract_audit_modules import memory_pipeline as memory_pipeline_module
from app.services.contract_audit_modules.memory_pipeline import execute_memory_audit
from app.services.contract_audit_modules.result_assembler import attach_risk_locations
from app.services.contract_audit_modules.trace_writer import write_audit_trace, write_round_trace, trace_clip
from app.memory_system.search import HybridSearcher
from app.memory_system.experience_repo import save_audit_episode
from app.core.llm_trace import new_trace_id

logger = structlog.get_logger(__name__)


def _build_preview_clauses(text: str):
    return build_preview_clauses(text)


def _attach_risk_locations(audit, clauses):
    return attach_risk_locations(audit, clauses)


def _chat_with_task_profile(llm, messages, task_profile: str, overrides=None):
    if hasattr(llm, "chat_with_profile"):
        return llm.chat_with_profile(messages, task_profile, overrides=overrides)
    next_overrides = dict(overrides or {})
    next_overrides["_task_profile"] = task_profile
    return llm.chat(messages, overrides=next_overrides)


def _get_memory_embedder(lang: str = "zh", cfg: Optional[Dict[str, Any]] = None):
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
    return {
        "memory_module_enabled": bool(raw.get("memory_module_enabled", True)),
        "memory_mode_when_disabled": mode,
        "memory_disable_fallback_on_error": bool(raw.get("memory_disable_fallback_on_error", True)),
        "memory_max_prompt_chars_per_clause": max(300, int(raw.get("memory_max_prompt_chars_per_clause") or 2400)),
    }


def _get_memory_temporary_disable_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    raw = cfg.get("memory_temporary_disable") if isinstance(
        cfg.get("memory_temporary_disable"), dict) else {}
    fallback_mode = str(raw.get("fallback_mode") or "classic").strip().lower()
    if fallback_mode not in {"classic"}:
        fallback_mode = "classic"
    return {
        "enabled": bool(raw.get("enabled", False)),
        "fallback_mode": fallback_mode,
        "reason": str(raw.get("reason") or "edge_llm_context_limit").strip() or "edge_llm_context_limit",
        "trigger_source": str(raw.get("trigger_source") or "config.memory_temporary_disable").strip() or "config.memory_temporary_disable",
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


def _build_classic_prompt_payload(
    cfg: Dict[str, Any],
    text: str,
    lang: str,
    preview_clauses: List[Dict[str, Any]],
    evidence_items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    norm_lang = "en" if str(lang or "").lower() == "en" else "zh"
    allowed_citation_ids = {
        str(it.get("citation_id") or "").strip()
        for it in evidence_items
        if str(it.get("citation_id") or "").strip()
    }
    evidence_items_used = list(evidence_items or [])[:24]
    lines = []
    evidence_content_truncated_count = 0
    reference_lines = []
    for idx, it in enumerate(evidence_items_used, start=1):
        cid = str(it.get("citation_id") or "").strip()
        law = str(it.get("law_title") or it.get("title") or "").strip()
        article = str(it.get("article_no") or "").strip()
        if cid and law:
            lines.append(f"- C{idx} [{cid}] {law} {article}".strip())
        content = str(it.get("content") or it.get("source_text")
                      or it.get("excerpt") or "").strip()
        if len(content) > 1200:
            evidence_content_truncated_count += 1
        header = f"[E{idx}] {law} {article}".strip()
        if content:
            reference_lines.append(f"{header}\n{content[:1200]}")
        elif law:
            reference_lines.append(header)
    whitelist_text = "\n".join(lines) if lines else "-"
    reference_evidence_text = "\n\n".join(
        reference_lines) if reference_lines else ""
    if reference_evidence_text:
        reference_evidence_text = (
            "参考法规证据（共{}条）:\n".format(len(reference_lines))
            + reference_evidence_text
        )
    max_clause_chars = int(_get_memory_runtime_config(
        cfg).get("memory_max_prompt_chars_per_clause") or 2400)
    prompt_clauses = list(preview_clauses or [])[:10]
    clause_lines = []
    clause_chars_truncated_count = 0
    for clause in prompt_clauses:
        clause_id = str(clause.get("clause_id") or "")
        title = str(clause.get("title") or clause.get("clause_path") or "")
        body = str(clause.get("clause_text") or clause.get("text") or "")
        if len(body) > max_clause_chars:
            clause_chars_truncated_count += 1
        clause_lines.append(
            f"[{clause_id}] {title}\n{body[:max_clause_chars]}")
    clause_text = "\n\n".join(clause_lines) if clause_lines else str(
        text or "")[:max_clause_chars]
    full_ctx_budget = max(
        1500, int(cfg.get("memory_full_context_budget_chars") or 6000))
    full_text_context = str(text or "")[:full_ctx_budget]
    if norm_lang == "en":
        system = "You are a senior tax contract audit lawyer. Output ONLY JSON. /no_think"
        user = "Use only the contract text and reference evidence below.\n"
        user += "Do not output reasoning process.\n\n"
        user += "TAX RISK ONLY: Only output tax-related risks (tax rate, invoicing, tax obligations, withholding tax, tax compliance). "
        user += "Do NOT output contract clause descriptions, breach of contract, or payment obligation issues that are not tax risks.\n"
        user += "CITATION REQUIRED: Every risk MUST include a citation_id from the whitelist and the corresponding law_title. "
        user += "If you cannot match a risk to a regulation in the whitelist, do NOT output that risk.\n"
        user += "RISK DESCRIPTION: The 'issue' field must describe the specific tax compliance risk and why it violates or conflicts with the cited regulation. "
        user += "Do NOT simply copy contract clause text as the issue.\n"
        user += "CROSS-CLAUSE RULE: Before flagging 'missing/unspecified' risks, verify against the full contract text. "
        user += "If the element is covered elsewhere in the contract, do NOT flag it.\n"
        if reference_evidence_text:
            user += f"{reference_evidence_text}\n\n"
        user += f"Whitelist (citation IDs):\n{whitelist_text}\n\n"
        user += f"Full Contract Text (for cross-clause verification):\n{full_text_context}\n\n"
        user += f"Contract Clauses (structured):\n{clause_text}\n\n"
        user += "JSON: {\"summary\":\"\",\"risks\":[{\"level\":\"high|medium|low\",\"issue\":\"\",\"suggestion\":\"\",\"citation_id\":\"\",\"law_title\":\"\",\"article_no\":\"\",\"evidence\":\"\",\"confidence\":0.0,\"clause_id\":\"\"}]}"
    else:
        system = "你是资深财税合同审计律师。只输出JSON。/no_think"
        user = "仅根据合同文本与参考法规证据输出结果；不要输出推理过程。\n\n"
        user += "【仅限涉税风险】只输出涉税风险（税率、开票、纳税义务、代扣代缴、税务合规等）。"
        user += "不要输出合同条款描述、违约责任、支付义务等非涉税内容。\n"
        user += "【强制引用】每个风险项必须包含 citation_id（从白名单中选择）和对应的 law_title。"
        user += "无法从白名单中匹配到法规条款的风险，不得输出。\n"
        user += "【风险描述】issue 字段必须描述具体的税务合规风险及其违反或冲突的法规依据，不得仅复制合同条款原文。\n"
        user += "【跨条款联合校验】标记'缺失/未约定'风险前，必须核查合同全文。若该要素已在其他条款中约定，不得标记为风险。\n"
        if reference_evidence_text:
            user += f"{reference_evidence_text}\n\n"
        user += f"白名单（引用ID）:\n{whitelist_text}\n\n"
        user += f"合同全文(用于跨条款联合校验):\n{full_text_context}\n\n"
        user += f"合同条款(结构化拆分):\n{clause_text}\n\n"
        user += "JSON: {\"summary\":\"\",\"risks\":[{\"level\":\"high|medium|low\",\"issue\":\"\",\"suggestion\":\"\",\"citation_id\":\"\",\"law_title\":\"\",\"article_no\":\"\",\"evidence\":\"\",\"confidence\":0.0,\"clause_id\":\"\"}]}"
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    return {
        "norm_lang": norm_lang,
        "messages": messages,
        "allowed_citation_ids": allowed_citation_ids,
        "prompt_meta": {
            "prompt_clause_count": len(prompt_clauses),
            "total_clause_count": len(preview_clauses or []),
            "clauses_omitted": max(0, len(list(preview_clauses or [])) - len(prompt_clauses)),
            "clause_chars_truncated_count": clause_chars_truncated_count,
            "full_text_context_chars": len(full_text_context),
            "full_text_context_truncated": len(str(text or "")) > full_ctx_budget,
            "full_text_context_budget_chars": full_ctx_budget,
            "evidence_items_used": len(evidence_items_used),
            "evidence_items_omitted": max(0, len(list(evidence_items or [])) - len(evidence_items_used)),
            "evidence_content_truncated_count": evidence_content_truncated_count,
            "whitelist_line_count": len(lines),
        },
    }


def _build_classic_audit(
    cfg: Dict[str, Any],
    llm,
    text: str,
    lang: str,
    preview_clauses: List[Dict[str, Any]],
    evidence_items: List[Dict[str, Any]],
    retrieval_opts: Dict[str, Any],
    audit_id: str,
    trace_id: str = "",
    prompt_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    prompt_payload = prompt_payload or _build_classic_prompt_payload(
        cfg=cfg,
        text=text,
        lang=lang,
        preview_clauses=preview_clauses,
        evidence_items=evidence_items,
    )
    norm_lang = str(prompt_payload.get("norm_lang") or "zh")
    allowed_citation_ids = set(
        prompt_payload.get("allowed_citation_ids") or set())
    messages = list(prompt_payload.get("messages") or [])

    trace_meta = {
        "module": "contract_audit",
        "stage": "contract_classic_audit",
        "audit_id": audit_id,
        "trace_id": trace_id,
        "lang": norm_lang,
        "audit_mode": str(retrieval_opts.get("audit_mode") or ""),
    }
    CLASSIC_AUDIT_MAX_TOKENS = int(cfg.get("classic_audit_max_tokens") or 4096)
    result_text, _raw = _chat_with_task_profile(
        llm,
        messages,
        "contract_audit_main",
        overrides={"max_tokens": CLASSIC_AUDIT_MAX_TOKENS, "enable_thinking": False, "reasoning_effort": "low",
                   "thinking_budget_tokens": 0, "_trace_meta": trace_meta},
    )
    parsed = _load_llm_json_object(result_text)
    risks = parsed.get("risks") if isinstance(
        parsed.get("risks"), list) else []

    # ---- Fix #3: 检测响应是否被 max_tokens 截断 ----
    raw_usage = (_raw or {}).get("usage") if isinstance(_raw, dict) else None
    completion_tokens = int((raw_usage or {}).get("completion_tokens") or 0)
    response_truncated = completion_tokens >= CLASSIC_AUDIT_MAX_TOKENS

    # ---- Fix #1: 检测 JSON 解析是否失败 ----
    # 当 LLM 输出非 JSON（如思考链草稿）时，parsed 为空但 result_text 非空
    parse_failed = not parsed and bool(result_text and result_text.strip())
    if parse_failed:
        logger.warning(
            "classic_audit_json_parse_failed",
            audit_id=audit_id,
            response_length=len(result_text),
            completion_tokens=completion_tokens,
            max_tokens=CLASSIC_AUDIT_MAX_TOKENS,
            response_head=trace_clip(result_text, 200),
        )
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
        issue_text = str(r.get("issue") or "").strip()
        evidence_text = str(r.get("evidence") or "").strip()
        # Skip risks with completely empty content (LLM returned a skeleton without substance)
        if len(issue_text) < 6 and len(evidence_text) < 6:
            continue
        # Skip risks without any legal reference (no citation_id AND no law_title)
        # These are likely the model echoing contract text rather than doing tax risk analysis
        if not mapped_cid and not law_title.strip():
            continue
        normalized_risks.append(
            {
                "level": level,
                "issue": issue_text,
                "suggestion": str(r.get("suggestion") or ""),
                "basis": basis,
                "law_reference": basis,
                "citation_id": mapped_cid,
                "citation_status": "mapped" if mapped_cid else "unmapped",
                "evidence": evidence_text,
                "law_title": law_title,
                "article_no": article_no,
                "location": {
                    "risk_id": f"classic-r{idx}",
                    "clause_id": str(c.get("clause_id") or ""),
                    "anchor_id": str(c.get("anchor_id") or ""),
                    "page_no": int(c.get("page_no") or 0),
                    "paragraph_no": str(c.get("paragraph_no") or ""),
                    "clause_path": str(c.get("clause_path") or ""),
                    "quote": evidence_text,
                    "score": round(float(r.get("confidence") or 0.0), 4),
                },
            }
        )
    risk_summary = {"high": 0, "medium": 0, "low": 0}
    for item in normalized_risks:
        risk_summary[str(item.get("level") or "low")] += 1
    summary = str(parsed.get("summary") or "").strip() or (
        f"经典审计完成，共发现 {len(normalized_risks)} 项风险" if norm_lang != "en" else
        f"Classic audit completed, found {len(normalized_risks)} risks"
    )
    # ---- Fix #1: 动态 legal_validation（替代硬编码 ok=True） ----
    legal_issues = []
    if parse_failed:
        legal_issues.append({
            "risk_id": "classic_parse",
            "message": (
                f"LLM 未返回有效 JSON（响应 {len(result_text)} 字符，"
                f"completion_tokens={completion_tokens}/{CLASSIC_AUDIT_MAX_TOKENS}），"
                f"审计结果可能不完整"
            ) if norm_lang != "en" else (
                f"LLM did not return valid JSON (response {len(result_text)} chars, "
                f"completion_tokens={completion_tokens}/{CLASSIC_AUDIT_MAX_TOKENS}), "
                f"audit result may be incomplete"
            ),
        })
    if response_truncated and not parse_failed:
        legal_issues.append({
            "risk_id": "classic_truncation",
            "message": (
                f"LLM 响应被 max_tokens={CLASSIC_AUDIT_MAX_TOKENS} 截断"
                f"（completion_tokens={completion_tokens}），部分风险可能丢失"
            ) if norm_lang != "en" else (
                f"LLM response truncated at max_tokens={CLASSIC_AUDIT_MAX_TOKENS} "
                f"(completion_tokens={completion_tokens}), some risks may be lost"
            ),
        })
    legal_validation = {
        "ok": len(legal_issues) == 0,
        "issues": legal_issues,
    }
    audit = {
        "summary": summary,
        "executive_opinion": [],
        "risk_summary": risk_summary,
        "risks": normalized_risks,
        "citations": _enrich_citations(evidence_items, evidence_items),
        "legal_validation": legal_validation,
    }

    # ---- Traceability: 记录审计全量上下文（证据/条款/LLM处理状态） ----
    write_audit_trace(cfg, "classic_audit_full_context", {
        "audit_id": audit_id,
        "evidence_count": len(evidence_items),
        "evidence_citation_ids": [
            str(it.get("citation_id") or "") for it in evidence_items
        ],
        "evidence_law_titles": [
            trace_clip(str(it.get("law_title") or it.get("title") or ""), 120)
            for it in evidence_items
        ],
        "preview_clause_count": len(preview_clauses),
        "preview_clause_ids": [
            str(c.get("clause_id") or "") for c in preview_clauses
        ],
        "preview_clause_titles": [
            trace_clip(str(c.get("title") or c.get("clause_path") or ""), 120)
            for c in preview_clauses
        ],
        "max_tokens": CLASSIC_AUDIT_MAX_TOKENS,
        "completion_tokens": completion_tokens,
        "response_truncated": response_truncated,
        "parse_failed": parse_failed,
        "response_length": len(result_text),
        "risks_count": len(normalized_risks),
        "raw_response_preview": trace_clip(result_text, 2000),
        "normalized_risks_summary": [
            {
                "level": item.get("level"),
                "law_title": item.get("law_title", ""),
                "article_no": item.get("article_no", ""),
                "issue_preview": trace_clip(item.get("issue", ""), 200),
            }
            for item in normalized_risks
        ],
    })

    return {
        "audit": audit,
        "meta": {
            "memory_mode": False,
            "memory_mode_enabled": False,
            "execution_path": "classic",
            "memory_llm_call_count": 1,
            "memory_report_risk_count": len(normalized_risks),
            "memory_validation_ok": legal_validation["ok"],
            "classic_parse_failed": parse_failed,
            "classic_response_truncated": response_truncated,
            "classic_completion_tokens": completion_tokens,
            "classic_max_tokens": CLASSIC_AUDIT_MAX_TOKENS,
        },
        "raw": {"mode": "classic"},
    }


def _select_multipass_evidence_items(
    evidence_items: List[Dict[str, Any]],
    max_items: int,
) -> List[Dict[str, Any]]:
    def _score(item: Dict[str, Any]) -> float:
        try:
            return float(item.get("final_score") or item.get("score") or item.get("tax_relevance") or 0.0)
        except Exception:
            return 0.0

    ranked = sorted(
        [item for item in list(evidence_items or [])
         if isinstance(item, dict)],
        key=_score,
        reverse=True,
    )
    return ranked[:max_items]


def _build_chunk_clause_range(chunk_id: str, clauses: List[Dict[str, Any]]) -> Dict[str, Any]:
    clause_ids = [str(item.get("clause_id") or "") for item in clauses]
    clause_paths = [str(item.get("clause_path") or item.get(
        "title") or "") for item in clauses]
    page_numbers = [
        int(item.get("page_no") or 0)
        for item in clauses
        if int(item.get("page_no") or 0) > 0
    ]
    return {
        "chunk_id": chunk_id,
        "start_clause_id": clause_ids[0] if clause_ids else "",
        "end_clause_id": clause_ids[-1] if clause_ids else "",
        "clause_ids": clause_ids,
        "clause_paths": clause_paths,
        "page_start": min(page_numbers) if page_numbers else 0,
        "page_end": max(page_numbers) if page_numbers else 0,
        "paragraph_start": str(clauses[0].get("paragraph_no") or "") if clauses else "",
        "paragraph_end": str(clauses[-1].get("paragraph_no") or "") if clauses else "",
    }


def _build_chunk_audit_result(
    *,
    chunk_id: str,
    round_index: int,
    clauses: List[Dict[str, Any]],
    audit_result: Dict[str, Any],
) -> Dict[str, Any]:
    audit = audit_result.get("audit") if isinstance(
        audit_result.get("audit"), dict) else {}
    meta = audit_result.get("meta") if isinstance(
        audit_result.get("meta"), dict) else {}
    risks = [item for item in list(
        audit.get("risks") or []) if isinstance(item, dict)]
    citations = [item for item in list(
        audit.get("citations") or []) if isinstance(item, dict)]
    evidence_links = []
    evidence_seen = set()
    for item in citations:
        key = (
            str(item.get("citation_id") or "").strip(),
            str(item.get("law_title") or item.get("title") or "").strip(),
            str(item.get("article_no") or "").strip(),
        )
        if key in evidence_seen:
            continue
        evidence_seen.add(key)
        evidence_links.append(
            {
                "citation_id": key[0],
                "law_title": key[1],
                "article_no": key[2],
            }
        )
    confidence_values = []
    for risk in risks:
        location = risk.get("location") if isinstance(
            risk.get("location"), dict) else {}
        try:
            confidence_values.append(
                float(location.get("score") or risk.get("confidence") or 0.0))
        except Exception:
            continue
    confidence_score = round(
        sum(confidence_values) / len(confidence_values), 4) if confidence_values else 0.0
    return {
        "chunk_id": chunk_id,
        "round_index": int(round_index or 0),
        "clause_range": _build_chunk_clause_range(chunk_id, clauses),
        "risk_items": risks,
        "risk_count": len(risks),
        "evidence_links": evidence_links,
        "confidence_score": confidence_score,
        "parse_failed_flag": bool(meta.get("classic_parse_failed")),
        "truncated_flag": bool(meta.get("classic_response_truncated")),
        "completion_tokens": int(meta.get("classic_completion_tokens") or 0),
        "max_output_tokens": int(meta.get("classic_max_tokens") or 0),
        "summary": str(audit.get("summary") or ""),
    }


def _run_multipass_chunk_round(
    *,
    cfg: Dict[str, Any],
    llm,
    text: str,
    lang: str,
    clauses: List[Dict[str, Any]],
    selected_evidence: List[Dict[str, Any]],
    retrieval_opts: Dict[str, Any],
    audit_id: str,
    trace_id: str,
    round_index: int,
    chunk_id: str,
    estimated_clause_tokens: int,
    budget_trigger: List[str],
    retry_index: int = 0,
    retry_reasons: Optional[List[str]] = None,
) -> Dict[str, Any]:
    chunk_text = "\n\n".join(
        [
            f"{item.get('clause_path') or item.get('title') or item.get('clause_id') or ''}\n"
            f"{item.get('clause_text') or item.get('text') or ''}"
            for item in clauses
        ]
    )
    chunk_payload = _build_classic_prompt_payload(
        cfg=cfg,
        text=chunk_text or text,
        lang=lang,
        preview_clauses=clauses,
        evidence_items=selected_evidence,
    )
    chunk_result = _build_classic_audit(
        cfg=cfg,
        llm=llm,
        text=chunk_text or text,
        lang=lang,
        preview_clauses=clauses,
        evidence_items=selected_evidence,
        retrieval_opts=retrieval_opts,
        audit_id=f"{audit_id}_r{round_index}_try{retry_index}",
        trace_id=trace_id,
        prompt_payload=chunk_payload,
    )
    round_meta = chunk_result.get("meta") if isinstance(
        chunk_result.get("meta"), dict) else {}
    chunk_audit_result = _build_chunk_audit_result(
        chunk_id=chunk_id,
        round_index=int(round_index or 0),
        clauses=clauses,
        audit_result=chunk_result,
    )
    write_round_trace(
        cfg,
        int(round_index or 0),
        "chunk_audit_done" if retry_index <= 0 else "chunk_retry_done",
        {
            "audit_id": audit_id,
            "chunk_id": chunk_id,
            "retry_index": retry_index,
            "retry_reasons": list(retry_reasons or []),
            "risk_count": chunk_audit_result.get("risk_count"),
            "confidence_score": chunk_audit_result.get("confidence_score"),
            "parse_failed_flag": chunk_audit_result.get("parse_failed_flag"),
            "truncated_flag": chunk_audit_result.get("truncated_flag"),
        },
    )
    return {
        "round_index": round_index,
        "chunk_id": chunk_id,
        "clause_ids": [str(item.get("clause_id") or "") for item in clauses],
        "clause_paths": [str(item.get("clause_path") or item.get("title") or "") for item in clauses],
        "estimated_clause_tokens": estimated_clause_tokens,
        "audit": chunk_result.get("audit"),
        "meta": round_meta,
        "chunk_audit_result": chunk_audit_result,
        "budget_trigger": list(budget_trigger or []),
        "retry_index": int(retry_index or 0),
        "retry_reasons": list(retry_reasons or []),
    }


def _merge_multipass_audits(
    rounds: List[Dict[str, Any]],
    cfg: Dict[str, Any],
    preview_clauses: List[Dict[str, Any]],
    lang: str,
) -> Dict[str, Any]:
    parse_failed_rounds = 0
    truncated_rounds = 0
    chunk_audit_results = []
    for round_result in rounds:
        meta = round_result.get("meta") if isinstance(
            round_result.get("meta"), dict) else {}
        if meta.get("classic_parse_failed"):
            parse_failed_rounds += 1
        if meta.get("classic_response_truncated"):
            truncated_rounds += 1
        if isinstance(round_result.get("chunk_audit_result"), dict):
            chunk_audit_results.append(round_result["chunk_audit_result"])
    aggregated = aggregate_chunk_audit_results(
        cfg=cfg,
        chunk_audit_results=chunk_audit_results,
        preview_clauses=preview_clauses,
        lang=lang,
    )
    aggregated_audit = aggregated.get("audit") if isinstance(
        aggregated.get("audit"), dict) else {}
    aggregated_meta = aggregated.get("meta") if isinstance(
        aggregated.get("meta"), dict) else {}
    legal_validation = aggregated_audit.get("legal_validation") if isinstance(
        aggregated_audit.get("legal_validation"), dict) else {}
    return {
        "audit": aggregated_audit,
        "meta": {
            "memory_mode": False,
            "memory_mode_enabled": False,
            "execution_path": "multipass_classic_stage1",
            "memory_llm_call_count": len(rounds),
            "memory_report_risk_count": len(list(aggregated_audit.get("risks") or [])),
            "memory_validation_ok": bool(legal_validation.get("ok", False)),
            "classic_parse_failed": parse_failed_rounds > 0,
            "classic_response_truncated": truncated_rounds > 0,
            "classic_completion_tokens": sum(
                int((item.get("meta") or {}).get(
                    "classic_completion_tokens") or 0)
                for item in rounds
            ),
            "classic_max_tokens": max(
                [int((item.get("meta") or {}).get("classic_max_tokens") or 0)
                 for item in rounds] or [0]
            ),
            "multipass_round_count": len(rounds),
            "multipass_parse_failed_rounds": parse_failed_rounds,
            "multipass_truncated_rounds": truncated_rounds,
            **aggregated_meta,
        },
        "raw": {
            "mode": "multipass_classic_stage1",
            "rounds": rounds,
            "chunk_audit_results": chunk_audit_results,
            "aggregated_exports": aggregated.get("exports") if isinstance(aggregated.get("exports"), dict) else {},
        },
    }


def _build_multipass_classic_audit(
    cfg: Dict[str, Any],
    llm,
    text: str,
    lang: str,
    preview_clauses: List[Dict[str, Any]],
    evidence_items: List[Dict[str, Any]],
    retrieval_opts: Dict[str, Any],
    audit_id: str,
    trace_id: str,
    token_budget: Dict[str, Any],
) -> Dict[str, Any]:
    policy = dict((token_budget or {}).get("policy") or {})
    clause_plan = plan_clause_groups(preview_clauses, policy)
    selected_evidence = _select_multipass_evidence_items(
        evidence_items,
        int(policy.get("max_evidence_items_per_round") or 12),
    )
    rounds: List[Dict[str, Any]] = []
    clause_group_map: Dict[str, List[Dict[str, Any]]] = {}
    for round_info, group in zip(clause_plan.get("rounds") or [], clause_plan.get("groups") or []):
        chunk_id = f"chunk-{int(round_info.get('round_index') or 0):03d}"
        clause_group_map[chunk_id] = group
        rounds.append(
            _run_multipass_chunk_round(
                cfg=cfg,
                llm=llm,
                text=text,
                lang=lang,
                clauses=group,
                selected_evidence=selected_evidence,
                retrieval_opts=retrieval_opts,
                audit_id=audit_id,
                trace_id=trace_id,
                round_index=int(round_info.get("round_index") or 0),
                chunk_id=chunk_id,
                estimated_clause_tokens=int(
                    round_info.get("estimated_clause_tokens") or 0),
                budget_trigger=list((token_budget or {}).get("reasons") or []),
            )
        )
    merged = _merge_multipass_audits(
        rounds,
        cfg=cfg,
        preview_clauses=preview_clauses,
        lang=lang,
    )
    review_plan = build_reliability_review_plan(
        cfg=cfg,
        aggregated_audit=merged.get("audit") if isinstance(
            merged.get("audit"), dict) else {},
        aggregated_meta=merged.get("meta") if isinstance(
            merged.get("meta"), dict) else {},
        chunk_audit_results=list(
            (merged.get("raw") or {}).get("chunk_audit_results") or []),
    )
    write_audit_trace(
        cfg,
        "audit_review_plan",
        {
            "audit_id": audit_id,
            "retry_chunk_ids": list(review_plan.get("retry_chunk_ids") or []),
            "review_item_count": int(review_plan.get("review_item_count") or 0),
            "should_retry": bool(review_plan.get("should_retry")),
            "initial_reliability_level": str(
                review_plan.get("initial_reliability_level") or "medium"),
        },
    )
    retry_logs: List[Dict[str, Any]] = []
    if bool(review_plan.get("should_retry")):
        round_index_map = {
            str(item.get("chunk_id") or ""): index
            for index, item in enumerate(rounds)
            if isinstance(item, dict)
        }
        max_retry_per_chunk = int(
            ((review_plan.get("policy") or {}).get("max_retry_per_chunk") or 0))
        for retry_target in list(review_plan.get("retry_chunks") or []):
            chunk_id = str(retry_target.get("chunk_id") or "")
            if not chunk_id or chunk_id not in round_index_map:
                continue
            current_round = rounds[round_index_map[chunk_id]]
            current_retry_index = int(current_round.get("retry_index") or 0)
            if current_retry_index >= max_retry_per_chunk:
                retry_logs.append(
                    {
                        "chunk_id": chunk_id,
                        "retry_performed": False,
                        "reason": "retry_limit_reached",
                        "retry_reasons": list(retry_target.get("reasons") or []),
                    }
                )
                continue
            next_round = _run_multipass_chunk_round(
                cfg=cfg,
                llm=llm,
                text=text,
                lang=lang,
                clauses=clause_group_map.get(chunk_id) or [],
                selected_evidence=selected_evidence,
                retrieval_opts=retrieval_opts,
                audit_id=audit_id,
                trace_id=trace_id,
                round_index=int(current_round.get("round_index") or 0),
                chunk_id=chunk_id,
                estimated_clause_tokens=int(
                    current_round.get("estimated_clause_tokens") or 0),
                budget_trigger=list(current_round.get("budget_trigger") or []),
                retry_index=current_retry_index + 1,
                retry_reasons=list(retry_target.get("reasons") or []),
            )
            retry_logs.append(
                {
                    "chunk_id": chunk_id,
                    "retry_performed": True,
                    "retry_index": current_retry_index + 1,
                    "retry_reasons": list(retry_target.get("reasons") or []),
                    "before_parse_failed": bool((current_round.get("chunk_audit_result") or {}).get("parse_failed_flag")),
                    "after_parse_failed": bool((next_round.get("chunk_audit_result") or {}).get("parse_failed_flag")),
                    "before_truncated": bool((current_round.get("chunk_audit_result") or {}).get("truncated_flag")),
                    "after_truncated": bool((next_round.get("chunk_audit_result") or {}).get("truncated_flag")),
                    "before_risk_count": int((current_round.get("chunk_audit_result") or {}).get("risk_count") or 0),
                    "after_risk_count": int((next_round.get("chunk_audit_result") or {}).get("risk_count") or 0),
                }
            )
            write_audit_trace(
                cfg,
                "audit_review_retry_done",
                {
                    "audit_id": audit_id,
                    "chunk_id": chunk_id,
                    "retry_index": current_retry_index + 1,
                    "retry_reasons": list(retry_target.get("reasons") or []),
                    "before_parse_failed": bool((current_round.get("chunk_audit_result") or {}).get("parse_failed_flag")),
                    "after_parse_failed": bool((next_round.get("chunk_audit_result") or {}).get("parse_failed_flag")),
                    "before_truncated": bool((current_round.get("chunk_audit_result") or {}).get("truncated_flag")),
                    "after_truncated": bool((next_round.get("chunk_audit_result") or {}).get("truncated_flag")),
                    "before_risk_count": int((current_round.get("chunk_audit_result") or {}).get("risk_count") or 0),
                    "after_risk_count": int((next_round.get("chunk_audit_result") or {}).get("risk_count") or 0),
                },
            )
            rounds[round_index_map[chunk_id]] = next_round
        merged = _merge_multipass_audits(
            rounds,
            cfg=cfg,
            preview_clauses=preview_clauses,
            lang=lang,
        )
    merged_meta = merged.get("meta") if isinstance(
        merged.get("meta"), dict) else {}
    reliability_summary = build_reliability_summary(
        review_plan=review_plan,
        retry_logs=retry_logs,
        final_audit=merged.get("audit") if isinstance(
            merged.get("audit"), dict) else {},
    )
    merged_meta["token_budget_triggered"] = True
    merged_meta["token_budget_reasons"] = list(
        (token_budget or {}).get("reasons") or [])
    merged_meta["token_budget_round_plan"] = clause_plan.get("rounds") or []
    merged_meta["multipass_overflow_clause_count"] = int(
        clause_plan.get("overflow_clause_count") or 0)
    merged_meta["review_item_count"] = int(
        reliability_summary.get("review_item_count") or 0)
    merged_meta["review_retry_count"] = int(
        reliability_summary.get("retry_count") or 0)
    merged_meta["unresolved_review_item_count"] = int(
        reliability_summary.get("unresolved_review_item_count") or 0)
    merged_meta["reliability_level"] = str(
        reliability_summary.get("reliability_level") or "medium")
    merged["meta"] = merged_meta
    merged_raw = merged.get("raw") if isinstance(
        merged.get("raw"), dict) else {}
    merged_raw["review_plan"] = review_plan
    merged_raw["review_retry_logs"] = retry_logs
    merged["raw"] = merged_raw
    return merged


def _run_budget_aware_classic_audit(
    cfg: Dict[str, Any],
    llm,
    text: str,
    lang: str,
    preview_clauses: List[Dict[str, Any]],
    evidence_items: List[Dict[str, Any]],
    retrieval_opts: Dict[str, Any],
    audit_id: str,
    trace_id: str,
    token_budget: Dict[str, Any],
    prompt_payload: Dict[str, Any],
) -> Dict[str, Any]:
    if bool((token_budget or {}).get("requires_multi_pass")):
        return _build_multipass_classic_audit(
            cfg=cfg,
            llm=llm,
            text=text,
            lang=lang,
            preview_clauses=preview_clauses,
            evidence_items=evidence_items,
            retrieval_opts=retrieval_opts,
            audit_id=audit_id,
            trace_id=trace_id,
            token_budget=token_budget,
        )
    classic_result = _build_classic_audit(
        cfg=cfg,
        llm=llm,
        text=text,
        lang=lang,
        preview_clauses=preview_clauses,
        evidence_items=evidence_items,
        retrieval_opts=retrieval_opts,
        audit_id=audit_id,
        trace_id=trace_id,
        prompt_payload=prompt_payload,
    )
    result_meta = classic_result.get("meta") if isinstance(
        classic_result.get("meta"), dict) else {}
    result_meta["token_budget_triggered"] = False
    result_meta["token_budget_reasons"] = list(
        (token_budget or {}).get("reasons") or [])
    classic_result["meta"] = result_meta
    return classic_result


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
    regulation_pack_id = "rp_" + hashlib.sha1(
        pack_seed.encode("utf-8")).hexdigest()[:20]
    fingerprint_payload = {
        "pack_id": regulation_pack_id,
        "members": members,
        "citations": citation_refs,
        "scope": scope,
    }
    regulation_fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_payload,
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "regulation_pack_id": regulation_pack_id,
        "regulation_fingerprint": regulation_fingerprint,
        "regulation_pack_members": members,
    }


def audit_contract(
    cfg: Dict[str, Any],
    llm,
    file_path: str,
    lang: str = "zh",
    embedder=None,
    reranker=None,
    translator=None,
    retrieval_options: Optional[Dict[str, Any]] = None,
    progress_cb: Optional[Callable[[str, int, str], None]] = None,
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
    trace_id = new_trace_id()
    _report("extracting", 15, "extracting text")
    logger.info("audit_extract_start", file=file_path,
                lang=lang, audit_id=audit_id, trace_id=trace_id)
    text, meta = extract_text_with_config(cfg, file_path)
    preview_clauses = build_preview_clauses(text)

    # Auto-generate internal markdown file for full-text LLM analysis.
    # This file is stored in a hidden internal directory (.audit_internal/)
    # and is NOT exposed to end users. It serves as intermediate material
    # for the LLM's cross-clause joint analysis and subsequent data processing.
    _internal_md_path = ""
    try:
        from app.services.markdown_export import contract_text_to_markdown
        internal_dir = os.path.join(
            cfg.get("data_dir", "data"), ".audit_internal")
        os.makedirs(internal_dir, exist_ok=True)
        source_name = os.path.basename(file_path)
        md_filename = f"{audit_id}_{os.path.splitext(source_name)[0]}.md"
        _internal_md_path = os.path.join(internal_dir, md_filename)
        md_content = contract_text_to_markdown(
            text=text,
            source_filename=source_name,
            meta=meta,
            include_metadata_header=True,
        )
        with open(_internal_md_path, "w", encoding="utf-8") as _mf:
            _mf.write(md_content)
        logger.info("audit_internal_md_generated",
                    path=_internal_md_path, length=len(md_content))
    except Exception as _md_err:
        logger.warning("audit_internal_md_failed", error=str(_md_err))

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
    classic_prompt_payload = _build_classic_prompt_payload(
        cfg=cfg,
        text=text,
        lang=lang,
        preview_clauses=preview_clauses,
        evidence_items=evidence_items,
    )
    classic_audit_max_tokens = int(cfg.get("classic_audit_max_tokens") or 4096)
    token_budget = build_contract_audit_budget(
        cfg,
        full_contract_text=text,
        preview_clauses=preview_clauses,
        evidence_items=evidence_items,
        prompt_messages=list(classic_prompt_payload.get("messages") or []),
        prompt_meta=dict(classic_prompt_payload.get("prompt_meta") or {}),
        requested_output_tokens=classic_audit_max_tokens,
    )
    regulation_identity = get_regulation_pack_fingerprint(
        evidence_items=evidence_items,
        retrieval_opts=opts,
        lang=lang,
    )
    write_audit_trace(
        cfg,
        "audit_token_budget",
        {
            "audit_id": audit_id,
            "file_path": file_path,
            "requires_multi_pass": bool(token_budget.get("requires_multi_pass")),
            "reasons": list(token_budget.get("reasons") or []),
            "truncation_reasons": list(token_budget.get("truncation_reasons") or []),
            "policy": dict(token_budget.get("policy") or {}),
            "estimates": dict(token_budget.get("estimates") or {}),
            "prompt_meta": dict(token_budget.get("prompt_meta") or {}),
        },
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
    memory_temporary_disable_cfg = _get_memory_temporary_disable_config(cfg)
    runtime_memory_enabled = bool(
        memory_runtime_cfg.get("memory_module_enabled", True))
    memory_temporarily_disabled = bool(
        memory_temporary_disable_cfg.get("enabled", False))
    effective_disable_mode = str(
        memory_temporary_disable_cfg.get("fallback_mode")
        or memory_runtime_cfg.get("memory_mode_when_disabled")
        or "classic"
    ).strip().lower()
    memory_enabled = runtime_memory_enabled and not memory_temporarily_disabled
    fallback_on_error = bool(
        memory_runtime_cfg.get("memory_disable_fallback_on_error", True))
    execution_path = "memory"
    memory_result: Dict[str, Any] = {}
    _report("auditing", 70, "auditing clauses")
    if memory_enabled:
        logger.info("audit_memory_enabled", file=file_path,
                    clauses=len(preview_clauses))
        custom_embedder = None
        if callable(_get_memory_embedder):
            try:
                custom_embedder = _get_memory_embedder(lang, cfg)
            except TypeError:
                custom_embedder = _get_memory_embedder()
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
                    "trace_id": trace_id,
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
            memory_result = _run_budget_aware_classic_audit(
                cfg=cfg,
                llm=llm,
                text=text,
                lang=lang,
                preview_clauses=preview_clauses,
                evidence_items=evidence_items,
                retrieval_opts=opts,
                audit_id=audit_id,
                trace_id=trace_id,
                token_budget=token_budget,
                prompt_payload=classic_prompt_payload,
            )
            execution_path = str(
                (memory_result.get("meta") or {}).get("execution_path") or "classic_fallback")
    else:
        if memory_temporarily_disabled:
            logger.warning(
                "memory_temporarily_disabled audit_id=%s file=%s service_node=%s trigger_source=%s disable_reason=%s fallback_mode=%s runtime_memory_enabled=%s clauses=%s",
                audit_id,
                file_path,
                socket.gethostname(),
                str(memory_temporary_disable_cfg.get(
                    "trigger_source") or "config.memory_temporary_disable"),
                str(memory_temporary_disable_cfg.get(
                    "reason") or "edge_llm_context_limit"),
                effective_disable_mode,
                runtime_memory_enabled,
                len(preview_clauses),
            )
        else:
            logger.info("audit_memory_disabled_use_classic",
                        audit_id=audit_id, file=file_path)
        memory_result = _run_budget_aware_classic_audit(
            cfg=cfg,
            llm=llm,
            text=text,
            lang=lang,
            preview_clauses=preview_clauses,
            evidence_items=evidence_items,
            retrieval_opts=opts,
            audit_id=audit_id,
            trace_id=trace_id,
            token_budget=token_budget,
            prompt_payload=classic_prompt_payload,
        )
        execution_path = str(
            (memory_result.get("meta") or {}).get("execution_path")
            or ("classic" if effective_disable_mode == "classic" else effective_disable_mode)
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
        "audit_token_budget": token_budget,
        "audit_token_budget_requires_multi_pass": bool(token_budget.get("requires_multi_pass")),
        "audit_token_budget_reasons": list(token_budget.get("reasons") or []),
        "audit_duration_ms": audit_duration_ms,
        "regulation_pack_id": regulation_identity.get("regulation_pack_id", ""),
        "regulation_fingerprint": regulation_identity.get("regulation_fingerprint", ""),
        "regulation_pack_members": regulation_identity.get("regulation_pack_members", []),
        "memory_module_enabled": memory_enabled,
        "memory_runtime_module_enabled": runtime_memory_enabled,
        "memory_temporarily_disabled": memory_temporarily_disabled,
        "memory_temporary_disable_reason": memory_temporary_disable_cfg.get("reason"),
        "memory_temporary_disable_trigger_source": memory_temporary_disable_cfg.get("trigger_source"),
        "memory_temporary_disable_fallback_mode": memory_temporary_disable_cfg.get("fallback_mode"),
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
            "memory_temporarily_disabled": memory_temporarily_disabled,
            "memory_temporary_disable_reason": output_meta.get("memory_temporary_disable_reason", ""),
            "memory_temporary_disable_trigger_source": output_meta.get("memory_temporary_disable_trigger_source", ""),
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
    final_audit = memory_result.get("audit") if isinstance(
        memory_result.get("audit"), dict) else {}
    final_report = build_contract_final_report(
        audit=final_audit,
        meta=output_meta,
        file_path=file_path,
        contract_id=audit_id,
    )
    final_audit["final_report"] = final_report
    final_audit["pipeline_summary"] = final_report.get("pipeline_summary") if isinstance(
        final_report.get("pipeline_summary"), dict) else {}
    final_audit["reliability_summary"] = final_report.get("reliability_summary") if isinstance(
        final_report.get("reliability_summary"), dict) else {}
    return {
        "audit": final_audit,
        "meta": output_meta,
        "raw": memory_result.get("raw") if isinstance(memory_result.get("raw"), dict) else {"mode": "memory"}
    }
