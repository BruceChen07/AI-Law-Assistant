from __future__ import annotations

import re
from typing import Dict, List

from pydantic import BaseModel


class ValidationIssue(BaseModel):
    risk_id: str
    message: str


class ValidationResult(BaseModel):
    ok: bool
    issues: List[ValidationIssue]


def _is_english_mode(report: Dict) -> bool:
    lang = str(report.get("language") or "").strip().lower().replace("_", "-")
    if lang.startswith("en"):
        return True
    if lang.startswith("zh"):
        return False
    risks = report.get("risks") if isinstance(
        report.get("risks"), list) else []
    sample = " ".join(
        f"{str(r.get('law_title') or '')} {str(r.get('article_no') or '')}" for r in risks[:20]
    )
    letters = len(re.findall(r"[A-Za-z]", sample))
    cjk = len(re.findall(r"[\u4e00-\u9fff]", sample))
    return letters > cjk


def _normalize_article_text(article: str) -> str:
    return re.sub(r"\s+", " ", str(article or "").strip())


def _build_article_candidates(article: str) -> List[str]:
    raw = _normalize_article_text(article)
    if not raw:
        return []
    out = [raw]
    lower = raw.lower()
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", raw):
        out.append(f"第{raw}条")
        out.append(f"Article {raw}")
    m = re.match(
        r"^(article|section|chapter|part)\s*([0-9ivxlcdm]+(?:\.[0-9]+)*)$",
        lower,
    )
    if m:
        prefix = m.group(1).capitalize()
        idx = m.group(2).upper()
        out.append(f"{prefix} {idx}")
        out.append(f"{idx}")
    return list(dict.fromkeys(out))


def validate_report_citations(report: Dict, legal_catalog: Dict[str, List[str]]) -> ValidationResult:
    issues: List[ValidationIssue] = []
    english_mode = _is_english_mode(report)
    risks = report.get("risks") if isinstance(
        report.get("risks"), list) else []
    for idx, risk in enumerate(risks, start=1):
        rid = str(risk.get("risk_id") or f"r{idx}")
        law = str(risk.get("law_title") or "").strip()
        article = str(risk.get("article_no") or "").strip()
        if not law or not article:
            msg = "Missing citation fields: law_title/article_no" if english_mode else "缺失法条引用字段"
            issues.append(ValidationIssue(risk_id=rid, message=msg))
            continue
        allowed = legal_catalog.get(law) or []
        allowed_set = {_normalize_article_text(x).lower()
                       for x in allowed if str(x or "").strip()}
        expected_items = _build_article_candidates(article)
        matched = any(_normalize_article_text(x).lower()
                      in allowed_set for x in expected_items)
        if not matched:
            expected = expected_items[0] if expected_items else article
            msg = f"Citation not found: {law} {expected}" if english_mode else f"法条不存在: {law} {expected}"
            issues.append(ValidationIssue(
                risk_id=rid, message=msg))
    return ValidationResult(ok=len(issues) == 0, issues=issues)
