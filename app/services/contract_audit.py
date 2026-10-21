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

from app.core.llm_env import is_cloud_provider, resolve_runtime_llm_config
from app.core.llm_router import resolve_llm_route
from app.core.utils import extract_text_with_config
from app.services.audit_utils import _safe_int, _normalize_citation_item, _enrich_citations
from app.services.audit_retrieval import _normalize_retrieval_options, _retrieve_regulation_evidence
from app.services.contract_audit_modules.clause_builder import build_preview_clauses
from app.services.contract_audit_modules.result_assembler import attach_risk_locations
from app.services.contract_audit_modules.trace_writer import memory_paths, write_audit_trace, trace_clip
from app.services.contract_preview_assets import build_contract_preview_manifest
from app.services.prompt_binding import resolve_bound_prompt_text

logger = structlog.get_logger(__name__)

CLASSIC_AUDIT_MAX_TOKENS = 900
CLASSIC_AUDIT_RETRY_MAX_TOKENS = 1400
CLASSIC_MULTIPASS_TRIGGER_CLAUSES = 10
CONTRACT_SUMMARY_SOURCE_MAX_CHARS = 30000
# Reasoning models spend part of max_tokens on hidden reasoning tokens, so the
# budget must cover reasoning plus the ~900 tokens of Chinese output.
CONTRACT_SUMMARY_MAX_TOKENS = 3000
CONTRACT_SUMMARY_RETRY_MAX_TOKENS = 4000
CONTRACT_SUMMARY_MAP_CHUNK_CHARS = 6000
CONTRACT_SUMMARY_MAP_MAX_CHUNKS = 24
CONTRACT_SUMMARY_MAP_MAX_TOKENS = 1200
CONTRACT_SUMMARY_MIN_SECTION_CHARS = 120
CONTRACT_SUMMARY_MIN_TOTAL_CHARS = 350
CONTRACT_SUMMARY_LONG_CONTRACT_CHARS = 3000
CONTRACT_SUMMARY_PROMPT_VERSION = "v2"
CONTRACT_SUMMARY_TARGET_MIN_CHARS = 600
CONTRACT_SUMMARY_TARGET_MAX_CHARS = 800
DEFAULT_CLOUD_CONTEXT_WINDOW_TOKENS = 128 * 1024
DEFAULT_LOCAL_CONTEXT_WINDOW_TOKENS = 4096
DEFAULT_CONTEXT_WINDOW_OUTPUT_RESERVE_TOKENS = 4096
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


def _get_contract_summary_runtime_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Read contract summary budgets from config with safe defaults."""
    raw = cfg.get("memory_runtime_config") if isinstance(
        cfg.get("memory_runtime_config"), dict) else {}
    source_max_chars = max(
        4000,
        _safe_int(raw.get("contract_summary_source_max_chars"),
                  CONTRACT_SUMMARY_SOURCE_MAX_CHARS),
    )
    max_tokens = max(
        600,
        _safe_int(raw.get("contract_summary_max_tokens"),
                  CONTRACT_SUMMARY_MAX_TOKENS),
    )
    retry_max_tokens = max(
        max_tokens,
        _safe_int(raw.get("contract_summary_retry_max_tokens"),
                  CONTRACT_SUMMARY_RETRY_MAX_TOKENS),
    )
    return {
        "source_max_chars": source_max_chars,
        "max_tokens": max_tokens,
        "retry_max_tokens": retry_max_tokens,
        "map_reduce_enabled": bool(raw.get("contract_summary_map_reduce_enabled", True)),
        "map_chunk_chars": max(
            1500,
            _safe_int(raw.get("contract_summary_map_chunk_chars"),
                      CONTRACT_SUMMARY_MAP_CHUNK_CHARS),
        ),
        "map_max_chunks": max(
            1,
            _safe_int(raw.get("contract_summary_map_max_chunks"),
                      CONTRACT_SUMMARY_MAP_MAX_CHUNKS),
        ),
    }


def _group_summary_clause_blocks(
    preview_clauses: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Group per-line preview clauses into clause-level blocks.

    ``split_contract_clauses`` emits one item per physical line and repeats the
    heading path for every following line, so consecutive items sharing the same
    ``clause_path`` are merged back into a single clause block. Keeping clauses
    intact prevents the heading hierarchy from being flattened before the model
    sees the contract.
    """
    groups: List[Dict[str, Any]] = []
    for clause in list(preview_clauses or []):
        if not isinstance(clause, dict):
            continue
        path = str(clause.get("clause_path") or "").strip()
        body = str(clause.get("clause_text")
                   or clause.get("text") or "").strip()
        if not body:
            continue
        if groups and path and groups[-1]["path"] == path:
            groups[-1]["lines"].append(body)
            continue
        groups.append({"path": path, "lines": [body]})
    return groups


def _render_summary_clause_block(group: Dict[str, Any]) -> str:
    path = str(group.get("path") or "").strip()
    lines = [str(x).strip()
             for x in list(group.get("lines") or []) if str(x).strip()]
    if not lines:
        return ""
    if not path:
        return "\n".join(lines)
    header = f"{path} {lines[0]}".strip()
    rest = lines[1:]
    return "\n".join([f"【{header}】", *rest]).strip()


def _build_contract_summary_source_text(
    text: str,
    preview_clauses: List[Dict[str, Any]],
    max_chars: int = CONTRACT_SUMMARY_SOURCE_MAX_CHARS,
) -> Dict[str, Any]:
    """Assemble summary source text on clause boundaries.

    Returns a dict with the assembled text plus truncation metadata. Blocks are
    never cut mid-clause: once the budget is exhausted the remaining clauses are
    dropped and flagged as truncated.
    """
    limit = max(2000, int(max_chars or CONTRACT_SUMMARY_SOURCE_MAX_CHARS))
    blocks = [
        block
        for block in (
            _render_summary_clause_block(group)
            for group in _group_summary_clause_blocks(preview_clauses)
        )
        if block
    ]
    selected: List[str] = []
    used = 0
    truncated = False
    for block in blocks:
        sep = 2 if selected else 0
        if used + sep + len(block) > limit:
            if not selected:
                selected.append(block[:limit].rstrip() + "\n……")
                truncated = True
            else:
                truncated = True
            break
        selected.append(block)
        used += sep + len(block)

    source_text = "\n\n".join(selected).strip()
    if not source_text:
        raw_text = str(text or "").strip()
        clipped = raw_text[:limit].strip()
        return {
            "text": clipped,
            "truncated": len(raw_text) > len(clipped),
            "clause_block_total": len(blocks),
            "clause_block_included": 1 if clipped else 0,
            "source_chars": len(clipped),
        }
    covered = min(len(selected), len(blocks))
    return {
        "text": source_text,
        "truncated": truncated or covered < len(blocks),
        "clause_block_total": len(blocks),
        "clause_block_included": covered,
        "source_chars": len(source_text),
    }


def _split_summary_source_chunks(
    source_text: str,
    preview_clauses: List[Dict[str, Any]],
    chunk_chars: int,
    max_chunks: int,
) -> List[str]:
    """Split the source text into clause-aligned chunks for map-reduce."""
    limit = max(1200, int(chunk_chars or CONTRACT_SUMMARY_MAP_CHUNK_CHARS))
    blocks = [
        block
        for block in (
            _render_summary_clause_block(group)
            for group in _group_summary_clause_blocks(preview_clauses)
        )
        if block
    ]
    if not blocks:
        raw_text = str(source_text or "").strip()
        if not raw_text:
            return []
        return [raw_text[idx: idx + limit] for idx in range(0, len(raw_text), limit)]

    def _greedy(size: int) -> List[str]:
        chunks: List[str] = []
        current: List[str] = []
        used = 0
        for block in blocks:
            sep = 2 if current else 0
            if current and used + sep + len(block) > size:
                chunks.append("\n\n".join(current))
                current = []
                used = 0
            current.append(block)
            used += (2 if len(current) > 1 else 0) + len(block)
        if current:
            chunks.append("\n\n".join(current))
        return chunks

    chunks = _greedy(limit)
    bounded = max(1, int(max_chunks or 1))
    if len(chunks) <= bounded:
        return chunks
    total_chars = sum(len(chunk) + 2 for chunk in chunks)
    balanced_size = -(-total_chars // bounded)
    rebalanced = _greedy(max(limit, balanced_size))
    return rebalanced[:bounded] if len(rebalanced) > bounded else rebalanced


def _merge_summary_fact_payloads(payloads: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge per-chunk fact payloads, de-duplicating list items."""
    merged: Dict[str, Any] = {}
    for payload in list(payloads or []):
        if not isinstance(payload, dict):
            continue
        for key, value in payload.items():
            name = str(key or "").strip()
            if not name:
                continue
            if isinstance(value, list):
                bucket = merged.setdefault(name, [])
                if not isinstance(bucket, list):
                    continue
                for item in value:
                    text = str(item or "").strip()
                    if text and text not in bucket:
                        bucket.append(text)
                continue
            text = str(value or "").strip()
            if not text:
                continue
            existing = merged.get(name)
            if not existing:
                merged[name] = text
                continue
            if isinstance(existing, str) and text not in existing:
                merged[name] = f"{existing}；{text}"
    return merged


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


def _estimate_text_tokens(text: str) -> int:
    source = str(text or "")
    if not source:
        return 0
    cjk = len(re.findall(r"[\u4e00-\u9fff]", source))
    non_cjk = max(0, len(source) - cjk)
    return max(1, int(cjk * 1.1 + non_cjk / 3.8))


def _resolve_contract_audit_runtime_llm(cfg: Dict[str, Any]) -> Dict[str, Any]:
    route_cfg, route_meta = resolve_llm_route(
        cfg or {},
        task_profile="contract_audit_main",
    )
    route_cfg = dict(route_cfg or {})
    # Mirror the chat client: pin the routed local target so that ambient
    # LLM_PROVIDER/LLM_MODEL variables cannot make budget sizing assume a
    # cloud model and blow the local num_ctx window.
    if (
        str(route_meta.get("selected_role") or "") in {"main", "small"}
        and str(route_meta.get("selected_source") or "") in {"main_model", "small_model"}
    ):
        route_cfg["_lock_runtime_target"] = True
    runtime_cfg, _runtime_meta = resolve_runtime_llm_config(route_cfg)
    return runtime_cfg if isinstance(runtime_cfg, dict) else {}


def _resolve_contract_audit_context_window_tokens(
    cfg: Dict[str, Any],
    runtime_cfg: Dict[str, Any],
) -> int:
    runtime = runtime_cfg if isinstance(runtime_cfg, dict) else {}
    provider = str(runtime.get("provider") or "").strip().lower()
    local_num_ctx = _safe_int(runtime.get("num_ctx"), 0)
    raw = cfg.get("memory_runtime_config") if isinstance(
        cfg.get("memory_runtime_config"), dict) else {}
    cloud_window = max(
        4096,
        _safe_int(
            raw.get("cloud_context_window_tokens"),
            DEFAULT_CLOUD_CONTEXT_WINDOW_TOKENS,
        ),
    )
    local_window = max(
        1024,
        _safe_int(
            local_num_ctx or raw.get("local_context_window_tokens"),
            DEFAULT_LOCAL_CONTEXT_WINDOW_TOKENS,
        ),
    )
    return cloud_window if is_cloud_provider(provider) else local_window


def _build_classic_clause_text(
    text: str,
    preview_clauses: List[Dict[str, Any]],
    max_clause_chars: int,
    context_window_tokens: int,
    output_reserve_tokens: int,
) -> str:
    reserve_tokens = max(256, int(output_reserve_tokens or 0))
    input_budget_tokens = max(
        600, int(context_window_tokens or 0) - reserve_tokens)
    clause_blocks: List[str] = []
    used_tokens = 0
    for c in list(preview_clauses or []):
        cid = str(c.get("clause_id") or "")
        title = str(c.get("title") or c.get("clause_path") or "")
        body = str(c.get("clause_text") or c.get(
            "text") or "")[:max_clause_chars]
        block = f"[{cid}] {title}\n{body}".strip()
        if not block:
            continue
        block_tokens = _estimate_text_tokens(block) + 8
        if clause_blocks and used_tokens + block_tokens > input_budget_tokens:
            break
        if not clause_blocks and block_tokens > input_budget_tokens:
            head_chars = max(300, min(len(body), int(max_clause_chars * 0.35)))
            block = f"[{cid}] {title}\n{body[:head_chars]}".strip()
            block_tokens = _estimate_text_tokens(block) + 8
        clause_blocks.append(block)
        used_tokens += block_tokens
    if clause_blocks:
        return "\n\n".join(clause_blocks)
    fallback_limit = max(300, min(int(max_clause_chars), len(str(text or ""))))
    return str(text or "")[:fallback_limit]


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


def _parse_contract_summary_object(raw_text: str) -> Dict[str, str]:
    parsed = _load_llm_json_object(raw_text)

    def _first_text(*keys: str) -> str:
        for key in keys:
            value = parsed.get(key)
            if value is None:
                continue
            if isinstance(value, (dict, list)):
                continue
            text = str(value).strip()
            if text:
                return text
        return ""

    return {
        "title": _first_text("title", "summary_title", "item_title"),
        "about": _first_text("about", "contract_about", "what_the_contract_is_about"),
        "core": _first_text("core", "core_arrangement", "core_terms"),
        "tax": _first_text("tax", "tax_note", "tax_review_note"),
    }


def _build_contract_summary_title(source_filename: str, lang: str) -> str:
    name = str(source_filename or "").strip()
    if not name:
        return "Current Contract" if str(lang or "").lower() == "en" else "当前合同"
    stem = os.path.splitext(os.path.basename(name))[0].strip()
    if not stem:
        return "Current Contract" if str(lang or "").lower() == "en" else "当前合同"
    return stem


def _summarize_finish_reason(finish_reason: str) -> str:
    return str(finish_reason or "").strip().lower()


def _resolve_summary_output_budget(
    summary_cfg: Dict[str, Any],
    runtime_cfg: Dict[str, Any],
    context_window_tokens: int,
) -> Dict[str, Any]:
    """Resolve differentiated input/output budgets for cloud vs local models."""
    provider = str((runtime_cfg or {}).get("provider") or "").strip().lower()
    cloud = is_cloud_provider(provider)
    window = max(2048, int(context_window_tokens or 0))
    if cloud:
        max_tokens = max(900, int(summary_cfg["max_tokens"]))
        retry_max_tokens = max(max_tokens, int(
            summary_cfg["retry_max_tokens"]))
        single_pass_chars = max(4000, int(summary_cfg["source_max_chars"]))
    else:
        # Local models have a small context window: keep the output budget at
        # most a third of the window and shrink the single-pass input so that
        # prompt + max_tokens never exceed num_ctx.
        local_out_cap = max(800, window // 3)
        max_tokens = min(
            max(900, int(summary_cfg["max_tokens"])), local_out_cap)
        retry_max_tokens = min(
            max(max_tokens, int(summary_cfg["retry_max_tokens"])),
            local_out_cap + 400,
        )
        available_tokens = max(700, window - max_tokens - 320)
        single_pass_chars = max(
            1200,
            min(int(summary_cfg["source_max_chars"]),
                int(available_tokens / 1.15)),
        )
    return {
        "provider": provider,
        "is_cloud": cloud,
        "context_window_tokens": window,
        "max_tokens": max_tokens,
        "retry_max_tokens": retry_max_tokens,
        "single_pass_chars": single_pass_chars,
    }


def _resolve_contract_summary_cache_path(
    cfg: Dict[str, Any],
    document_id: str,
    source_sha256: str,
    lang: str,
) -> str:
    """Build the idempotent cache path for a contract summary."""
    doc_id = str(document_id or "").strip()
    digest = str(source_sha256 or "").strip().lower()
    if not doc_id or not digest:
        return ""
    try:
        memory_dir, _db_path = memory_paths(cfg)
    except Exception:
        return ""
    key_material = f"{doc_id}|{digest}|{lang}|{CONTRACT_SUMMARY_PROMPT_VERSION}"
    key = hashlib.sha256(key_material.encode("utf-8")).hexdigest()[:32]
    return os.path.join(memory_dir, "contract_summaries", f"{key}.json")


def _read_contract_summary_cache(path: str) -> Optional[Dict[str, Any]]:
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if str(payload.get("status") or "").strip().lower() != "ok":
        return None
    if not any(str(payload.get(key) or "").strip() for key in ("about", "core", "tax")):
        return None
    return payload


def _write_contract_summary_cache(path: str, payload: Dict[str, Any]) -> None:
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except Exception as exc:
        logger.warning(
            "contract_summary_cache_write_failed error=%s", str(exc))


def _validate_contract_summary_payload(
    parsed: Dict[str, str],
    finish_reason: str,
    source_chars: int,
) -> List[str]:
    """Detect truncated or under-specified summaries instead of reporting ok."""
    issues: List[str] = []
    sections = {key: str(parsed.get(key) or "").strip()
                for key in ("about", "core", "tax")}
    missing = [key for key, value in sections.items() if not value]
    if missing:
        issues.append("missing_sections:" + ",".join(missing))
    if _summarize_finish_reason(finish_reason) == "length":
        issues.append("output_truncated")
    if int(source_chars or 0) >= CONTRACT_SUMMARY_LONG_CONTRACT_CHARS:
        short = [
            key for key, value in sections.items()
            if value and len(value) < CONTRACT_SUMMARY_MIN_SECTION_CHARS
        ]
        if short:
            issues.append("section_too_short:" + ",".join(short))
        total_chars = sum(len(value) for value in sections.values())
        if total_chars and total_chars < CONTRACT_SUMMARY_MIN_TOTAL_CHARS:
            issues.append("summary_too_short")
    return issues


def _request_contract_summary_payload(
    llm,
    bound_prompt: Dict[str, Any],
    max_tokens: int,
    trace_meta: Dict[str, Any],
    source_chars: int,
) -> Dict[str, Any]:
    result_text, raw = _chat_with_task_profile(
        llm,
        [
            {"role": "system", "content": str(
                bound_prompt.get("rendered_system_text") or "")},
            {"role": "user", "content": str(
                bound_prompt.get("rendered_text") or "")},
        ],
        "contract_audit_main",
        overrides={
            "max_tokens": int(max_tokens),
            "enable_thinking": False,
            "reasoning_effort": "low",
            "thinking_budget_tokens": 0,
            "_trace_meta": trace_meta,
        },
    )
    finish_reason = _extract_finish_reason(
        raw if isinstance(raw, dict) else {})
    parsed = _parse_contract_summary_object(result_text)
    issues = _validate_contract_summary_payload(
        parsed, finish_reason, source_chars)
    return {
        "parsed": parsed,
        "finish_reason": finish_reason,
        "issues": issues,
        "raw_text": str(result_text or ""),
    }


def _extract_summary_chunk_facts(
    cfg: Dict[str, Any],
    llm,
    chunk_text: str,
    chunk_index: int,
    chunk_total: int,
    lang: str,
    source_name: str,
    max_tokens: int,
    audit_id: str,
) -> Dict[str, Any]:
    """Map step: extract structured contract facts from one clause chunk."""
    context = {
        "lang": lang,
        "contract_chunk_index": int(chunk_index),
        "contract_chunk_total": int(chunk_total),
        "contract_chunk_text": chunk_text,
        "source_filename": source_name,
    }
    try:
        bound_prompt = resolve_bound_prompt_text(
            "contract_audit.contract_summary_map",
            "",
            context,
            cfg,
        )
    except Exception as exc:
        logger.warning(
            "contract_summary_map_prompt_unavailable audit_id=%s error=%s",
            audit_id,
            str(exc),
        )
        return {}
    trace_meta = {
        "module": "contract_audit",
        "stage": "contract_summary_map",
        "audit_id": audit_id,
        "lang": lang,
        "chunk_index": int(chunk_index),
        "chunk_total": int(chunk_total),
        "prompt_source": str(bound_prompt.get("source") or "system_default"),
        "prompt_binding_scene": "contract_audit.contract_summary_map",
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
                "max_tokens": int(max_tokens),
                "enable_thinking": False,
                "reasoning_effort": "low",
                "thinking_budget_tokens": 0,
                "_trace_meta": trace_meta,
            },
        )
    except Exception as exc:
        logger.warning(
            "contract_summary_map_failed audit_id=%s chunk=%s error=%s",
            audit_id,
            chunk_index,
            str(exc),
        )
        return {}
    return _load_llm_json_object(result_text)


def _build_contract_summary(
    cfg: Dict[str, Any],
    llm,
    text: str,
    lang: str,
    preview_clauses: List[Dict[str, Any]],
    page_count: int,
    audit_id: str,
    source_filename: str,
    document_id: str = "",
    source_sha256: str = "",
) -> Dict[str, Any]:
    norm_lang = "en" if str(lang or "").lower() == "en" else "zh"
    source_name = os.path.basename(str(source_filename or "").strip())
    summary_title = _build_contract_summary_title(source_name, norm_lang)
    enabled = bool(cfg.get("contract_summary_enabled", False))
    base = {
        "enabled": enabled,
        "title": "",
        "about": "",
        "core": "",
        "tax": "",
        "source_file": source_name or summary_title,
        "status": "disabled",
        "source": "disabled",
        "prompt_id": "",
        "prompt_name": "",
        "error": "",
        "issues": [],
        "truncated": False,
        "cache_hit": False,
        "source_chars": 0,
        "map_reduce": False,
        "map_chunks": 0,
    }
    if not enabled:
        return base

    summary_cfg = _get_contract_summary_runtime_config(cfg)
    runtime_cfg = _resolve_contract_audit_runtime_llm(cfg)
    context_window_tokens = _resolve_contract_audit_context_window_tokens(
        cfg, runtime_cfg)
    budget = _resolve_summary_output_budget(
        summary_cfg, runtime_cfg, context_window_tokens)
    base["context_window_tokens"] = int(budget["context_window_tokens"])

    cache_path = _resolve_contract_summary_cache_path(
        cfg, document_id, source_sha256, norm_lang)
    if cache_path:
        cached = _read_contract_summary_cache(cache_path)
        if cached:
            cached_payload = dict(cached)
            cached_payload.update({
                "enabled": True,
                "cache_hit": True,
                "source": "cache",
            })
            logger.info(
                "contract_summary_cache_hit",
                audit_id=audit_id,
                document_id=document_id,
            )
            return cached_payload

    full_source = _build_contract_summary_source_text(
        text, preview_clauses, max_chars=summary_cfg["source_max_chars"])
    full_source_text = str(full_source.get("text") or "")
    if not full_source_text:
        base.update({"source": "empty_input", "status": "empty_input"})
        return base

    use_map_reduce = bool(
        summary_cfg["map_reduce_enabled"]
        and len(full_source_text) > int(budget["single_pass_chars"])
    )
    facts_payload: Dict[str, Any] = {}
    facts_text = ""
    contract_text_for_prompt = full_source_text
    truncated = bool(full_source.get("truncated"))
    map_chunks: List[str] = []

    if use_map_reduce:
        # Keep every map chunk inside the model input budget: cloud models can
        # take the configured chunk size, local models must stay well below the
        # single-pass budget so the instruction block still fits.
        if budget["is_cloud"]:
            map_chunk_chars = int(summary_cfg["map_chunk_chars"])
        else:
            map_chunk_chars = max(
                700, int(int(budget["single_pass_chars"]) * 0.55))
        map_chunks = _split_summary_source_chunks(
            full_source_text,
            preview_clauses,
            chunk_chars=map_chunk_chars,
            max_chunks=summary_cfg["map_max_chunks"],
        )
        chunk_payloads: List[Dict[str, Any]] = []
        for index, chunk in enumerate(map_chunks, start=1):
            payload = _extract_summary_chunk_facts(
                cfg,
                llm,
                chunk,
                index,
                len(map_chunks),
                norm_lang,
                source_name,
                min(int(summary_cfg["max_tokens"]),
                    CONTRACT_SUMMARY_MAP_MAX_TOKENS),
                audit_id,
            )
            if payload:
                chunk_payloads.append(payload)
        facts_payload = _merge_summary_fact_payloads(chunk_payloads)
        if facts_payload:
            facts_text = json.dumps(
                facts_payload, ensure_ascii=False, indent=2)
            contract_text_for_prompt = ""
        else:
            # Map step produced nothing usable: degrade to a single-pass
            # summary over the budget-limited source instead of failing.
            limited = _build_contract_summary_source_text(
                text, preview_clauses, max_chars=int(budget["single_pass_chars"]))
            contract_text_for_prompt = str(limited.get("text") or "")
            truncated = bool(limited.get("truncated"))
            use_map_reduce = False
    else:
        truncated_note_required = len(full_source_text) > int(
            budget["single_pass_chars"])
        if truncated_note_required:
            limited = _build_contract_summary_source_text(
                text, preview_clauses, max_chars=int(budget["single_pass_chars"]))
            contract_text_for_prompt = str(limited.get("text") or "")
            truncated = bool(limited.get("truncated"))

    if not contract_text_for_prompt and not facts_text:
        base.update({"source": "empty_input", "status": "empty_input"})
        return base

    truncated_note = ""
    if truncated:
        truncated_note = (
            "The contract text was truncated because of the context budget; "
            "do not assert mechanisms that may only appear later in the contract."
            if norm_lang == "en"
            else "合同原文因上下文预算被截断；不要推断可能只出现在后文的机制。"
        )

    prompt_context = {
        "lang": norm_lang,
        "contract_page_count": max(0, int(page_count or 0)),
        "contract_clause_count": len(list(preview_clauses or [])),
        "contract_document_count": 1,
        "contract_summary_title": summary_title,
        "source_filename": source_name,
        "contract_text": contract_text_for_prompt,
        "contract_facts": facts_text,
        "contract_truncated_note": truncated_note,
        "target_chars_min": CONTRACT_SUMMARY_TARGET_MIN_CHARS,
        "target_chars_max": CONTRACT_SUMMARY_TARGET_MAX_CHARS,
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
                "You are a senior contract analysis assistant for tax reviewers. "
                "Summarize the contract faithfully and never fabricate."
                if norm_lang == "en"
                else "你是面向税务审核师的资深合同分析助手，请忠实归纳合同内容，不得编造。"
            ),
            "rendered_text": (
                "Return strict JSON only with exactly the fields title, about, core, tax.\n"
                "about: what the contract is about (150-220 chars).\n"
                "core: core commercial arrangements (220-320 chars).\n"
                "tax: tax review notes (200-300 chars).\n"
                f"{truncated_note}\n"
                f"Contract content:\n{contract_text_for_prompt or facts_text}"
                if norm_lang == "en"
                else "请仅输出严格 JSON，且只包含 title / about / core / tax 四个字段。\n"
                "about：合同在说什么（150-220 字）。\n"
                "core：核心安排（220-320 字）。\n"
                "tax：税务审核提示（200-300 字）。\n"
                f"{truncated_note}\n"
                f"合同内容：\n{contract_text_for_prompt or facts_text}"
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
        "map_reduce": bool(use_map_reduce),
        "map_chunks": len(map_chunks),
        "source_chars": len(contract_text_for_prompt),
        "facts_chars": len(facts_text),
        "prompt_source": str(bound_prompt.get("source") or "system_default"),
        "prompt_binding_scene": "contract_audit.contract_summary",
        "prompt_binding_id": str(bound_prompt.get("prompt_id") or ""),
        "prompt_binding_name": str(bound_prompt.get("prompt_name") or ""),
    }

    try:
        attempt = _request_contract_summary_payload(
            llm,
            bound_prompt,
            budget["max_tokens"],
            trace_meta,
            len(facts_text) + len(contract_text_for_prompt),
        )
        if attempt["issues"]:
            logger.info(
                "contract_summary_retry audit_id=%s issues=%s",
                audit_id,
                ",".join(attempt["issues"]),
            )
            retry_attempt = _request_contract_summary_payload(
                llm,
                bound_prompt,
                budget["retry_max_tokens"],
                trace_meta,
                len(facts_text) + len(contract_text_for_prompt),
            )
            if len(retry_attempt["issues"]) <= len(attempt["issues"]):
                attempt = retry_attempt

        parsed = attempt["parsed"]
        issues = list(attempt["issues"])
        title = str(parsed.get("title") or "").strip() or summary_title
        about = str(parsed.get("about") or "").strip()
        core = str(parsed.get("core") or "").strip()
        tax = str(parsed.get("tax") or "").strip()
        has_content = bool(about or core or tax)
        if not has_content:
            status = "empty"
        elif issues:
            status = "partial"
        else:
            status = "ok"
        logger.info(
            "contract_summary_generation_done",
            audit_id=audit_id,
            status=status,
            chars=len(title) + len(about) + len(core) + len(tax),
            finish_reason=attempt["finish_reason"],
            issues=",".join(issues),
            map_reduce=bool(use_map_reduce),
            map_chunks=len(map_chunks),
            source=str(bound_prompt.get("source") or "system_default"),
            prompt_id=str(bound_prompt.get("prompt_id") or ""),
        )
        base.update({
            "title": title,
            "about": about,
            "core": core,
            "tax": tax,
            "status": status,
            "source": str(bound_prompt.get("source") or "system_default"),
            "prompt_id": str(bound_prompt.get("prompt_id") or ""),
            "prompt_name": str(bound_prompt.get("prompt_name") or ""),
            "issues": issues,
            "truncated": truncated,
            "source_chars": len(contract_text_for_prompt) + len(facts_text),
            "map_reduce": bool(use_map_reduce),
            "map_chunks": len(map_chunks),
        })
        if status == "ok":
            _write_contract_summary_cache(cache_path, dict(base))
        return base
    except Exception as exc:
        logger.warning(
            "contract_summary_generation_failed",
            audit_id=audit_id,
            error=str(exc),
        )
        base.update({"status": "error", "source": "error", "error": str(exc)})
        return base


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
    runtime_llm_cfg = _resolve_contract_audit_runtime_llm(cfg)
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
    context_window_tokens = _resolve_contract_audit_context_window_tokens(
        cfg, runtime_llm_cfg)
    output_reserve_tokens = max(
        int(classic_audit_retry_max_tokens),
        DEFAULT_CONTEXT_WINDOW_OUTPUT_RESERVE_TOKENS,
    )
    clause_text = _build_classic_clause_text(
        text=text,
        preview_clauses=preview_clauses,
        max_clause_chars=max_clause_chars,
        context_window_tokens=context_window_tokens,
        output_reserve_tokens=output_reserve_tokens,
    )

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
        "provider": str(runtime_llm_cfg.get("provider") or ""),
        "context_window_tokens": int(context_window_tokens),
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
            "classic_context_window_tokens": int(context_window_tokens),
            "classic_provider": str(runtime_llm_cfg.get("provider") or ""),
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
