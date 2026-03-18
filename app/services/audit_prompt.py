import json
import re
from typing import Dict, Any, List, Optional
from app.services.audit_utils import _normalize_citation_item

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
    evidence = _build_evidence_block(evidence_items or [], lang)
    if lang.lower() == "en":
        system = "You are a contract audit assistant. Output strict JSON only."
        focus = (
            "Prioritize tax-related regulations and tax implications in obligations, invoicing, withholding, and tax liabilities. "
            if tax_focus else
            ""
        )
        user = (
            focus +
            "Analyze the contract and identify tax/compliance and legal risks using the regulation evidence list when available. "
            "Include both tax-related findings and important legal compliance issues. "
            "Return strict JSON with keys: summary, executive_opinion, risk_summary, risks, citations. "
            "executive_opinion is an array of concise action-first suggestions for tax team. "
            "risk_summary is an object with keys: high, medium, low. "
            "risks is an array of objects with keys: level, type, issue, evidence, suggestion, law_reference, citation_id. "
            "citations is an array of objects with keys: citation_id, law_title, title, article_no, excerpt, content, effective_date, expiry_date, region, industry. "
            "If no risk, return risks as empty array and risk_summary values as 0. "
            "Do not force one item for each level. A level can be zero if not present. "
            "List all identifiable risks without artificial quantity limits. "
            "If no matching citation, citation_id should be empty string. "
            "Regulation evidence list:\n" + evidence + "\n"
            "Contract:\n" + text
        )
    else:
        system = "你是合同审计助手，仅输出严格JSON。"
        focus = "优先识别涉税条款风险，重点关注税率、开票、纳税义务、代扣代缴与税务合规责任。"
        if not tax_focus:
            focus = ""
        user = (
            focus +
            "对合同进行财税与法律风险审计，优先引用给定法规证据。"
            "需包含涉税风险与重要法律合规风险。"
            "仅输出严格JSON，包含字段：summary, executive_opinion, risk_summary, risks, citations。"
            "executive_opinion是给税务团队的前置审核意见数组，需简洁可执行。"
            "risk_summary是对象，字段：high, medium, low。"
            "risks为数组，元素字段：level, type, issue, evidence, suggestion, law_reference, citation_id。"
            "citations为数组，元素字段：citation_id, law_title, title, article_no, excerpt, content, effective_date, expiry_date, region, industry。"
            "如无风险，risks为空数组，risk_summary三个字段为0。"
            "不要为了覆盖等级而强行输出high/medium/low各一条，某个等级可以为0。"
            "请尽可能完整列出识别到的风险，不做人为数量限制。"
            "若无法匹配证据，citation_id留空字符串。"
            "法规证据列表：\n" + evidence + "\n"
            "合同内容：\n" + text
        )
    return {
        "system": system,
        "user": user
    }

def _estimate_prompt_tokens(text: str) -> int:
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    non_cjk = max(0, len(text) - cjk)
    return int(cjk * 1.1 + non_cjk / 3.8)
