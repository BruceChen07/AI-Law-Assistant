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


from ._cfg import set_active_config
from . import cleanup, contracts, issues, regulations, reports  # noqa: F401

from .regulations import (  # noqa: F401
    import_tax_regulation,
    parse_tax_regulation,
)
from .contracts import (  # noqa: F401
    import_tax_contract,
    analyze_tax_contract,
    list_tax_contract_clauses,
    run_tax_contract_match,
    list_tax_contract_matches,
    run_tax_pipeline,
    list_tax_contract_trace,
)
from .issues import (  # noqa: F401
    generate_tax_audit_issues,
    list_tax_contract_issues,
    review_tax_issue,
    list_tax_issue_trace,
)
from .reports import (  # noqa: F401
    get_tax_contract_report,
    generate_tax_contract_report,
    export_tax_contract_report,
    get_tax_report_export_status,
    download_tax_report_export,
)
from .cleanup import (  # noqa: F401
    run_tax_audit_cleanup,
    retry_tax_audit_cleanup_job,
    list_tax_audit_cleanup_jobs,
    list_tax_audit_archive,
)
from ._shared import (  # noqa: F401
    ALLOWED_TAX_AUDIT_EXTENSIONS,
    _validate_extension,
    _save_upload,
)

def build_router(cfg):
    """Assemble the tax-audit routes.

    Handlers live in one submodule per resource. The factory signature is
    unchanged for app_factory.
    """
    set_active_config(cfg)
    router = APIRouter()
    router.include_router(regulations.router)
    router.include_router(contracts.router)
    router.include_router(issues.router)
    router.include_router(reports.router)
    router.include_router(cleanup.router)
    return router
