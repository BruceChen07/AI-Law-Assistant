"""Prompt and parsing helpers for memory pipeline clause auditing."""

from typing import Any, Dict, List, Tuple
import json
import re


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
        return system, user

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
    return system, user
