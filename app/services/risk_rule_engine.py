from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any, Dict, Tuple

from app.services.audit_utils import _normalize_risk_level
from app.services.utils.contract_audit_utils import norm_text, citation_match_key


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def get_risk_rule_engine_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    raw = cfg.get("audit_risk_rule_engine") if isinstance(
        cfg.get("audit_risk_rule_engine"), dict) else {}
    return {
        "dedupe_similarity_threshold": max(
            0.6, min(1.0, _safe_float(raw.get("dedupe_similarity_threshold"), 0.9))),
        "dedupe_require_same_clause": str(
            raw.get("dedupe_require_same_clause", "true")).strip().lower() in {"1", "true", "yes", "on"},
        "keep_higher_confidence": str(
            raw.get("keep_higher_confidence", "true")).strip().lower() in {"1", "true", "yes", "on"},
        "require_citation_for_high_risk": str(
            raw.get("require_citation_for_high_risk", "true")).strip().lower() in {"1", "true", "yes", "on"},
    }


def risk_confidence_score(risk: Dict[str, Any]) -> float:
    if not isinstance(risk, dict):
        return 0.0
    location = risk.get("location") if isinstance(risk.get("location"), dict) else {}
    return _safe_float(location.get("score"), _safe_float(risk.get("confidence"), 0.0))


def build_risk_signature(risk: Dict[str, Any]) -> Dict[str, str]:
    location = risk.get("location") if isinstance(risk.get("location"), dict) else {}
    issue = norm_text(risk.get("issue") or "")
    suggestion = norm_text(risk.get("suggestion") or "")
    law_ref = citation_match_key(
        risk.get("law_title") or risk.get("law_reference") or risk.get("basis") or "",
        risk.get("article_no") or "",
    )
    return {
        "level": _normalize_risk_level(risk.get("level")),
        "issue": issue,
        "suggestion": suggestion,
        "law_ref": law_ref,
        "clause_id": str(location.get("clause_id") or ""),
    }


def compute_risk_similarity(left: Dict[str, Any], right: Dict[str, Any]) -> float:
    left_sig = build_risk_signature(left)
    right_sig = build_risk_signature(right)
    if left_sig["law_ref"] and left_sig["law_ref"] == right_sig["law_ref"]:
        base = 0.4
    else:
        base = 0.0
    issue_ratio = SequenceMatcher(None, left_sig["issue"], right_sig["issue"]).ratio()
    suggestion_ratio = SequenceMatcher(
        None, left_sig["suggestion"], right_sig["suggestion"]).ratio()
    return min(1.0, base + issue_ratio * 0.45 + suggestion_ratio * 0.15)


def are_risks_duplicates(
    left: Dict[str, Any],
    right: Dict[str, Any],
    config: Dict[str, Any],
) -> Tuple[bool, str]:
    left_sig = build_risk_signature(left)
    right_sig = build_risk_signature(right)
    if config.get("dedupe_require_same_clause", True):
        if left_sig["clause_id"] != right_sig["clause_id"]:
            return False, "different_clause"
    if left_sig["law_ref"] and left_sig["law_ref"] == right_sig["law_ref"] and left_sig["issue"] == right_sig["issue"]:
        return True, "same_issue_same_law"
    similarity = compute_risk_similarity(left, right)
    if similarity >= float(config.get("dedupe_similarity_threshold") or 0.9):
        return True, f"similarity:{similarity:.4f}"
    return False, f"similarity:{similarity:.4f}"


def choose_preferred_risk(
    current: Dict[str, Any],
    candidate: Dict[str, Any],
    config: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    current_score = risk_confidence_score(current)
    candidate_score = risk_confidence_score(candidate)
    current_level = _normalize_risk_level(current.get("level"))
    candidate_level = _normalize_risk_level(candidate.get("level"))
    level_rank = {"high": 3, "medium": 2, "low": 1}
    if config.get("keep_higher_confidence", True):
        if candidate_score > current_score:
            return candidate, current
        if candidate_score < current_score:
            return current, candidate
    if level_rank.get(candidate_level, 0) > level_rank.get(current_level, 0):
        return candidate, current
    return current, candidate
