from fastapi import APIRouter


def build_router(embedder):
    router = APIRouter()

    @router.get("/health")
    def health():
        status = embedder.get_registry_status()
        return {
            "status": "ok",
            "embedding_ready": status["ready"],
            "embedding_default_language": status["default_language"],
            "embedding_languages": status["languages"]
        }

    return router
