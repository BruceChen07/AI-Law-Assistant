"""Contract report export endpoint."""
from __future__ import annotations

import json
import os
import re

from app.api.dependencies import get_current_user
from app.services.crud import get_document_by_id_for_user, get_latest_contract_audit_by_document, get_latest_successful_document_digitization_by_document
from app.services.docx_renderer import render_tax_audit_docx
from datetime import datetime, timezone
from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse
from typing import Any, Dict

from app.api.routers.contracts._cfg import contracts_cfg

router = APIRouter()


def _build_readable_export_filename(original_filename: str, ts, ext: str) -> str:
    """Build a human-friendly export filename: contract name + timestamp."""
    base = os.path.splitext(str(original_filename or ""))[0].strip()
    base = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", base).strip(" ._")
    if not base:
        base = "contract"
    return f"{base}_{ts.strftime('%Y%m%d_%H%M%S')}.{ext}"


@router.post("/contracts/{document_id}/report/export")
def export_contract_report(
    document_id: str,
    payload: Dict[str, Any] = Body(default={}),
    current_user: dict = Depends(get_current_user),
    cfg: dict = Depends(contracts_cfg),
):
    doc = get_document_by_id_for_user(cfg, document_id, current_user["id"])
    if not doc:
        raise HTTPException(status_code=404, detail="document not found")
    contract_name = str(doc.get("original_filename")
                        or doc.get("filename") or "")
    audit_row = get_latest_contract_audit_by_document(
        cfg, document_id, status="done")
    if not audit_row:
        raise HTTPException(
            status_code=404, detail="contract audit result not found")
    try:
        audit = json.loads(str(audit_row.get("result_json") or "{}"))
    except Exception:
        audit = {}
    if not isinstance(audit, dict):
        audit = {}
    fmt = str((payload or {}).get("export_format") or "json").lower()
    if fmt not in {"json", "docx"}:
        raise HTTPException(
            status_code=400, detail="only json/docx export is supported")
    template_version = str((payload or {}).get(
        "template_version") or "v1.0")
    locale = str((payload or {}).get("locale") or "zh-CN")
    brand = str((payload or {}).get("brand") or "")
    risks = audit.get("risks") if isinstance(
        audit.get("risks"), list) else []
    citations = audit.get("citations") if isinstance(
        audit.get("citations"), list) else []
    citation_map = {str(c.get("citation_id") or "")
                        : c for c in citations if isinstance(c, dict)}
    risk_summary = {"high": 0, "medium": 0, "low": 0}
    risk_items = []
    evidence_items = []
    for idx, r in enumerate(risks, start=1):
        if not isinstance(r, dict):
            continue
        level = str(r.get("level") or "medium").lower()
        level = level if level in {"high", "medium", "low"} else "medium"
        risk_summary[level] += 1
        location = r.get("location") if isinstance(
            r.get("location"), dict) else {}
        issue_id = f"r{idx}"
        citation_id = str(r.get("citation_id") or "")
        citation = citation_map.get(citation_id, {})
        issue_text = str(r.get("issue") or "")
        suggestion = str(r.get("suggestion") or "")
        evidence_text = str(r.get("evidence") or "")
        risk_items.append({
            "issue_id": issue_id,
            "risk_level": level,
            "issue_text": issue_text,
            "suggestion": suggestion,
            "reviewer_status": "pending",
            "reviewer_note": "",
            "clause": {
                "clause_id": str(location.get("clause_id") or ""),
                "clause_path": str(location.get("clause_path") or ""),
                "clause_text": str(location.get("quote") or ""),
                "page_no": int(location.get("page_no") or 0),
                "paragraph_no": str(location.get("paragraph_no") or ""),
            },
            "rule": {
                "rule_id": citation_id,
                "law_title": str(citation.get("law_title") or r.get("law_title") or ""),
                "article_no": str(citation.get("article_no") or r.get("article_no") or ""),
                "source_text": str(citation.get("content") or citation.get("excerpt") or ""),
            },
        })
        if evidence_text:
            evidence_items.append({
                "issue_id": issue_id,
                "law_title": str(citation.get("law_title") or r.get("law_title") or ""),
                "article_no": str(citation.get("article_no") or r.get("article_no") or ""),
                "source_text": evidence_text,
                "source_page": int(location.get("page_no") or 0),
                "source_paragraph": str(location.get("paragraph_no") or ""),
                "clause_id": str(location.get("clause_id") or ""),
                "clause_path": str(location.get("clause_path") or ""),
            })
    report = {
        "contract_id": document_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overview": {
            "contract_filename": doc.get("original_filename") or doc.get("filename") or "",
            "contract_parse_status": "done",
            "clause_count": 0,
            "issue_count": len(risk_items),
            "trace_count": 0,
        },
        "risk_summary": risk_summary,
        "review_summary": {"pending": len(risk_items), "confirmed": 0, "exception": 0},
        "risk_items": risk_items,
        "evidence_items": evidence_items,
        "review_conclusions": [],
        "exception_items": [],
    }
    report_dir = os.path.join(cfg["files_dir"], "contract_reports")
    os.makedirs(report_dir, exist_ok=True)
    ext = "json" if fmt == "json" else "docx"
    filename = _build_readable_export_filename(
        contract_name, datetime.now(), ext)
    output_path = os.path.join(report_dir, filename)
    if fmt == "json":
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        media_type = "application/json"
    else:
        export_mode = str((payload or {}).get(
            "export_mode") or "report").lower()
        digitization_record = get_latest_successful_document_digitization_by_document(
            cfg, document_id
        )
        if export_mode == "digitized_source":
            digitized_path = str(
                (digitization_record or {}).get(
                    "digitized_docx_path") or ""
            )
            if not digitized_path or not os.path.exists(digitized_path):
                raise HTTPException(
                    status_code=404,
                    detail="digitized word document not found",
                )
            return FileResponse(
                digitized_path,
                media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                filename=_build_readable_export_filename(
                    contract_name, datetime.now(), "docx"),
            )
        if export_mode == "comments":
            # Export original contract with comments (M2)
            from app.services.docx_modifier import insert_risk_comments
            original_file = str(doc.get("file_path") or "")
            if not original_file or not os.path.exists(original_file):
                raise HTTPException(
                    status_code=404, detail="original contract file not found")
            comment_source = original_file
            source_ext = str(os.path.splitext(
                original_file)[1] or "").lower()
            if source_ext != ".docx":
                digitized_path = str(
                    (digitization_record or {}).get(
                        "digitized_docx_path") or ""
                )
                if not digitized_path or not os.path.exists(digitized_path):
                    raise HTTPException(
                        status_code=404,
                        detail="digitized word document not found",
                    )
                comment_source = digitized_path
            insert_risk_comments(comment_source, output_path, risk_items)
            filename = _build_readable_export_filename(
                contract_name, datetime.now(), "docx",
            )
        else:
            render_tax_audit_docx(
                report, output_path, template_version=template_version, locale=locale, brand=brand)
            filename = _build_readable_export_filename(
                contract_name, datetime.now(), "docx")

        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return FileResponse(output_path, media_type=media_type, filename=filename)
