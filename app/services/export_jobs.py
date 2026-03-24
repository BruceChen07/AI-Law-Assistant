import json
import os
import uuid
import hashlib
from datetime import datetime, timezone
from app.services.tax_report import build_tax_audit_report, export_tax_audit_report
from app.services.crud import (
    create_export_job,
    get_export_job_by_export_id,
    get_export_job_by_idempotency_key,
    update_export_job_status,
    create_export_snapshot,
    create_evidence_anchor,
)


def _to_snapshot_hash(report: dict) -> str:
    encoded = json.dumps(report, ensure_ascii=False,
                         sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def _to_idempotency_key(contract_id: str, export_format: str, template_version: str, locale: str, include_appendix: bool, brand: str) -> str:
    raw = f"{contract_id}|{export_format}|{template_version}|{locale}|{int(bool(include_appendix))}|{brand}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def submit_tax_report_export_job(
    cfg,
    contract_id: str,
    export_format: str,
    template_version: str,
    locale: str,
    include_appendix: bool,
    brand: str,
    requester: str,
):
    fmt = str(export_format or "json").lower()
    if fmt not in {"json", "docx"}:
        raise ValueError("unsupported export format")
    idem_key = _to_idempotency_key(
        contract_id, fmt, template_version, locale, include_appendix, brand)
    existing = get_export_job_by_idempotency_key(cfg, idem_key)
    if existing and str(existing.get("status") or "") in {"queued", "processing", "done"}:
        return existing

    export_id = str(uuid.uuid4())
    job_id = create_export_job(
        cfg=cfg,
        export_id=export_id,
        contract_document_id=contract_id,
        requester=requester,
        export_format=fmt,
        template_version=template_version,
        locale=locale,
        include_appendix=include_appendix,
        idempotency_key=idem_key,
        created_by=requester,
    )
    try:
        update_export_job_status(
            cfg, export_id, status="processing", progress=10)
        report = build_tax_audit_report(cfg, contract_id)
        snapshot_hash = _to_snapshot_hash(report)
        create_export_snapshot(
            cfg=cfg,
            export_job_id=job_id,
            snapshot_hash=snapshot_hash,
            data_manifest_json=json.dumps(
                {
                    "contract_id": contract_id,
                    "template_version": template_version,
                    "locale": locale,
                    "include_appendix": bool(include_appendix),
                    "brand": brand,
                    "generated_at": _utc_now_iso(),
                    "report": report,
                },
                ensure_ascii=False,
            ),
            created_by=requester,
        )

        if include_appendix:
            for item in report.get("evidence_items") or []:
                text = str(item.get("source_text") or "")
                if not text:
                    continue
                issue_id = str(item.get("issue_id") or "")
                create_evidence_anchor(
                    cfg=cfg,
                    contract_document_id=contract_id,
                    issue_id=issue_id if issue_id else None,
                    snapshot_hash=snapshot_hash,
                    locator_type="paragraph_span",
                    quote_text=text,
                    created_by=requester,
                    page_no=item.get("source_page"),
                    paragraph_no=item.get("source_paragraph"),
                    clause_id=item.get("clause_id"),
                    clause_path=item.get("clause_path"),
                    confidence=0.9,
                )

        update_export_job_status(
            cfg, export_id, status="processing", progress=70)
        result = export_tax_audit_report(
            cfg,
            contract_id,
            export_format=fmt,
            template_version=template_version,
            locale=locale,
            brand=brand,
        )
        output_path = result.get("file_path") or ""
        output_sha256 = ""
        if output_path and os.path.exists(output_path):
            with open(output_path, "rb") as f:
                output_sha256 = hashlib.sha256(f.read()).hexdigest()
        update_export_job_status(
            cfg,
            export_id,
            status="done",
            progress=100,
            output_path=output_path,
            output_sha256=output_sha256,
        )
    except Exception as e:
        update_export_job_status(
            cfg, export_id, status="failed", progress=100, error_message=str(e))
        raise
    return get_export_job_by_export_id(cfg, export_id)


def get_tax_report_export_job(cfg, export_id: str):
    job = get_export_job_by_export_id(cfg, export_id)
    if not job:
        return None
    return job
