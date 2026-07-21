from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _risk_level_rank(level: str) -> int:
    normalized = str(level or "").strip().lower()
    return {"high": 3, "medium": 2, "low": 1}.get(normalized, 0)


def _normalize_risk_summary(audit: Dict[str, Any]) -> Dict[str, int]:
    summary = {"high": 0, "medium": 0, "low": 0}
    for item in list((audit or {}).get("risks") or []):
        if not isinstance(item, dict):
            continue
        level = str(item.get("level") or "medium").strip().lower()
        if level not in summary:
            level = "medium"
        summary[level] += 1
    return summary


def _build_key_findings(audit: Dict[str, Any]) -> List[Dict[str, Any]]:
    risks = [item for item in list((audit or {}).get("risks") or []) if isinstance(item, dict)]
    risks.sort(
        key=lambda item: (
            -_risk_level_rank(str(item.get("level") or "")),
            -_safe_float(((item.get("location") or {}).get("score") if isinstance(item.get("location"), dict) else item.get("confidence")), 0.0),
        )
    )
    findings: List[Dict[str, Any]] = []
    for item in risks[:5]:
        location = item.get("location") if isinstance(
            item.get("location"), dict) else {}
        findings.append(
            {
                "level": str(item.get("level") or "medium"),
                "issue": str(item.get("issue") or ""),
                "suggestion": str(item.get("suggestion") or ""),
                "clause_id": str(location.get("clause_id") or ""),
                "clause_path": str(location.get("clause_path") or ""),
                "confidence": round(
                    _safe_float(location.get("score"), _safe_float(item.get("confidence"), 0.0)), 4),
            }
        )
    return findings


def _build_review_conclusions(audit: Dict[str, Any], meta: Dict[str, Any]) -> List[Dict[str, Any]]:
    risk_summary = _normalize_risk_summary(audit)
    reliability_level = str(meta.get("reliability_level") or "medium")
    conclusions = [
        {
            "title": "总体风险分布",
            "detail": f"高风险 {risk_summary['high']} 项，中风险 {risk_summary['medium']} 项，低风险 {risk_summary['low']} 项。",
        },
        {
            "title": "审计可靠性",
            "detail": f"当前可靠性等级为 {reliability_level}，复核项 {int(meta.get('review_item_count') or 0)} 个，定向重试 {int(meta.get('review_retry_count') or 0)} 次。",
        },
    ]
    if int(meta.get("unresolved_review_item_count") or 0) > 0:
        conclusions.append(
            {
                "title": "待人工复核",
                "detail": f"仍有 {int(meta.get('unresolved_review_item_count') or 0)} 个复核问题未闭环，建议人工确认后再出具正式意见。",
            }
        )
    return conclusions


def _build_exception_items(audit: Dict[str, Any]) -> List[Dict[str, Any]]:
    issues = list(((audit or {}).get("legal_validation") or {}).get("issues") or [])
    out = []
    for item in issues:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "reason": str(item.get("reason") or item.get("risk_id") or ""),
                "severity": str(item.get("severity") or "medium"),
                "message": str(item.get("message") or ""),
                "clause_id": str(item.get("clause_id") or ""),
                "risk_id": str(item.get("risk_id") or ""),
            }
        )
    return out


def _build_risk_items(audit: Dict[str, Any]) -> List[Dict[str, Any]]:
    citations = audit.get("citations") if isinstance(audit.get("citations"), list) else []
    citation_map = {
        str(item.get("citation_id") or ""): item
        for item in citations
        if isinstance(item, dict) and str(item.get("citation_id") or "").strip()
    }
    items = []
    for idx, risk in enumerate(list(audit.get("risks") or []), start=1):
        if not isinstance(risk, dict):
            continue
        location = risk.get("location") if isinstance(
            risk.get("location"), dict) else {}
        citation = citation_map.get(str(risk.get("citation_id") or ""), {})
        issue_id = str(location.get("risk_id") or f"r{idx}")
        items.append(
            {
                "issue_id": issue_id,
                "risk_level": str(risk.get("level") or "medium"),
                "issue_text": str(risk.get("issue") or ""),
                "suggestion": str(risk.get("suggestion") or ""),
                "reviewer_status": "pending",
                "reviewer_note": "",
                "clause": {
                    "clause_id": str(location.get("clause_id") or ""),
                    "clause_path": str(location.get("clause_path") or ""),
                    "clause_text": str(location.get("quote") or risk.get("evidence") or ""),
                    "page_no": int(location.get("page_no") or 0),
                    "paragraph_no": str(location.get("paragraph_no") or ""),
                },
                "rule": {
                    "rule_id": str(risk.get("citation_id") or ""),
                    "law_title": str(citation.get("law_title") or risk.get("law_title") or ""),
                    "article_no": str(citation.get("article_no") or risk.get("article_no") or ""),
                    "source_text": str(citation.get("content") or citation.get("excerpt") or risk.get("evidence") or ""),
                },
            }
        )
    return items


def _build_evidence_items(audit: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for risk in list(audit.get("risks") or []):
        if not isinstance(risk, dict):
            continue
        location = risk.get("location") if isinstance(
            risk.get("location"), dict) else {}
        issue_id = str(location.get("risk_id") or "")
        evidence = str(risk.get("evidence") or "").strip()
        if not evidence:
            continue
        out.append(
            {
                "issue_id": issue_id,
                "law_title": str(risk.get("law_title") or ""),
                "article_no": str(risk.get("article_no") or ""),
                "source_text": evidence,
                "source_page": int(location.get("page_no") or 0),
                "source_paragraph": str(location.get("paragraph_no") or ""),
                "clause_id": str(location.get("clause_id") or ""),
                "clause_path": str(location.get("clause_path") or ""),
            }
        )
    return out


def _build_pipeline_stages(meta: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        {
            "stage": "token_admission",
            "status": "done",
            "requires_multi_pass": bool(meta.get("audit_token_budget_requires_multi_pass")),
            "reasons": list(meta.get("audit_token_budget_reasons") or []),
        },
        {
            "stage": "chunk_audit",
            "status": "done",
            "round_count": int(meta.get("multipass_round_count") or 1),
            "execution_path": str(meta.get("execution_path") or ""),
        },
        {
            "stage": "aggregation",
            "status": "done",
            "duplicate_items_removed": int(meta.get("duplicate_items_removed") or 0),
            "conflict_items_removed": int(meta.get("conflict_items_removed") or 0),
        },
        {
            "stage": "reliability_review",
            "status": "done",
            "review_item_count": int(meta.get("review_item_count") or 0),
            "retry_count": int(meta.get("review_retry_count") or 0),
            "reliability_level": str(meta.get("reliability_level") or "medium"),
        },
    ]


def build_contract_final_report(
    *,
    audit: Dict[str, Any],
    meta: Dict[str, Any],
    file_path: str = "",
    contract_id: str = "",
) -> Dict[str, Any]:
    risk_summary = _normalize_risk_summary(audit)
    risk_items = _build_risk_items(audit)
    evidence_items = _build_evidence_items(audit)
    review_conclusions = _build_review_conclusions(audit, meta)
    exception_items = _build_exception_items(audit)
    pipeline_stages = _build_pipeline_stages(meta)
    contract_filename = os.path.basename(str(file_path or "")) if str(file_path or "").strip() else ""
    report = {
        "contract_id": str(contract_id or ""),
        "generated_at": _utc_now(),
        "overview": {
            "contract_filename": contract_filename,
            "contract_parse_status": "done",
            "clause_count": int(meta.get("preview_clause_total") or 0),
            "issue_count": len(risk_items),
            "trace_count": int(meta.get("memory_llm_call_count") or 0),
        },
        "pipeline_summary": {
            "execution_path": str(meta.get("execution_path") or ""),
            "stages": pipeline_stages,
            "duration_ms": int(meta.get("audit_duration_ms") or 0),
            "retrieval_used": bool(meta.get("retrieval_used")),
        },
        "reliability_summary": {
            "level": str(meta.get("reliability_level") or "medium"),
            "review_item_count": int(meta.get("review_item_count") or 0),
            "retry_count": int(meta.get("review_retry_count") or 0),
            "unresolved_review_item_count": int(meta.get("unresolved_review_item_count") or 0),
        },
        "risk_summary": risk_summary,
        "review_summary": {
            "pending": len(risk_items),
            "confirmed": 0,
            "exception": len(exception_items),
        },
        "key_findings": _build_key_findings(audit),
        "review_conclusions": review_conclusions,
        "risk_items": risk_items,
        "evidence_items": evidence_items,
        "exception_items": exception_items,
    }
    return report
