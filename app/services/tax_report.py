import os
import json
import logging
from datetime import datetime, timezone
from app.services.docx_renderer import render_tax_audit_docx
from app.services.crud import (
    get_tax_contract_document,
    list_tax_audit_issues_by_contract,
    list_audit_trace_by_contract,
    list_contract_clauses,
    list_tax_rules,
)
from app.services.tax_contract_parser import detect_text_language

logger = logging.getLogger("law_assistant")


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def _risk_summary(issues: list[dict]) -> dict:
    high = len([x for x in issues if str(x.get("risk_level") or "") == "high"])
    medium = len([x for x in issues if str(
        x.get("risk_level") or "") == "medium"])
    low = len([x for x in issues if str(x.get("risk_level") or "") == "low"])
    return {
        "total": len(issues),
        "high": high,
        "medium": medium,
        "low": low,
    }


def _review_summary(issues: list[dict]) -> dict:
    confirmed = len([x for x in issues if str(
        x.get("reviewer_status") or "") == "confirmed"])
    rejected = len([x for x in issues if str(
        x.get("reviewer_status") or "") == "rejected"])
    downgraded = len([x for x in issues if str(
        x.get("reviewer_status") or "") == "downgraded"])
    exception = len([x for x in issues if str(
        x.get("reviewer_status") or "") == "exception"])
    pending = len([x for x in issues if str(
        x.get("reviewer_status") or "") == "pending"])
    return {
        "confirmed": confirmed,
        "rejected": rejected,
        "downgraded": downgraded,
        "exception": exception,
        "pending": pending,
    }


def _detect_contract_language(contract: dict, clauses: list[dict]) -> str:
    source = "\n".join([str(x.get("clause_text") or "")
                       for x in clauses[:300]])
    if not source:
        source = str(contract.get("original_filename") or "")
    lang = detect_text_language(source, default="zh")
    return "en" if lang == "en" else "zh"


def build_tax_audit_report(cfg, contract_id: str) -> dict:
    logger.info("tax_report_build_start contract_id=%s", contract_id)
    contract = get_tax_contract_document(cfg, contract_id)
    if not contract:
        raise ValueError("contract document not found")
    issues = list_tax_audit_issues_by_contract(cfg, contract_id)
    clauses = list_contract_clauses(cfg, contract_id, limit=5000)
    traces = list_audit_trace_by_contract(cfg, contract_id, limit=5000)
    rules = list_tax_rules(cfg, limit=5000)
    language = _detect_contract_language(contract, clauses)
    clause_map = {str(x.get("id") or ""): x for x in clauses}
    rule_map = {str(x.get("id") or ""): x for x in rules}
    risk_items = []
    evidence_items = []
    exception_items = []
    for issue in issues:
        clause = clause_map.get(str(issue.get("clause_id") or ""), {})
        rule = rule_map.get(str(issue.get("rule_id") or ""), {})
        item = {
            "issue_id": issue.get("id"),
            "risk_level": issue.get("risk_level"),
            "issue_text": issue.get("issue_text"),
            "suggestion": issue.get("suggestion"),
            "reviewer_status": issue.get("reviewer_status"),
            "reviewer_note": issue.get("reviewer_note"),
            "clause": {
                "clause_id": clause.get("id"),
                "clause_path": clause.get("clause_path"),
                "page_no": clause.get("page_no"),
                "paragraph_no": clause.get("paragraph_no"),
                "clause_text": clause.get("clause_text"),
            },
            "rule": {
                "rule_id": rule.get("id"),
                "law_title": rule.get("law_title"),
                "article_no": rule.get("article_no"),
                "rule_type": rule.get("rule_type"),
                "source_page": rule.get("source_page"),
                "source_paragraph": rule.get("source_paragraph"),
                "source_text": rule.get("source_text"),
            },
        }
        risk_items.append(item)
        evidence_items.append(
            {
                "issue_id": issue.get("id"),
                "rule_id": rule.get("id"),
                "law_title": rule.get("law_title"),
                "article_no": rule.get("article_no"),
                "source_page": rule.get("source_page"),
                "source_paragraph": rule.get("source_paragraph"),
                "source_text": rule.get("source_text"),
                "clause_id": clause.get("id"),
                "clause_path": clause.get("clause_path"),
                "clause_page_no": clause.get("page_no"),
            }
        )
        if str(issue.get("reviewer_status") or "") == "exception":
            exception_items.append(item)
    report = {
        "contract_id": contract_id,
        "language": language,
        "generated_at": _utc_now_iso(),
        "overview": {
            "contract_filename": contract.get("original_filename"),
            "contract_parse_status": contract.get("parse_status"),
            "ocr_used": bool(contract.get("ocr_used")),
            "clause_count": len(clauses),
            "issue_count": len(issues),
            "trace_count": len(traces),
        },
        "risk_summary": _risk_summary(issues),
        "review_summary": _review_summary(issues),
        "risk_items": risk_items,
        "evidence_items": evidence_items,
        "review_conclusions": traces,
        "exception_items": exception_items,
    }
    logger.info(
        "tax_report_build_done contract_id=%s issues=%s traces=%s clauses=%s rules=%s exceptions=%s",
        contract_id,
        len(issues),
        len(traces),
        len(clauses),
        len(rules),
        len(exception_items),
    )
    return report


def export_tax_audit_report(
    cfg,
    contract_id: str,
    export_format: str = "json",
    template_version: str = "v1.0",
    locale: str = "zh-CN",
    brand: str = "",
) -> dict:
    fmt = str(export_format or "json").lower()
    if fmt not in {"json", "docx"}:
        raise ValueError("only json/docx export is supported")
    logger.info("tax_report_export_start contract_id=%s format=%s",
                contract_id, fmt)
    report = build_tax_audit_report(cfg, contract_id)
    report_dir = os.path.join(cfg["files_dir"], "tax_audit_reports")
    os.makedirs(report_dir, exist_ok=True)
    ext = "json" if fmt == "json" else "docx"
    filename = f"tax_audit_report_{contract_id}.{ext}"
    file_path = os.path.join(report_dir, filename)
    if fmt == "json":
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    else:
        render_tax_audit_docx(
            report, file_path, template_version=template_version, locale=locale, brand=brand)
    result = {
        "contract_id": contract_id,
        "export_format": fmt,
        "file_path": file_path,
        "file_name": filename,
        "size": os.path.getsize(file_path),
        "generated_at": report.get("generated_at"),
        "template_version": template_version,
        "locale": locale,
        "brand": brand,
    }
    logger.info(
        "tax_report_export_done contract_id=%s file=%s size=%s",
        contract_id,
        file_path,
        result["size"],
    )
    return result
