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
        return {
            "status": "ok",
            "embedding_ready": status["ready"],
            "embedding_default_language": status["default_language"],
            "embedding_languages": status["languages"],
            "audits_running": running_audits,
        }

    return router
