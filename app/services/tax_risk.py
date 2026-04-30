import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from app.services.tax_common import parse_llm_json_object
from app.services.crud import (
    get_tax_contract_document,
    list_clause_rule_matches_by_contract,
    clear_audit_issues_by_contract,
    create_audit_issues,
    get_audit_issue,
    update_audit_issue_review,
    insert_audit_trace,
)
from app.memory_system.experience_repo import record_user_feedback

logger = logging.getLogger("law_assistant")


def _is_english_mode(doc: dict, matches: list[dict]) -> bool:
    sample_parts = [str((doc or {}).get("original_filename") or "")]
    if isinstance(doc, dict):
        sample_parts.append(str(doc.get("language") or ""))
    for m in (matches or [])[:20]:
        try:
            evidence = json.loads(m.get("evidence_json") or "{}")
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(
                "tax_risk_evidence_json_invalid_for_lang_detect clause_id=%s err=%s",
                str(m.get("clause_id") or ""),
                str(e),
            )
            evidence = {}
        sample_parts.append(str(evidence.get("reason") or ""))
    sample = " ".join(x for x in sample_parts if x).strip()
    letters = len(re.findall(r"[A-Za-z]", sample))
    cjk = len(re.findall(r"[\u4e00-\u9fff]", sample))
    return letters > cjk


def _risk_level_by_label(match_label: str) -> str:
    label = str(match_label or "")
    if label == "non_compliant":
        return "high"
    if label == "not_mentioned":
        return "medium"
    return "low"


def _build_issue_text(match_item: dict, english_mode: bool = False) -> str:
    label = str(match_item.get("match_label") or "")
    if label == "non_compliant":
        return "Contract clauses conflict with tax rules and may trigger compliance risks." if english_mode else "合同条款与财税规则存在冲突，可能触发税务合规风险。"
    if label == "not_mentioned":
        return "Contract clauses do not explicitly cover key tax obligations, which may cause omission risks." if english_mode else "合同条款未明确覆盖关键财税义务，存在遗漏风险。"
    return "Contract clauses are generally aligned with tax rules." if english_mode else "合同条款与财税规则基本一致。"


def _build_suggestion(match_item: dict, english_mode: bool = False) -> str:
    label = str(match_item.get("match_label") or "")
    if label == "non_compliant":
        return "Revise clause values or obligations according to regulations, and add clear execution criteria." if english_mode else "请按法规要求修订条款数值或义务描述，并补充明确执行口径。"
    if label == "not_mentioned":
        return "Add key terms such as tax rate, timeline, invoicing requirements, and tax payment responsibilities." if english_mode else "请补充税率、时限、开票与纳税责任等关键条款。"
    return "Keep current clauses and retain supporting regulations in appendices." if english_mode else "建议保留现有约定并在附件中保留法规依据。"


def generate_issues_from_matches(cfg, contract_id: str, operator_id: str = "", llm=None) -> dict:
    doc = get_tax_contract_document(cfg, contract_id)
    if not doc:
        raise ValueError("contract document not found")
    matches = list_clause_rule_matches_by_contract(
        cfg, contract_id, limit=5000)
    if not matches:
        raise ValueError("clause rule matches not found, run match first")
    english_mode = _is_english_mode(doc, matches)
    logger.info(
        "tax_risk_generate_start contract_id=%s operator=%s matches=%s",
        contract_id,
        operator_id,
        len(matches),
    )

    if llm is None:
        raise ValueError("llm service is required")

    def process_match(m):
        label = str(m.get("match_label") or "")
        if label not in ["non_compliant", "not_mentioned"]:
            return None

        evidence = {}
        try:
            evidence = json.loads(m.get("evidence_json") or "{}")
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(
                "tax_risk_evidence_json_invalid_for_issue clause_id=%s err=%s",
                str(m.get("clause_id") or ""),
                str(e),
            )

        clause_text = evidence.get("clause_excerpt", "")
        rule_text = evidence.get("rule_excerpt", "")

        # Build prompt for dynamic risk issue generation
        lang_instruction = "English" if english_mode else "Chinese"
        prompt = f"""
        You are a senior tax auditor. Based on the following contract clause and tax rule, generate a specific risk issue description and a concrete revision suggestion.
        
        Match Type: {label} ("non_compliant" means conflict, "not_mentioned" means missing key obligations)
        Contract Clause: "{clause_text}"
        Tax Rule: "{rule_text}"
        
        Respond ONLY with a JSON object in {lang_instruction} language, using this format:
        {{
            "issue_text": "Detailed description of the specific risk or conflict found.",
            "suggestion": "Concrete, actionable suggestion on how to revise the clause."
        }}
        """

        issue_text = _build_issue_text(m, english_mode=english_mode)
        suggestion = _build_suggestion(m, english_mode=english_mode)

        try:
            response, _ = llm.chat(
                [{"role": "user", "content": prompt}],
                overrides={"model": cfg.get("llm_config", {}).get(
                    "model", "qwen3.5-plus")},
            )
            result = parse_llm_json_object(response)
            issue_text = result.get("issue_text", issue_text)
            suggestion = result.get("suggestion", suggestion)
        except Exception as e:
            logger.error(f"LLM risk generation failed: {e}")

        return {
            "contract_document_id": contract_id,
            "clause_id": m.get("clause_id", ""),
            "rule_id": m.get("rule_id", ""),
            "risk_level": _risk_level_by_label(label),
            "issue_text": issue_text,
            "suggestion": suggestion,
            "reviewer_status": "pending",
            "reviewer_note": evidence.get("reason", ""),
        }

    issue_items = []
    with ThreadPoolExecutor(max_workers=cfg.get("tax_audit_max_workers", 4)) as executor:
        results = executor.map(process_match, matches)
        for item in results:
            if item is not None:
                issue_items.append(item)

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
    feedback_meta = {}
    try:
        feedback_meta = record_user_feedback(
            cfg=cfg,
            issue=issue,
            reviewer_status=status,
            reviewer_note=reviewer_note,
            operator_id=operator_id,
            risk_level=normalized_risk or issue.get("risk_level", ""),
        )
    except Exception as e:
        logger.warning(
            "record_user_feedback_failed issue_id=%s err=%s", issue_id, str(e))
        feedback_meta = {"saved": False,
                         "reason": "exception", "error": str(e)}
    updated = get_audit_issue(cfg, issue_id)
    logger.info(
        "tax_issue_review_done issue_id=%s reviewer_status=%s risk_level=%s action=%s feedback_saved=%s outcome=%s",
        issue_id,
        updated.get("reviewer_status", status),
        updated.get("risk_level", normalized_risk or issue.get(
            "risk_level", "")),
        action,
        bool(feedback_meta.get("saved", False)),
        str(feedback_meta.get("outcome", "")),
    )
    return {
        "issue_id": issue_id,
        "reviewer_status": updated.get("reviewer_status", status),
        "risk_level": updated.get("risk_level", normalized_risk or issue.get("risk_level", "")),
        "reviewer_note": updated.get("reviewer_note", reviewer_note),
    }
