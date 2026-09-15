"""Prompt and parsing helpers for memory pipeline clause auditing."""

from typing import Any, Dict, List, Tuple
import json
import re
from app.services.prompt_binding import render_prompt_messages


def format_failure_patterns(patterns: List[Dict[str, Any]], lang: str) -> str:
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


def format_case_memories(hits: List[Dict[str, Any]], lang: str) -> str:
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


def format_workflow_memories(hits: List[Dict[str, Any]], lang: str) -> str:
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


def load_llm_json_object(raw_text: str) -> Dict[str, Any]:
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
    last_error = None
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


def build_clause_prompt(
    *,
    norm_lang: str,
    evidence_whitelist_text: str,
    short_memory: str,
    case_memory_block: str,
    failure_patterns_block: str,
    workflow_memory_block: str,
    global_context_block: str,
    long_memory_block: str,
    clause_title: str,
    clause_text: str,
) -> Tuple[str, str]:
    rendered = render_prompt_messages(
        "memory_pipeline.clause_audit",
        {
            "norm_lang": norm_lang,
            "evidence_whitelist_text": evidence_whitelist_text,
            "short_memory": short_memory,
            "case_memory_block": case_memory_block,
            "failure_patterns_block": failure_patterns_block,
            "workflow_memory_block": workflow_memory_block,
            "global_context_block": global_context_block,
            "long_memory_block": long_memory_block,
            "clause_title": clause_title,
            "clause_text": clause_text,
        },
    )
    return (
        str(rendered.get("rendered_system_text") or ""),
        str(rendered.get("rendered_text") or ""),
    )
