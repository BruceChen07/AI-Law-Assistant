"""Durable audit progress and queue endpoints."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_current_user
from app.services import audit_task_runner
from app.services.crud import (
    get_contract_audit_progress,
    get_latest_contract_audit_by_document,
)
from app.api.routers.contracts._cfg import contracts_cfg

logger = logging.getLogger("law_assistant")

router = APIRouter()


def _load_progress(cfg: Dict[str, Any], audit_id: str) -> Optional[Dict[str, Any]]:
    row = get_contract_audit_progress(cfg, audit_id)
    if not row:
        return None
    return {
        "audit_id": row.get("audit_id"),
        "status": row.get("status"),
        "progress": int(row.get("percent") or 0),
        "stage": row.get("stage") or "",
        "message": row.get("message") or "",
        "updated_at": row.get("updated_at") or "",
        "detail": _parse_json(row.get("detail_json")),
        "document_id": row.get("document_id") or "",
        "chunks_total": int(row.get("chunks_total") or 0),
        "durable": True,
    }


def _parse_json(raw: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw or "") or "null")
    except ValueError:
        return None


@router.get("/contracts/audit/{audit_id}/task")
def get_contract_audit_task(
    audit_id: str,
    current_user: dict = Depends(get_current_user),
    cfg: dict = Depends(contracts_cfg),
):
    _ = current_user
    progress = _load_progress(cfg, audit_id)
    if not progress:
        raise HTTPException(status_code=404, detail="audit progress not found")
    document_id = str(progress.get("document_id") or "")
    result = None
    meta: Dict[str, Any] = {}
    if progress.get("status") == "done" and document_id:
        row = get_latest_contract_audit_by_document(cfg, document_id)
        if row and row.get("result_json"):
            result = _parse_json(row.get("result_json"))
    return {
        "audit_id": audit_id,
        "status": progress.get("status"),
        "progress": progress,
        "document_id": document_id,
        "has_result": result is not None,
        "result": result,
        "meta": meta,
    }


@router.get("/contracts/audit/queue")
def get_contract_audit_queue(
    current_user: dict = Depends(get_current_user),
):
    _ = current_user
    return audit_task_runner.queue_snapshot()


@router.delete("/contracts/audit/queue/{audit_id}")
def cancel_contract_audit_queue(
    audit_id: str,
    current_user: dict = Depends(get_current_user),
):
    _ = current_user
    outcome = audit_task_runner.cancel(audit_id)
    if outcome == "missing":
        raise HTTPException(status_code=404, detail="queued audit not found")
    if outcome == "running":
        raise HTTPException(
            status_code=409,
            detail="audit is already running and cannot be cancelled",
        )
    return {"audit_id": audit_id, "status": "cancelled"}
