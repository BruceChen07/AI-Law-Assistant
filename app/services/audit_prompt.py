import json
import re
from typing import Dict, Any, List, Optional
from app.services.audit_utils import _normalize_citation_item, _normalize_lang
from app.services.prompt_binding import render_prompt_messages


def _build_evidence_block(items: List[Dict[str, Any]], lang: str) -> str:
    if not items:
        return "[]"
    lines = []
    for i, it in enumerate(items, 1):
        item = _normalize_citation_item(it)
        lines.append(
            json.dumps(
                {
                    "idx": i,
                    "citation_id": item.get("citation_id"),
                    "law_title": item.get("law_title"),
                    "title": item.get("title"),
                    "article_no": item.get("article_no"),
                    "excerpt": item.get("excerpt"),
                    "content": item.get("content"),
                    "answer": item.get("answer"),
                    "effective_date": item.get("effective_date"),
                    "expiry_date": item.get("expiry_date"),
                    "region": item.get("region"),
                    "industry": item.get("industry"),
                    "final_score": item.get("final_score"),
                },
                ensure_ascii=False
            )
        )
    return "\n".join(lines)


def _build_prompt(
    text: str,
    lang: str,
    evidence_items: Optional[List[Dict[str, Any]]] = None,
    tax_focus: bool = True
) -> Dict[str, str]:
    norm_lang = _normalize_lang(lang, default="zh")
    evidence = _build_evidence_block(evidence_items or [], lang)
    rendered = render_prompt_messages(
        "audit_prompt.tax_audit_json",
        {
            "lang": norm_lang,
            "tax_focus": bool(tax_focus),
            "evidence_block": evidence,
            "contract_text": text,
        },
    )
    return {
        "system": str(rendered.get("rendered_system_text") or ""),
        "user": str(rendered.get("rendered_text") or ""),
    }


def _estimate_prompt_tokens(text: str) -> int:
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    non_cjk = max(0, len(text) - cjk)
    return int(cjk * 1.1 + non_cjk / 3.8)
