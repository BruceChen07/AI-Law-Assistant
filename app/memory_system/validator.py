from __future__ import annotations

from typing import Dict, List

from pydantic import BaseModel


class ValidationIssue(BaseModel):
    risk_id: str
    message: str


class ValidationResult(BaseModel):
    ok: bool
    issues: List[ValidationIssue]


def validate_report_citations(report: Dict, legal_catalog: Dict[str, List[str]]) -> ValidationResult:
    issues: List[ValidationIssue] = []
    risks = report.get("risks") if isinstance(report.get("risks"), list) else []
    for idx, risk in enumerate(risks, start=1):
        rid = str(risk.get("risk_id") or f"r{idx}")
        law = str(risk.get("law_title") or "").strip()
        article = str(risk.get("article_no") or "").strip()
        if not law or not article:
            issues.append(ValidationIssue(risk_id=rid, message="缺失法条引用字段"))
            continue
        expected = article if "条" in article else f"第{article}条"
        allowed = legal_catalog.get(law) or []
        if expected not in allowed:
            issues.append(ValidationIssue(risk_id=rid, message=f"法条不存在: {law} {expected}"))
    return ValidationResult(ok=len(issues) == 0, issues=issues)
