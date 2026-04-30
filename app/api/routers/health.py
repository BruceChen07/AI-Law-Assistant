from fastapi import APIRouter, Depends
from app.api.dependencies import get_app_embedder


def build_router():
    router = APIRouter()

    @router.get("/health")
    def health(embedder=Depends(get_app_embedder)):
        status = embedder.get_registry_status()
        return {
            "status": "ok",
            "embedding_ready": status["ready"],
            "embedding_default_language": status["default_language"],
            "embedding_languages": status["languages"]
        }

    return router
