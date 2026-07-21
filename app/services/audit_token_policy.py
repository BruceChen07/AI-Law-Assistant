from __future__ import annotations

from typing import Any, Dict, List

from app.core.token_utils import estimate_messages_tokens, estimate_text_tokens


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _safe_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def get_audit_token_policy(cfg: Dict[str, Any]) -> Dict[str, Any]:
    raw = cfg.get("audit_token_policy") if isinstance(
        cfg.get("audit_token_policy"), dict) else {}
    default_output_tokens = _safe_int(
        cfg.get("classic_audit_max_tokens")
        or (cfg.get("llm_config") or {}).get("max_tokens")
        or 4096,
        4096,
    )
    reserve_output_tokens = max(
        1024, _safe_int(raw.get("reserve_output_tokens"), default_output_tokens))
    context_window_tokens = max(
        reserve_output_tokens + 2048,
        _safe_int(raw.get("context_window_tokens"), 16384),
    )
    safety_margin_tokens = max(
        256, _safe_int(raw.get("safety_margin_tokens"), 1024))
    input_hard_limit = _safe_int(
        raw.get("input_hard_limit_tokens"),
        context_window_tokens - reserve_output_tokens - safety_margin_tokens,
    )
    input_soft_limit = _safe_int(
        raw.get("input_soft_limit_tokens"),
        min(input_hard_limit, max(2048, input_hard_limit - 1536)),
    )
    clause_group_target_tokens = max(
        1200, _safe_int(raw.get("clause_group_target_tokens"), 4200))
    return {
        "enabled": _safe_bool(raw.get("enabled"), True),
        "context_window_tokens": context_window_tokens,
        "default_output_tokens": default_output_tokens,
        "reserve_output_tokens": reserve_output_tokens,
        "safety_margin_tokens": safety_margin_tokens,
        "input_soft_limit_tokens": max(1024, min(input_soft_limit, input_hard_limit)),
        "input_hard_limit_tokens": max(1024, input_hard_limit),
        "force_multi_pass_when_exceed": _safe_bool(
            raw.get("force_multi_pass_when_exceed"), True),
        "force_multi_pass_when_prompt_truncated": _safe_bool(
            raw.get("force_multi_pass_when_prompt_truncated"), True),
        "clause_group_target_tokens": clause_group_target_tokens,
        "max_clauses_per_group": max(
            1, _safe_int(raw.get("max_clauses_per_group"), 8)),
        "chunk_output_tokens": max(
            512, _safe_int(raw.get("chunk_output_tokens"), 1536)),
        "max_evidence_items_per_round": max(
            4, _safe_int(raw.get("max_evidence_items_per_round"), 12)),
        "max_rounds": max(1, _safe_int(raw.get("max_rounds"), 12)),
    }


def build_contract_audit_budget(
    cfg: Dict[str, Any],
    *,
    full_contract_text: str,
    preview_clauses: List[Dict[str, Any]],
    evidence_items: List[Dict[str, Any]],
    prompt_messages: List[Dict[str, Any]],
    prompt_meta: Dict[str, Any],
    requested_output_tokens: int,
) -> Dict[str, Any]:
    policy = get_audit_token_policy(cfg)
    prompt_input_tokens = estimate_messages_tokens(prompt_messages)
    full_contract_tokens = estimate_text_tokens(full_contract_text)
    preview_clause_tokens = sum(
        estimate_text_tokens(
            f"{clause.get('title') or clause.get('clause_path') or ''}\n"
            f"{clause.get('clause_text') or clause.get('text') or ''}"
        )
        for clause in list(preview_clauses or [])
        if isinstance(clause, dict)
    )
    evidence_tokens = sum(
        estimate_text_tokens(
            f"{item.get('law_title') or item.get('title') or ''}\n"
            f"{item.get('article_no') or ''}\n"
            f"{item.get('content') or item.get('excerpt') or ''}"
        )
        for item in list(evidence_items or [])
        if isinstance(item, dict)
    )
    requested_output_tokens = max(128, int(requested_output_tokens or 0))
    projected_total_tokens = prompt_input_tokens + requested_output_tokens
    available_input_tokens = max(
        0,
        int(policy["context_window_tokens"])
        - requested_output_tokens
        - int(policy["safety_margin_tokens"]),
    )
    truncation_reasons: List[str] = []
    if prompt_meta.get("full_text_context_truncated"):
        truncation_reasons.append("full_text_context_truncated")
    if int(prompt_meta.get("clauses_omitted") or 0) > 0:
        truncation_reasons.append("clauses_omitted")
    if int(prompt_meta.get("clause_chars_truncated_count") or 0) > 0:
        truncation_reasons.append("clause_chars_truncated")
    if int(prompt_meta.get("evidence_items_omitted") or 0) > 0:
        truncation_reasons.append("evidence_items_omitted")
    if int(prompt_meta.get("evidence_content_truncated_count") or 0) > 0:
        truncation_reasons.append("evidence_content_truncated")
    reasons: List[str] = []
    if prompt_input_tokens > int(policy["input_soft_limit_tokens"]):
        reasons.append("prompt_input_over_soft_limit")
    if prompt_input_tokens > int(policy["input_hard_limit_tokens"]):
        reasons.append("prompt_input_over_hard_limit")
    if prompt_input_tokens > available_input_tokens:
        reasons.append("prompt_input_over_available_budget")
    if projected_total_tokens > int(policy["context_window_tokens"]):
        reasons.append("projected_total_over_context_window")
    if truncation_reasons and bool(policy["force_multi_pass_when_prompt_truncated"]):
        reasons.extend(truncation_reasons)
    requires_multi_pass = bool(policy["enabled"]) and (
        (
            bool(policy["force_multi_pass_when_exceed"])
            and any(
                item in reasons
                for item in {
                    "prompt_input_over_soft_limit",
                    "prompt_input_over_hard_limit",
                    "prompt_input_over_available_budget",
                    "projected_total_over_context_window",
                }
            )
        )
        or bool(truncation_reasons and policy["force_multi_pass_when_prompt_truncated"])
    )
    return {
        "policy": policy,
        "estimates": {
            "full_contract_tokens": full_contract_tokens,
            "preview_clause_tokens": preview_clause_tokens,
            "evidence_tokens": evidence_tokens,
            "prompt_input_tokens": prompt_input_tokens,
            "requested_output_tokens": requested_output_tokens,
            "projected_total_tokens": projected_total_tokens,
            "available_input_tokens": available_input_tokens,
        },
        "prompt_meta": dict(prompt_meta or {}),
        "truncation_reasons": truncation_reasons,
        "reasons": reasons,
        "requires_multi_pass": requires_multi_pass,
    }


def plan_clause_groups(
    preview_clauses: List[Dict[str, Any]],
    policy: Dict[str, Any],
) -> Dict[str, Any]:
    clauses = [item for item in list(preview_clauses or []) if isinstance(item, dict)]
    groups: List[List[Dict[str, Any]]] = []
    current_group: List[Dict[str, Any]] = []
    current_tokens = 0
    max_rounds = max(1, int(policy.get("max_rounds") or 1))
    max_clauses_per_group = max(1, int(policy.get("max_clauses_per_group") or 1))
    target_tokens = max(1200, int(policy.get("clause_group_target_tokens") or 1200))
    overflow_clause_count = 0
    for clause in clauses:
        clause_tokens = estimate_text_tokens(
            f"{clause.get('title') or clause.get('clause_path') or ''}\n"
            f"{clause.get('clause_text') or clause.get('text') or ''}"
        )
        should_wrap = bool(current_group) and (
            len(current_group) >= max_clauses_per_group
            or current_tokens + clause_tokens > target_tokens
        )
        if should_wrap:
            groups.append(current_group)
            current_group = []
            current_tokens = 0
        if len(groups) >= max_rounds:
            overflow_clause_count += 1
            if groups:
                groups[-1].append(clause)
            else:
                groups.append([clause])
            continue
        current_group.append(clause)
        current_tokens += clause_tokens
    if current_group:
        groups.append(current_group)
    round_plans = []
    for index, group in enumerate(groups, start=1):
        clause_ids = [str(item.get("clause_id") or "") for item in group]
        clause_paths = [str(item.get("clause_path") or item.get("title") or "") for item in group]
        combined_tokens = sum(
            estimate_text_tokens(
                f"{item.get('title') or item.get('clause_path') or ''}\n"
                f"{item.get('clause_text') or item.get('text') or ''}"
            )
            for item in group
        )
        round_plans.append(
            {
                "round_index": index,
                "clause_count": len(group),
                "clause_ids": clause_ids,
                "clause_paths": clause_paths,
                "estimated_clause_tokens": combined_tokens,
            }
        )
    return {
        "round_count": len(round_plans),
        "overflow_clause_count": overflow_clause_count,
        "rounds": round_plans,
        "groups": groups,
    }
