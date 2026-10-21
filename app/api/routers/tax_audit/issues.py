"""Tax audit issue generation, listing, review and trace endpoints."""
from __future__ import annotations

from app.api.dependencies import get_app_llm, get_current_user
from app.api.schemas import TaxAuditIssueGenerateResponse, TaxAuditIssueListResponse, TaxAuditIssueReviewRequest, TaxAuditIssueReviewResponse, TaxAuditTraceListResponse
from app.services.audit_orchestrator import AuditServices, resolve_tax_contract_language, tax_generate_issues
from app.services.crud import list_audit_trace_by_issue, list_tax_audit_issues_by_contract
from app.services.tax_risk import review_audit_issue
from fastapi import APIRouter, Depends, HTTPException

from app.api.routers.tax_audit._cfg import tax_cfg

router = APIRouter()

@router.post("/tax-audit/contracts/{contract_id}/issues/generate", response_model=TaxAuditIssueGenerateResponse)
def generate_tax_audit_issues(
    contract_id: str,
    current_user: dict = Depends(get_current_user),
    llm=Depends(get_app_llm),
    cfg: dict = Depends(tax_cfg),
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
def list_tax_contract_issues(contract_id: str, current_user: dict = Depends(get_current_user),
    cfg: dict = Depends(tax_cfg),
):
    _ = current_user
    items = list_tax_audit_issues_by_contract(cfg, contract_id)
    language = resolve_tax_contract_language(cfg, contract_id=contract_id)
    return TaxAuditIssueListResponse(
        contract_id=contract_id,
        language=language,
        total=len(items),
        items=items,
    )


@router.post("/tax-audit/issues/{issue_id}/review", response_model=TaxAuditIssueReviewResponse)
def review_tax_issue(issue_id: str, payload: TaxAuditIssueReviewRequest, current_user: dict = Depends(get_current_user),
    cfg: dict = Depends(tax_cfg),
):
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
def list_tax_issue_trace(issue_id: str, current_user: dict = Depends(get_current_user),
    cfg: dict = Depends(tax_cfg),
):
    _ = current_user
    items = list_audit_trace_by_issue(cfg, issue_id)
    return TaxAuditTraceListResponse(issue_id=issue_id, total=len(items), items=items)
