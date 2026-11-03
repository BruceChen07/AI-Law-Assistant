"""Tax contract import, analyze, match, pipeline and trace endpoints."""
from __future__ import annotations

from app.api.dependencies import get_app_embedder, get_app_llm, get_current_user
from app.api.schemas import TaxAuditClauseListResponse, TaxAuditContractAnalyzeResponse, TaxAuditContractImportResponse, TaxAuditMatchListResponse, TaxAuditMatchRunResponse, TaxAuditPipelineRunResponse, TaxAuditTraceListResponse
from app.services.audit_orchestrator import AuditServices, resolve_tax_contract_language, run_tax_pipeline_bundle, tax_analyze_contract, tax_match_contract
from app.services.crud import create_tax_contract_document, list_audit_trace_by_contract, list_clause_rule_matches_by_contract
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api.routers.tax_audit._cfg import tax_cfg
from app.api.routers.tax_audit._shared import (
    _save_upload,
)

router = APIRouter()

@router.post("/tax-audit/contracts/import", response_model=TaxAuditContractImportResponse)
async def import_tax_contract(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
    cfg: dict = Depends(tax_cfg),
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


@router.post("/tax-audit/contracts/{contract_id}/analyze", response_model=TaxAuditContractAnalyzeResponse)
def analyze_tax_contract(
    contract_id: str,
    current_user: dict = Depends(get_current_user),
    llm=Depends(get_app_llm),
    cfg: dict = Depends(tax_cfg),
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
def list_tax_contract_clauses(contract_id: str, current_user: dict = Depends(get_current_user),
    cfg: dict = Depends(tax_cfg),
):
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
    cfg: dict = Depends(tax_cfg),
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
def list_tax_contract_matches(contract_id: str, current_user: dict = Depends(get_current_user),
    cfg: dict = Depends(tax_cfg),
):
    _ = current_user
    items = list_clause_rule_matches_by_contract(cfg, contract_id)
    language = resolve_tax_contract_language(cfg, contract_id=contract_id)
    return TaxAuditMatchListResponse(contract_id=contract_id, language=language, total=len(items), items=items)


@router.post("/tax-audit/contracts/{contract_id}/pipeline/run", response_model=TaxAuditPipelineRunResponse)
def run_tax_pipeline(
    contract_id: str,
    current_user: dict = Depends(get_current_user),
    llm=Depends(get_app_llm),
    embedder=Depends(get_app_embedder),
    cfg: dict = Depends(tax_cfg),
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


@router.get("/tax-audit/contracts/{contract_id}/trace", response_model=TaxAuditTraceListResponse)
def list_tax_contract_trace(contract_id: str, current_user: dict = Depends(get_current_user),
    cfg: dict = Depends(tax_cfg),
):
    _ = current_user
    items = list_audit_trace_by_contract(cfg, contract_id)
    return TaxAuditTraceListResponse(contract_id=contract_id, total=len(items), items=items)
