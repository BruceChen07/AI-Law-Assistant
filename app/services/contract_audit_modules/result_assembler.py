"""
Result Assembler.
职责: 组装审计结果，附加风险原文定位，归一化输出结构。
输入输出: 接收审计原始结果和条款列表，返回标准化后的结果字典。
异常场景: 输入不完整时返回基础结构的默认空值字典。
"""
import structlog
from difflib import SequenceMatcher
from typing import Dict, Any, List, Optional
from app.services.utils.contract_audit_utils import norm_text
from app.services.audit_utils import _safe_int, _normalize_citation_item, _enrich_citations, _normalize_risk_level
from app.services.audit_tax import _filter_tax_audit_result

logger = structlog.get_logger(__name__)


def _normalize_block_text(block: Dict[str, Any]) -> str:
    if not isinstance(block, dict):
        return ""
    for key in ["text", "content", "value"]:
        value = str(block.get(key) or "").strip()
        if value:
            return value
    lines = block.get("lines")
    if isinstance(lines, list):
        joined = " ".join(str(item or "").strip()
                          for item in lines if str(item or "").strip())
        if joined:
            return joined
    return ""


def _pick_manifest_page_no(
    risk: Dict[str, Any],
    preview_manifest: Optional[Dict[str, Any]],
    fallback_page_no: int,
) -> int:
    pages = (preview_manifest or {}).get("pages")
    if not isinstance(pages, list) or not pages:
        return fallback_page_no
    queries: List[str] = []
    location = risk.get("location") if isinstance(risk.get("location"), dict) else {}
    for key in ["quote", "evidence", "issue", "suggestion"]:
        if key == "quote":
            q = str(location.get(key) or "").strip()
        else:
            q = str(risk.get(key) or "").strip()
        if len(q) >= 6:
            queries.append(q)
    if not queries:
        return fallback_page_no
    best_page_no = fallback_page_no
    best_score = -1.0
    for page in pages:
        page_no = _safe_int(page.get("page_no"), 0)
        blocks = page.get("blocks") if isinstance(page.get("blocks"), list) else []
        if page_no <= 0 or not blocks:
            continue
        page_best = 0.0
        for block in blocks:
            normalized_block = norm_text(_normalize_block_text(block))
            if len(normalized_block) < 6:
                continue
            for q in queries:
                qn = norm_text(q)
                if len(qn) < 6:
                    continue
                if qn in normalized_block:
                    score = 1.0
                else:
                    score = SequenceMatcher(
                        None,
                        qn[:220],
                        normalized_block[:600],
                    ).ratio()
                if score > page_best:
                    page_best = score
        if page_best > best_score:
            best_score = page_best
            best_page_no = page_no
    return best_page_no if best_score >= 0.18 else fallback_page_no


def attach_risk_locations(
    audit: Dict[str, Any],
    clauses: List[Dict[str, Any]],
    preview_manifest: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """为风险项附加具体的条款定位信息和相似度得分。"""
    risks = audit.get("risks")
    if not isinstance(risks, list) or not clauses:
        return audit
    prepared_clauses = []
    for clause in clauses:
        c_text = str(clause.get("clause_text", "") or "")
        prepared_clauses.append((clause, norm_text(c_text), c_text))
    for idx, risk in enumerate(risks):
        if not isinstance(risk, dict):
            continue
        queries = []
        for key in ["evidence", "issue", "suggestion"]:
            q = str(risk.get(key, "") or "").strip()
            if len(q) >= 6:
                queries.append(q)
        best_clause = None
        best_score = -1.0
        matched_quote = ""
        for clause, normalized_clause_text, raw_clause_text in prepared_clauses:
            local_best = 0.0
            local_quote = ""
            for q in queries:
                qn = norm_text(q)
                if len(qn) < 6:
                    continue
                if qn in normalized_clause_text:
                    score = 1.0
                else:
                    score = SequenceMatcher(
                        None,
                        qn[:200],
                        normalized_clause_text[:500],
                    ).ratio()
                if score > local_best:
                    local_best = score
                    local_quote = q if qn in normalized_clause_text else raw_clause_text[:120]
            if local_best > best_score:
                best_score = local_best
                best_clause = clause
                matched_quote = local_quote
        if (not best_clause) or best_score < 0.15:
            risk["location"] = {
                "risk_id": f"r{idx + 1}",
                "clause_id": "",
                "anchor_id": "",
                "page_no": 0,
                "paragraph_no": "",
                "clause_path": "",
                "quote": "",
                "score": 0.0,
            }
            continue
        risk["location"] = {
            "risk_id": f"r{idx + 1}",
            "clause_id": best_clause.get("clause_id", ""),
            "anchor_id": best_clause.get("anchor_id", ""),
            "page_no": _pick_manifest_page_no(
                risk,
                preview_manifest,
                _safe_int(best_clause.get("page_no"), 0),
            ),
            "paragraph_no": str(best_clause.get("paragraph_no") or ""),
            "clause_path": best_clause.get("clause_path", ""),
            "quote": matched_quote,
            "score": round(max(0.0, best_score), 4),
        }
    return audit

def normalize_audit_result(
    parsed: Any,
    raw_text: str,
    evidence_items: List[Dict[str, Any]],
    lang: str,
    tax_only: bool = True
) -> Dict[str, Any]:
    """将任意解析结果归一化为标准的审计输出格式。"""
    if not isinstance(parsed, dict):
        parsed = {}
    summary = str(parsed.get("summary", "") or "")
    risks = parsed.get("risks")
    if not isinstance(risks, list):
        risks = []
    executive_opinion = parsed.get("executive_opinion")
    if not isinstance(executive_opinion, list):
        executive_opinion = []
    risk_summary = parsed.get("risk_summary")
    if not isinstance(risk_summary, dict):
        risk_summary = {"high": 0, "medium": 0, "low": 0}
    citations = parsed.get("citations")
    if not isinstance(citations, list):
        citations = []
    if not citations and evidence_items:
        citations = [
            _normalize_citation_item(
                {
                    "citation_id": it.get("citation_id", ""),
                    "law_title": it.get("law_title", "") or it.get("title", ""),
                    "title": it.get("title", ""),
                    "article_no": it.get("article_no", ""),
                    "excerpt": it.get("excerpt", ""),
                    "content": it.get("content", ""),
                    "effective_date": it.get("effective_date", ""),
                    "expiry_date": it.get("expiry_date", ""),
                    "region": it.get("region", ""),
                    "industry": it.get("industry", "")
                }
            )
            for it in evidence_items
        ]
    citations = _enrich_citations(citations, evidence_items)
    if tax_only:
        filtered = _filter_tax_audit_result(
            summary=summary,
            executive_opinion=executive_opinion,
            risks=risks,
            citations=citations,
            lang=lang
        )
        summary = filtered["summary"]
        executive_opinion = filtered["executive_opinion"]
        risk_summary = filtered["risk_summary"]
        risks = filtered["risks"]
        citations = filtered["citations"]
    if isinstance(risks, list):
        normalized_summary = {"high": 0, "medium": 0, "low": 0}
        for r in risks:
            if isinstance(r, dict):
                normalized_summary[_normalize_risk_level(r.get("level"))] += 1
        risk_summary = normalized_summary
    out = {
        "summary": summary,
        "executive_opinion": executive_opinion,
        "risk_summary": {
            "high": _safe_int(risk_summary.get("high", 0), 0),
            "medium": _safe_int(risk_summary.get("medium", 0), 0),
            "low": _safe_int(risk_summary.get("low", 0), 0),
        },
        "risks": risks,
        "citations": citations
    }
    if not summary and not risks and raw_text:
        out["raw"] = raw_text
    return out
