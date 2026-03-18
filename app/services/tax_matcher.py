import re
import json
import logging
from datetime import datetime
from app.services.crud import (
    get_tax_contract_document,
    list_contract_clauses,
    list_tax_rules,
    clear_clause_rule_matches_by_contract,
    create_clause_rule_matches,
)

logger = logging.getLogger("law_assistant")


def _extract_percent(text: str) -> str:
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%", str(text or ""))
    return f"{m.group(1)}%" if m else ""


def _extract_deadline_days(text: str) -> int:
    m = re.search(r"([0-9]{1,3})\s*(?:日内|天内|个工作日内)", str(text or ""))
    return int(m.group(1)) if m else 0


def _keywords(text: str) -> set[str]:
    parts = re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z0-9_]+", str(text or ""))
    stop = {"应当", "按照", "以及", "或者", "进行", "相关", "条款", "规定", "合同"}
    return {p for p in parts if p not in stop}


def _overlap_score(rule_text: str, clause_text: str) -> float:
    a = _keywords(rule_text)
    b = _keywords(clause_text)
    if not a or not b:
        return 0.0
    inter = len(a.intersection(b))
    union = len(a.union(b))
    return round(inter / max(union, 1), 4)


def evaluate_clause_rule_match(clause: dict, rule: dict) -> dict:
    clause_text = str(clause.get("clause_text") or "")
    rule_text = " ".join(
        [
            str(rule.get("source_text") or ""),
            str(rule.get("required_action") or ""),
            str(rule.get("prohibited_action") or ""),
            str(rule.get("numeric_constraints") or ""),
            str(rule.get("deadline_constraints") or ""),
        ]
    ).strip()
    reason = ""
    label = "not_mentioned"
    score = 0.12
    if str(rule.get("rule_type") or "") == "tax_rate":
        rule_rate = _extract_percent(rule.get("numeric_constraints") or rule_text)
        clause_rate = _extract_percent(clause_text)
        if not clause_rate:
            label = "not_mentioned"
            score = 0.15
            reason = "clause_has_no_tax_rate"
        elif clause_rate == rule_rate and rule_rate:
            label = "compliant"
            score = 0.96
            reason = "tax_rate_consistent"
        elif rule_rate and clause_rate != rule_rate:
            label = "non_compliant"
            score = 0.99
            reason = "tax_rate_conflict"
    elif str(rule.get("rule_type") or "") == "deadline":
        rule_days = _extract_deadline_days(rule.get("deadline_constraints") or rule_text)
        clause_days = _extract_deadline_days(clause_text)
        if clause_days == 0:
            label = "not_mentioned"
            score = 0.2
            reason = "deadline_missing"
        elif rule_days > 0 and clause_days <= rule_days:
            label = "compliant"
            score = 0.9
            reason = "deadline_within_limit"
        elif rule_days > 0 and clause_days > rule_days:
            label = "non_compliant"
            score = 0.95
            reason = "deadline_exceeds_limit"
        else:
            label = "not_mentioned"
            score = 0.22
            reason = "deadline_unclear"
    else:
        overlap = _overlap_score(rule_text, clause_text)
        if overlap >= 0.28:
            label = "compliant"
            score = min(0.88, 0.55 + overlap)
            reason = "keyword_overlap_sufficient"
        elif overlap <= 0.05:
            label = "not_mentioned"
            score = 0.12
            reason = "keyword_overlap_missing"
        else:
            label = "not_mentioned"
            score = 0.3 + overlap
            reason = "keyword_overlap_weak"
    evidence = {
        "reason": reason,
        "clause_excerpt": clause_text[:300],
        "rule_excerpt": str(rule.get("source_text") or "")[:300],
        "rule_type": rule.get("rule_type", ""),
        "rule_article_no": rule.get("article_no", ""),
        "evaluated_at": datetime.utcnow().isoformat(),
    }
    return {
        "clause_id": clause.get("id", ""),
        "rule_id": rule.get("id", ""),
        "match_score": round(float(score), 4),
        "match_label": label,
        "evidence_json": json.dumps(evidence, ensure_ascii=False),
    }


def _pick_matches_for_clause(evaluated: list[dict], top_k: int = 5) -> list[dict]:
    ranked = sorted(
        evaluated,
        key=lambda x: (
            0 if x["match_label"] == "non_compliant" else (1 if x["match_label"] == "not_mentioned" else 2),
            -float(x["match_score"]),
        ),
    )
    selected = ranked[:max(1, int(top_k))]
    extra = [x for x in ranked if x["match_label"] == "non_compliant" and x not in selected]
    return selected + extra


def match_contract_against_rules(cfg, contract_id: str, operator_id: str = "", top_k_per_clause: int = 5) -> dict:
    contract = get_tax_contract_document(cfg, contract_id)
    if not contract:
        raise ValueError("contract document not found")
    clauses = list_contract_clauses(cfg, contract_id, limit=5000)
    if not clauses:
        raise ValueError("contract clauses not found, run analyze first")
    rules = list_tax_rules(cfg, limit=5000)
    if not rules:
        raise ValueError("tax rules not found, run regulation parse first")
    logger.info(
        "tax_match_start contract_id=%s operator=%s clauses=%s rules=%s top_k_per_clause=%s",
        contract_id,
        operator_id,
        len(clauses),
        len(rules),
        int(top_k_per_clause),
    )
    all_matches = []
    for clause in clauses:
        evaluated = [evaluate_clause_rule_match(clause, rule) for rule in rules]
        all_matches.extend(_pick_matches_for_clause(evaluated, top_k=top_k_per_clause))
    clear_clause_rule_matches_by_contract(cfg, contract_id)
    create_clause_rule_matches(cfg, all_matches, created_by=operator_id)
    compliant = len([x for x in all_matches if x["match_label"] == "compliant"])
    non_compliant = len([x for x in all_matches if x["match_label"] == "non_compliant"])
    not_mentioned = len([x for x in all_matches if x["match_label"] == "not_mentioned"])
    logger.info(
        "tax_match_done contract_id=%s total=%s compliant=%s non_compliant=%s not_mentioned=%s",
        contract_id,
        len(all_matches),
        compliant,
        non_compliant,
        not_mentioned,
    )
    return {
        "contract_id": contract_id,
        "total_matches": len(all_matches),
        "compliant_count": compliant,
        "non_compliant_count": non_compliant,
        "not_mentioned_count": not_mentioned,
    }
