import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends

from app.api.dependencies import get_app_embedder
from app.core.config import get_config
from app.services.crud import list_running_contract_audit_progress


def build_router(cfg: Optional[Dict[str, Any]] = None):
    router = APIRouter()

    def _runtime_cfg() -> Dict[str, Any]:
        return cfg if isinstance(cfg, dict) else get_config()

    @router.get("/health")
    def health(embedder=Depends(get_app_embedder)):
        status = embedder.get_registry_status()
        running_audits = 0
        try:
            runtime_cfg = _runtime_cfg()
            db_path = str(runtime_cfg.get("db_path") or "")
            # Never create the database from a health probe.
            if db_path and os.path.exists(db_path):
                running_audits = len(
                    list_running_contract_audit_progress(runtime_cfg, limit=50))
        except Exception:
            running_audits = 0
        try:
            from app.core.dependency_preflight import get_last_report

            preflight = get_last_report() or {}
        except Exception:
            preflight = {}
        return {
            "status": "ok",
            "embedding_ready": status["ready"],
            "embedding_default_language": status["default_language"],
            "embedding_languages": status["languages"],
            "audits_running": running_audits,
            "preflight": {
                "ok": bool(preflight.get("ok", True)),
                "critical_count": int(preflight.get("critical_count") or 0),
                "warning_count": int(preflight.get("warning_count") or 0),
                "issues": [
                    str(item.get("name") or "")
                    for item in (preflight.get("critical") or [])
                ],
                "checked_at": str(preflight.get("checked_at") or ""),
            },
        }

    return router
