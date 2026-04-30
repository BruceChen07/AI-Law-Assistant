import os
import json
import uuid
import hashlib
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from fastapi.responses import FileResponse
from app.api.dependencies import get_current_user, get_app_llm, get_app_embedder
from app.api.schemas import (
    TaxAuditRegulationImportResponse,
    TaxAuditRegulationParseResponse,
    TaxAuditContractImportResponse,
    TaxAuditContractAnalyzeResponse,
    TaxAuditClauseListResponse,
    TaxAuditMatchRunResponse,
    TaxAuditMatchListResponse,
    TaxAuditIssueGenerateResponse,
    TaxAuditIssueReviewRequest,
    TaxAuditIssueReviewResponse,
    TaxAuditTraceListResponse,
    TaxAuditReportResponse,
    TaxAuditReportExportRequest,
    TaxAuditReportExportResponse,
    TaxAuditExportJobStatusResponse,
    TaxAuditIssueListResponse,
    TaxAuditCleanupRunRequest,
    TaxAuditCleanupRunResponse,
    TaxAuditCleanupJobListResponse,
    TaxAuditArchiveRecordListResponse,
    TaxAuditPipelineRunResponse,
)
from app.services.crud import (
    create_tax_regulation_document,
    create_tax_contract_document,
    list_clause_rule_matches_by_contract,
    list_audit_trace_by_issue,
    list_audit_trace_by_contract,
    list_tax_audit_issues_by_contract,
    list_tax_cleanup_jobs,
    list_tax_archive_records,
)
from app.services.tax_parser import parse_regulation_document
from app.services.tax_risk import review_audit_issue
from app.services.audit_orchestrator import (
    AuditServices,
    tax_analyze_contract,
    tax_match_contract,
    tax_generate_issues,
    tax_build_report,
    resolve_tax_contract_language,
    run_tax_pipeline_bundle,
)
from app.services.tax_lifecycle import run_tax_cleanup, retry_tax_cleanup
from app.services.export_jobs import submit_tax_report_export_job, get_tax_report_export_job


ALLOWED_TAX_AUDIT_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".png",
    ".jpg",
    ".jpeg",
    ".tif",
    ".tiff",
}


def _validate_extension(filename: str):
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in ALLOWED_TAX_AUDIT_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="unsupported file type, only pdf/doc/docx/xls/xlsx/images are allowed",
        )
    return ext


async def _save_upload(cfg: dict, file: UploadFile, prefix: str):
    ext = _validate_extension(file.filename)
    file_id = str(uuid.uuid4())
    cache_dir = os.path.join(cfg.get("data_dir", "./data"), "uploads", "cache")
    os.makedirs(cache_dir, exist_ok=True)
    save_path = os.path.join(cache_dir, f"{prefix}_{file_id}{ext}")
    content = await file.read()
    with open(save_path, "wb") as f:
        f.write(content)
    digest = hashlib.md5(content).hexdigest()

    # Also insert into new upload_log table
    from app.core.database import get_conn
    with get_conn(cfg) as conn:
        conn.execute(
            "INSERT INTO upload_log (file_id, original_filename, md5_hash, file_path, created_at, updated_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (file_id, file.filename, digest, save_path)
        )
        conn.commit()

    return file_id, save_path, len(content), ext, digest


def build_router(cfg):
    router = APIRouter()

    @router.post("/tax-audit/regulations/import", response_model=TaxAuditRegulationImportResponse)
    async def import_tax_regulation(
        file: UploadFile = File(...),
        current_user: dict = Depends(get_current_user),
    ):
        doc_id, save_path, file_size, ext, digest = await _save_upload(cfg, file, "tax_reg")
        create_tax_regulation_document(
            cfg=cfg,
            document_id=doc_id,
            original_filename=file.filename,
            file_path=save_path,
            file_type=ext.lstrip("."),
            file_size=file_size,
            uploaded_by=current_user["id"],
            checksum=digest,
            parse_status="pending",
        )
        return TaxAuditRegulationImportResponse(document_id=doc_id, parse_status="pending")

    @router.post("/tax-audit/contracts/import", response_model=TaxAuditContractImportResponse)
    async def import_tax_contract(
        file: UploadFile = File(...),
        current_user: dict = Depends(get_current_user),
    ):
        contract_id, save_path, file_size, ext, _ = await _save_upload(cfg, file, "tax_contract")
        create_tax_contract_document(
            cfg=cfg,
            document_id=contract_id,
            original_filename=file.filename,
            file_path=save_path,
            file_type=ext.lstrip("."),
            file_size=file_size,
            uploaded_by=current_user["id"],
            parse_status="pending",
            ocr_used=0,
        )
        return TaxAuditContractImportResponse(contract_id=contract_id, parse_status="pending")

    @router.post("/tax-audit/regulations/{document_id}/parse", response_model=TaxAuditRegulationParseResponse)
    def parse_tax_regulation(document_id: str, current_user: dict = Depends(get_current_user)):
        try:
            parsed = parse_regulation_document(
                cfg,
                document_id=document_id,
                operator_id=current_user["id"],
            )
            return TaxAuditRegulationParseResponse(**parsed)
        except ValueError as e:
            msg = str(e)
            if "not found" in msg:
                raise HTTPException(status_code=404, detail=msg)
            raise HTTPException(status_code=400, detail=msg)

    @router.post("/tax-audit/contracts/{contract_id}/analyze", response_model=TaxAuditContractAnalyzeResponse)
    def analyze_tax_contract(
        contract_id: str,
        current_user: dict = Depends(get_current_user),
        llm=Depends(get_app_llm),
    ):
        try:
            services = AuditServices(llm=llm)
            analyzed = tax_analyze_contract(
                cfg,
                services,
                contract_id=contract_id,
                operator_id=current_user["id"],
            )
            return TaxAuditContractAnalyzeResponse(**analyzed)
        except ValueError as e:
            msg = str(e)
            if "not found" in msg:
                raise HTTPException(status_code=404, detail=msg)
            raise HTTPException(status_code=400, detail=msg)

    @router.get("/tax-audit/contracts/{contract_id}/clauses", response_model=TaxAuditClauseListResponse)
    def list_tax_contract_clauses(contract_id: str, current_user: dict = Depends(get_current_user)):
        _ = current_user
        from app.services.crud import list_contract_clauses
        items = list_contract_clauses(cfg, contract_id)
        language = resolve_tax_contract_language(cfg, contract_id=contract_id)
        return TaxAuditClauseListResponse(contract_id=contract_id, language=language, total=len(items), items=items)

    @router.post("/tax-audit/contracts/{contract_id}/match", response_model=TaxAuditMatchRunResponse)
    def run_tax_contract_match(
        contract_id: str,
        current_user: dict = Depends(get_current_user),
        llm=Depends(get_app_llm),
        embedder=Depends(get_app_embedder),
    ):
        try:
            services = AuditServices(llm=llm, embedder=embedder)
            result = tax_match_contract(
                cfg,
                services,
                contract_id=contract_id,
                operator_id=current_user["id"],
            )
            return TaxAuditMatchRunResponse(**result)
        except ValueError as e:
            msg = str(e)
            if "not found" in msg:
                raise HTTPException(status_code=404, detail=msg)
            raise HTTPException(status_code=400, detail=msg)

    @router.get("/tax-audit/contracts/{contract_id}/matches", response_model=TaxAuditMatchListResponse)
    def list_tax_contract_matches(contract_id: str, current_user: dict = Depends(get_current_user)):
        _ = current_user
        items = list_clause_rule_matches_by_contract(cfg, contract_id)
        language = resolve_tax_contract_language(cfg, contract_id=contract_id)
        return TaxAuditMatchListResponse(contract_id=contract_id, language=language, total=len(items), items=items)

    @router.post("/tax-audit/contracts/{contract_id}/issues/generate", response_model=TaxAuditIssueGenerateResponse)
    def generate_tax_audit_issues(
        contract_id: str,
        current_user: dict = Depends(get_current_user),
        llm=Depends(get_app_llm),
    ):
        try:
            services = AuditServices(llm=llm)
            result = tax_generate_issues(
                cfg, services, contract_id=contract_id, operator_id=current_user["id"])
            return TaxAuditIssueGenerateResponse(**result)
        except ValueError as e:
            msg = str(e)
            if "not found" in msg:
                raise HTTPException(status_code=404, detail=msg)
            raise HTTPException(status_code=400, detail=msg)

    @router.get("/tax-audit/contracts/{contract_id}/issues", response_model=TaxAuditIssueListResponse)
    def list_tax_contract_issues(contract_id: str, current_user: dict = Depends(get_current_user)):
        _ = current_user
        items = list_tax_audit_issues_by_contract(cfg, contract_id)
        language = resolve_tax_contract_language(cfg, contract_id=contract_id)
        return TaxAuditIssueListResponse(
            contract_id=contract_id,
            language=language,
            total=len(items),
            items=items,
        )

    @router.post("/tax-audit/contracts/{contract_id}/pipeline/run", response_model=TaxAuditPipelineRunResponse)
    def run_tax_pipeline(
        contract_id: str,
        current_user: dict = Depends(get_current_user),
        llm=Depends(get_app_llm),
        embedder=Depends(get_app_embedder),
    ):
        try:
            services = AuditServices(llm=llm, embedder=embedder)
            result = run_tax_pipeline_bundle(
                cfg,
                services,
                contract_id=contract_id,
                operator_id=current_user["id"],
            )
            return TaxAuditPipelineRunResponse(**result)
        except ValueError as e:
            msg = str(e)
            if "not found" in msg:
                raise HTTPException(status_code=404, detail=msg)
            raise HTTPException(status_code=400, detail=msg)

    @router.post("/tax-audit/issues/{issue_id}/review", response_model=TaxAuditIssueReviewResponse)
    def review_tax_issue(issue_id: str, payload: TaxAuditIssueReviewRequest, current_user: dict = Depends(get_current_user)):
        try:
            result = review_audit_issue(
                cfg,
                issue_id=issue_id,
                reviewer_status=payload.reviewer_status,
                reviewer_note=payload.reviewer_note or "",
                risk_level=payload.risk_level or "",
                operator_id=current_user["id"],
            )
            return TaxAuditIssueReviewResponse(**result)
        except ValueError as e:
            msg = str(e)
            if "not found" in msg:
                raise HTTPException(status_code=404, detail=msg)
            raise HTTPException(status_code=400, detail=msg)

    @router.get("/tax-audit/issues/{issue_id}/trace", response_model=TaxAuditTraceListResponse)
    def list_tax_issue_trace(issue_id: str, current_user: dict = Depends(get_current_user)):
        _ = current_user
        items = list_audit_trace_by_issue(cfg, issue_id)
        return TaxAuditTraceListResponse(issue_id=issue_id, total=len(items), items=items)

    @router.get("/tax-audit/contracts/{contract_id}/trace", response_model=TaxAuditTraceListResponse)
    def list_tax_contract_trace(contract_id: str, current_user: dict = Depends(get_current_user)):
        _ = current_user
        items = list_audit_trace_by_contract(cfg, contract_id)
        return TaxAuditTraceListResponse(contract_id=contract_id, total=len(items), items=items)

    @router.get("/tax-audit/contracts/{contract_id}/report", response_model=TaxAuditReportResponse)
    def get_tax_contract_report(contract_id: str, current_user: dict = Depends(get_current_user)):
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
    def generate_tax_contract_report(contract_id: str, current_user: dict = Depends(get_current_user)):
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
    def get_tax_report_export_status(export_id: str, current_user: dict = Depends(get_current_user)):
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
    def download_tax_report_export(export_id: str, current_user: dict = Depends(get_current_user)):
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

    @router.post("/tax-audit/cleanup/run", response_model=TaxAuditCleanupRunResponse)
    def run_tax_audit_cleanup(
        payload: TaxAuditCleanupRunRequest,
        current_user: dict = Depends(get_current_user),
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
    def retry_tax_audit_cleanup_job(job_id: str, current_user: dict = Depends(get_current_user)):
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
    def list_tax_audit_cleanup_jobs(current_user: dict = Depends(get_current_user)):
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
    def list_tax_audit_archive(current_user: dict = Depends(get_current_user)):
        _ = current_user
        rows = list_tax_archive_records(cfg, limit=200)
        return TaxAuditArchiveRecordListResponse(total=len(rows), items=rows)

    return router
