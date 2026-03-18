import json
import logging
from app.services.crud import (
    get_tax_contract_document,
    list_clause_rule_matches_by_contract,
    clear_audit_issues_by_contract,
    create_audit_issues,
    get_audit_issue,
    update_audit_issue_review,
    insert_audit_trace,
)

logger = logging.getLogger("law_assistant")


def _risk_level_by_label(match_label: str) -> str:
    label = str(match_label or "")
    if label == "non_compliant":
        return "high"
    if label == "not_mentioned":
        return "medium"
    return "low"


def _build_issue_text(match_item: dict) -> str:
    label = str(match_item.get("match_label") or "")
    if label == "non_compliant":
        return "合同条款与财税规则存在冲突，可能触发税务合规风险。"
    if label == "not_mentioned":
        return "合同条款未明确覆盖关键财税义务，存在遗漏风险。"
    return "合同条款与财税规则基本一致。"


def _build_suggestion(match_item: dict) -> str:
    label = str(match_item.get("match_label") or "")
    if label == "non_compliant":
        return "请按法规要求修订条款数值或义务描述，并补充明确执行口径。"
    if label == "not_mentioned":
        return "请补充税率、时限、开票与纳税责任等关键条款。"
    return "建议保留现有约定并在附件中保留法规依据。"


def generate_issues_from_matches(cfg, contract_id: str, operator_id: str = "") -> dict:
    doc = get_tax_contract_document(cfg, contract_id)
    if not doc:
        raise ValueError("contract document not found")
    matches = list_clause_rule_matches_by_contract(cfg, contract_id, limit=5000)
    if not matches:
        raise ValueError("clause rule matches not found, run match first")
    logger.info(
        "tax_risk_generate_start contract_id=%s operator=%s matches=%s",
        contract_id,
        operator_id,
        len(matches),
    )
    issue_items = []
    for m in matches:
        label = str(m.get("match_label") or "")
        if label not in ["non_compliant", "not_mentioned"]:
            continue
        evidence = {}
        try:
            evidence = json.loads(m.get("evidence_json") or "{}")
        except Exception:
            evidence = {}
        issue_items.append(
            {
                "contract_document_id": contract_id,
                "clause_id": m.get("clause_id", ""),
                "rule_id": m.get("rule_id", ""),
                "risk_level": _risk_level_by_label(label),
                "issue_text": _build_issue_text(m),
                "suggestion": _build_suggestion(m),
                "reviewer_status": "pending",
                "reviewer_note": evidence.get("reason", ""),
            }
        )
    clear_audit_issues_by_contract(cfg, contract_id)
    create_audit_issues(cfg, issue_items, created_by=operator_id)
    high = len([x for x in issue_items if x["risk_level"] == "high"])
    medium = len([x for x in issue_items if x["risk_level"] == "medium"])
    low = len([x for x in issue_items if x["risk_level"] == "low"])
    logger.info(
        "tax_risk_generate_done contract_id=%s total=%s high=%s medium=%s low=%s",
        contract_id,
        len(issue_items),
        high,
        medium,
        low,
    )
    return {
        "contract_id": contract_id,
        "total": len(issue_items),
        "high": high,
        "medium": medium,
        "low": low,
    }


def review_audit_issue(
    cfg,
    issue_id: str,
    reviewer_status: str,
    reviewer_note: str = "",
    operator_id: str = "",
    risk_level: str = "",
) -> dict:
    logger.info(
        "tax_issue_review_start issue_id=%s operator=%s reviewer_status=%s risk_level=%s",
        issue_id,
        operator_id,
        reviewer_status,
        risk_level,
    )
    issue = get_audit_issue(cfg, issue_id)
    if not issue:
        raise ValueError("audit issue not found")
    allowed = {"confirmed", "rejected", "downgraded", "exception", "pending"}
    status = str(reviewer_status or "").strip().lower()
    if status not in allowed:
        raise ValueError("invalid reviewer status")
    normalized_risk = str(risk_level or "").strip().lower()
    if normalized_risk and normalized_risk not in {"high", "medium", "low"}:
        raise ValueError("invalid risk level")
    update_audit_issue_review(
        cfg,
        issue_id=issue_id,
        reviewer_status=status,
        reviewer_note=reviewer_note,
        risk_level=normalized_risk or None,
    )
    payload = json.dumps(
        {
            "reviewer_status": status,
            "reviewer_note": reviewer_note,
            "risk_level": normalized_risk,
        },
        ensure_ascii=False,
    )
    action = "reviewer_confirm"
    if status in {"rejected", "downgraded", "exception"}:
        action = "reviewer_override"
    insert_audit_trace(
        cfg,
        issue_id=issue_id,
        action_type=action,
        operator=operator_id,
        payload_json=payload,
        created_by=operator_id,
    )
    updated = get_audit_issue(cfg, issue_id)
    logger.info(
        "tax_issue_review_done issue_id=%s reviewer_status=%s risk_level=%s action=%s",
        issue_id,
        updated.get("reviewer_status", status),
        updated.get("risk_level", normalized_risk or issue.get("risk_level", "")),
        action,
    )
    return {
        "issue_id": issue_id,
        "reviewer_status": updated.get("reviewer_status", status),
        "risk_level": updated.get("risk_level", normalized_risk or issue.get("risk_level", "")),
        "reviewer_note": updated.get("reviewer_note", reviewer_note),
    }
