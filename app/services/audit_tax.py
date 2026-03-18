from typing import Dict, Any, List
from app.services.audit_utils import _safe_int, _normalize_risk_level

TAX_KEYWORDS = [
    "税",
    "税务",
    "税收",
    "增值税",
    "所得税",
    "企业所得税",
    "个人所得税",
    "印花税",
    "发票",
    "进项",
    "销项",
    "代扣代缴",
    "纳税",
    "完税",
    "vat",
    "tax",
    "withholding",
    "invoice"
]

def _tax_relevance_score(item: Dict[str, Any]) -> int:
    text = " ".join([
        str(item.get("title", "") or ""),
        str(item.get("article_no", "") or ""),
        str(item.get("content", "") or ""),
        str(item.get("answer", "") or ""),
    ]).lower()
    if not text.strip():
        return 0
    return sum(1 for kw in TAX_KEYWORDS if kw.lower() in text)

def _tax_query_prefix(lang: str) -> str:
    if lang.lower() == "en":
        return "tax compliance tax law vat withholding invoice"
    return "财税 税务 税收 增值税 所得税 发票 代扣代缴"

def _is_tax_related_text(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    return any(kw.lower() in text for kw in TAX_KEYWORDS)

def _is_tax_related_citation(item: Dict[str, Any]) -> bool:
    if _safe_int(item.get("tax_relevance", 0), 0) > 0:
        return True
    if _tax_relevance_score(item) > 0:
        return True
    industry = str(item.get("industry", "") or "")
    return _is_tax_related_text(industry)

def _build_tax_citation_map(citations: List[Dict[str, Any]]) -> Dict[str, bool]:
    out = {}
    for c in citations:
        cid = str(c.get("citation_id", "")).strip()
        if not cid:
            continue
        out[cid] = _is_tax_related_citation(c)
    return out

def _is_tax_related_risk(risk: Dict[str, Any], citation_tax_map: Dict[str, bool]) -> bool:
    cid = str(risk.get("citation_id", "")
              or risk.get("law_reference", "")).strip()
    if cid and citation_tax_map.get(cid):
        return True
    parts = [
        risk.get("type", ""),
        risk.get("issue", ""),
        risk.get("evidence", ""),
        risk.get("suggestion", ""),
        risk.get("law_reference", ""),
        risk.get("citation_id", "")
    ]
    return _is_tax_related_text(" ".join(str(p or "") for p in parts))

def _filter_tax_audit_result(
    summary: str,
    executive_opinion: List[Any],
    risks: List[Any],
    citations: List[Any],
    lang: str
) -> Dict[str, Any]:
    safe_citations = [c for c in citations if isinstance(c, dict)]
    citation_tax_map = _build_tax_citation_map(safe_citations)
    tax_risks = [
        r for r in risks
        if isinstance(r, dict) and _is_tax_related_risk(r, citation_tax_map)
    ]
    selected_citation_ids = {
        str(r.get("citation_id", "") or r.get("law_reference", "")).strip()
        for r in tax_risks
        if isinstance(r, dict)
    }
    selected_citation_ids = {x for x in selected_citation_ids if x}
    tax_citations = []
    for c in safe_citations:
        cid = str(c.get("citation_id", "")).strip()
        if cid and (cid in selected_citation_ids or citation_tax_map.get(cid)):
            tax_citations.append(c)
    if not tax_citations:
        tax_citations = [
            c for c in safe_citations if _is_tax_related_citation(c)]
    tax_opinions = [
        str(x).strip()
        for x in executive_opinion
        if str(x).strip() and _is_tax_related_text(x)
    ]
    if not tax_opinions and tax_risks:
        tax_opinions = [
            str(r.get("suggestion", "")).strip()
            for r in tax_risks
            if str(r.get("suggestion", "")).strip()
        ][:3]
    risk_summary = {"high": 0, "medium": 0, "low": 0}
    for r in tax_risks:
        risk_summary[_normalize_risk_level(r.get("level"))] += 1
    tax_summary = summary
    if not _is_tax_related_text(summary):
        if lang.lower() == "en":
            tax_summary = "No explicit tax-related risks identified. Non-tax items are filtered out."
        else:
            tax_summary = "未识别到明确涉税风险，非税务内容已过滤。"
    return {
        "summary": tax_summary,
        "executive_opinion": tax_opinions,
        "risk_summary": risk_summary,
        "risks": tax_risks,
        "citations": tax_citations
    }
