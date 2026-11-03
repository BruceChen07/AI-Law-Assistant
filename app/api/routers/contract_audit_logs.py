from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from app.api.dependencies import get_current_user
from app.services.contract_audit_stage_log import (
    export_contract_audit_stage_logs,
    query_contract_audit_stage_logs,
)


def build_router(cfg):
    router = APIRouter()

    @router.get("/contract-audit-logs")
    def list_contract_audit_logs(
        contract_name: str = Query(""),
        started_at_utc: str = Query(""),
        ended_at_utc: str = Query(""),
        stage: Optional[list[str]] = Query(default=None),
        audit_id: str = Query(""),
        document_id: str = Query(""),
        operator_id: str = Query(""),
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
        current_user: dict = Depends(get_current_user),
    ):
        effective_operator_id = str(current_user.get("id") or "")
        if str(current_user.get("role") or "").lower() == "admin" and operator_id:
            effective_operator_id = str(operator_id)
        result = query_contract_audit_stage_logs(
            cfg,
            operator_id=effective_operator_id,
            contract_name=contract_name,
            started_at_utc=started_at_utc,
            ended_at_utc=ended_at_utc,
            stage_filters=stage,
            audit_id=audit_id,
            document_id=document_id,
            limit=limit,
            offset=offset,
        )
        return {
            "items": result["items"],
            "total": result["total"],
            "limit": result["limit"],
            "offset": result["offset"],
            "filters": {
                "contract_name": contract_name,
                "started_at_utc": started_at_utc,
                "ended_at_utc": ended_at_utc,
                "stage": stage or [],
                "audit_id": audit_id,
                "document_id": document_id,
                "operator_id": effective_operator_id,
            },
        }

    @router.get("/contract-audit-logs/export")
    def export_contract_audit_logs(
        contract_name: str = Query(""),
        started_at_utc: str = Query(""),
        ended_at_utc: str = Query(""),
        stage: Optional[list[str]] = Query(default=None),
        audit_id: str = Query(""),
        document_id: str = Query(""),
        operator_id: str = Query(""),
        export_format: str = Query("jsonl"),
        current_user: dict = Depends(get_current_user),
    ):
        effective_operator_id = str(current_user.get("id") or "")
        if str(current_user.get("role") or "").lower() == "admin" and operator_id:
            effective_operator_id = str(operator_id)
        result = query_contract_audit_stage_logs(
            cfg,
            operator_id=effective_operator_id,
            contract_name=contract_name,
            started_at_utc=started_at_utc,
            ended_at_utc=ended_at_utc,
            stage_filters=stage,
            audit_id=audit_id,
            document_id=document_id,
            limit=5000,
            offset=0,
        )
        if not result["items"]:
            raise HTTPException(status_code=404, detail="no audit logs matched the export filters")
        try:
            exported = export_contract_audit_stage_logs(
                cfg,
                records=result["items"],
                export_format=export_format,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        media_type = "application/x-ndjson" if exported["format"] == "jsonl" else "text/csv"
        return FileResponse(
            exported["path"],
            media_type=media_type,
            filename=exported["filename"],
        )

    return router
