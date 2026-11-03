"""Vector store configuration and cleanup."""
from __future__ import annotations

from app.api.dependencies import require_admin
from app.core.config import get_config
from app.vector_store.factory import VectorStoreFactory
from fastapi import APIRouter, Depends, HTTPException
from app.api.routers.admin.schemas import (
    VectorStoreConfigUpdate,
)

router = APIRouter()


@router.get("/vector-store/config")
def get_vector_store_config(current_user: dict = Depends(require_admin)):
    cfg = get_config()
    engine = VectorStoreFactory.get_engine(cfg)
    chroma_available = VectorStoreFactory.is_chroma_available()
    return {
        "engine": engine,
        "chroma_available": chroma_available,
        "supported_engines": ["sqlite"] + (["chromadb"] if chroma_available else [])
    }


@router.put("/vector-store/config")
def update_vector_store_config(req: VectorStoreConfigUpdate, current_user: dict = Depends(require_admin)):
    if req.engine not in ['sqlite', 'chromadb']:
        raise HTTPException(status_code=400, detail="Unsupported engine")

    cfg = get_config()
    try:
        VectorStoreFactory.set_engine(cfg, req.engine)

        # We need to trigger the async task for processing pending files
        from app.services.vector_store_migration import trigger_migration
        trigger_migration(cfg)

        return {"status": "success", "engine": req.engine}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/vector-store/cleanup")
def cleanup_old_vector_data(current_user: dict = Depends(require_admin)):
    cfg = get_config()
    from app.services.vector_store_migration import cleanup_old_engine_data
    result = cleanup_old_engine_data(cfg)
    return result
