"""Tax audit report build / export / download endpoints."""
from __future__ import annotations

import os

from app.api.dependencies import get_current_user
from app.api.schemas import TaxAuditExportJobStatusResponse, TaxAuditReportExportRequest, TaxAuditReportExportResponse, TaxAuditReportResponse
from app.services.audit_orchestrator import tax_build_report
from app.services.export_jobs import get_tax_report_export_job, submit_tax_report_export_job
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.api.routers.tax_audit._cfg import tax_cfg

router = APIRouter()

@router.get("/tax-audit/contracts/{contract_id}/report", response_model=TaxAuditReportResponse)
def get_tax_contract_report(contract_id: str, current_user: dict = Depends(get_current_user),
    cfg: dict = Depends(tax_cfg),
):
    _ = current_user
    try:
        report = tax_build_report(cfg, contract_id=contract_id)
        return TaxAuditReportResponse(**report)
    except ValueError as e:
        msg = str(e)
        if "not found" in msg:
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)


@router.post("/tax-audit/contracts/{contract_id}/report", response_model=TaxAuditReportResponse)
def generate_tax_contract_report(contract_id: str, current_user: dict = Depends(get_current_user),
    cfg: dict = Depends(tax_cfg),
):
    _ = current_user
    try:
        report = tax_build_report(cfg, contract_id=contract_id)
        return TaxAuditReportResponse(**report)
    except ValueError as e:
        msg = str(e)
        if "not found" in msg:
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)


@router.post("/tax-audit/contracts/{contract_id}/report/export", response_model=TaxAuditReportExportResponse)
def export_tax_contract_report(
    contract_id: str,
    payload: TaxAuditReportExportRequest,
    current_user: dict = Depends(get_current_user),
    cfg: dict = Depends(tax_cfg),
):
    _ = current_user
    try:
        job = submit_tax_report_export_job(
            cfg,
            contract_id=contract_id,
            export_format=payload.export_format or "json",
            template_version=payload.template_version or "v1.0",
            locale=payload.locale or "zh-CN",
            include_appendix=bool(payload.include_appendix),
            brand=payload.brand or "",
            requester=current_user["id"],
        )
        output_path = str(job.get("output_path") or "")
        size = os.path.getsize(
            output_path) if output_path and os.path.exists(output_path) else 0
        return TaxAuditReportExportResponse(
            export_id=str(job.get("export_id") or ""),
            status=str(job.get("status") or "queued"),
            progress=int(job.get("progress") or 0),
            contract_id=contract_id,
            export_format=str(job.get("export_format") or (
                payload.export_format or "json")),
            file_path=output_path,
            file_name=os.path.basename(output_path) if output_path else "",
            size=size,
            generated_at=str(job.get("finished_at") or job.get(
                "updated_at") or job.get("created_at") or ""),
            template_version=str(job.get("template_version") or (
                payload.template_version or "v1.0")),
            locale=str(job.get("locale") or (payload.locale or "zh-CN")),
            brand=payload.brand or "",
        )
    except ValueError as e:
        msg = str(e)
        if "not found" in msg:
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)


@router.get("/tax-audit/exports/{export_id}", response_model=TaxAuditExportJobStatusResponse)
def get_tax_report_export_status(export_id: str, current_user: dict = Depends(get_current_user),
    cfg: dict = Depends(tax_cfg),
):
    _ = current_user
    row = get_tax_report_export_job(cfg, export_id)
    if not row:
        raise HTTPException(status_code=404, detail="export job not found")
    return TaxAuditExportJobStatusResponse(
        export_id=str(row.get("export_id") or ""),
        contract_id=str(row.get("contract_document_id") or ""),
        status=str(row.get("status") or "queued"),
        progress=int(row.get("progress") or 0),
        export_format=str(row.get("export_format") or "json"),
        template_version=str(row.get("template_version") or "v1.0"),
        locale=str(row.get("locale") or "zh-CN"),
        include_appendix=bool(row.get("include_appendix")),
        file_path=str(row.get("output_path") or ""),
        error_message=str(row.get("error_message") or ""),
        created_at=str(row.get("created_at") or ""),
        started_at=str(row.get("started_at") or ""),
        finished_at=str(row.get("finished_at") or ""),
    )


@router.get("/tax-audit/exports/{export_id}/download")
def download_tax_report_export(export_id: str, current_user: dict = Depends(get_current_user),
    cfg: dict = Depends(tax_cfg),
):
    _ = current_user
    row = get_tax_report_export_job(cfg, export_id)
    if not row:
        raise HTTPException(status_code=404, detail="export job not found")
    if str(row.get("status") or "") != "done":
        raise HTTPException(
            status_code=400, detail="export job is not completed")
    output_path = str(row.get("output_path") or "")
    if not output_path or not os.path.exists(output_path):
        raise HTTPException(
            status_code=404, detail="export file not found")
    filename = os.path.basename(output_path)
    media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if filename.lower().endswith(".json"):
        media_type = "application/json"
    return FileResponse(output_path, media_type=media_type, filename=filename)
