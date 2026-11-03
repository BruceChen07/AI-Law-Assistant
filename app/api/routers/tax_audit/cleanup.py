"""Tax audit cleanup jobs and archive endpoints."""
from __future__ import annotations

import json

from app.api.dependencies import get_current_user
from app.api.schemas import TaxAuditArchiveRecordListResponse, TaxAuditCleanupJobListResponse, TaxAuditCleanupRunRequest, TaxAuditCleanupRunResponse
from app.services.crud import list_tax_archive_records, list_tax_cleanup_jobs
from app.services.tax_lifecycle import retry_tax_cleanup, run_tax_cleanup
from fastapi import APIRouter, Depends, HTTPException

from app.api.routers.tax_audit._cfg import tax_cfg

router = APIRouter()

@router.post("/tax-audit/cleanup/run", response_model=TaxAuditCleanupRunResponse)
def run_tax_audit_cleanup(
    payload: TaxAuditCleanupRunRequest,
    current_user: dict = Depends(get_current_user),
    cfg: dict = Depends(tax_cfg),
):
    try:
        result = run_tax_cleanup(
            cfg,
            operator_id=current_user["id"],
            retention_days=payload.retention_days or 30,
        )
        return TaxAuditCleanupRunResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/tax-audit/cleanup/jobs/{job_id}/retry", response_model=TaxAuditCleanupRunResponse)
def retry_tax_audit_cleanup_job(job_id: str, current_user: dict = Depends(get_current_user),
    cfg: dict = Depends(tax_cfg),
):
    try:
        result = retry_tax_cleanup(
            cfg, job_id=job_id, operator_id=current_user["id"])
        return TaxAuditCleanupRunResponse(**result)
    except ValueError as e:
        msg = str(e)
        if "not found" in msg:
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)


@router.get("/tax-audit/cleanup/jobs", response_model=TaxAuditCleanupJobListResponse)
def list_tax_audit_cleanup_jobs(current_user: dict = Depends(get_current_user),
    cfg: dict = Depends(tax_cfg),
):
    _ = current_user
    rows = list_tax_cleanup_jobs(cfg, limit=100)
    for row in rows:
        try:
            parsed = json.loads(row.get("details_json") or "{}")
            row["details_json"] = json.dumps(parsed, ensure_ascii=False)
        except Exception:
            row["details_json"] = row.get("details_json") or "{}"
    return TaxAuditCleanupJobListResponse(total=len(rows), items=rows)


@router.get("/tax-audit/archive/records", response_model=TaxAuditArchiveRecordListResponse)
def list_tax_audit_archive(current_user: dict = Depends(get_current_user),
    cfg: dict = Depends(tax_cfg),
):
    _ = current_user
    rows = list_tax_archive_records(cfg, limit=200)
    return TaxAuditArchiveRecordListResponse(total=len(rows), items=rows)
