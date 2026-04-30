from fastapi import APIRouter, HTTPException, Depends
from app.api.schemas import EmbeddingRequest
from app.api.dependencies import get_app_embedder


def build_router():
    router = APIRouter()

    @router.get("/embeddings/info")
    def embedding_info(embedder=Depends(get_app_embedder)):
        status = embedder.get_registry_status()
        models = {}
        for k, v in status["registry"].items():
            models[k] = {
                "model_id": v.get("model_id"),
                "source": v.get("source"),
                "model_path": v.get("model_path"),
                "tokenizer_dir": v.get("tokenizer_dir"),
                "max_seq_len": v.get("max_len"),
                "pooling": v.get("pooling"),
                "inputs": v.get("inputs", [])
            }
        return {
            "ready": status["ready"],
            "default_language": status["default_language"],
            "models": models
        }

    @router.post("/embeddings/encode")
    def encode_embedding(req: EmbeddingRequest, embedder=Depends(get_app_embedder)):
        v = embedder.compute_embedding(
            req.text, is_query=req.is_query, lang=req.language)
        prof = embedder.get_embed_profile(req.language)
        if v is None or not prof:
            raise HTTPException(
                status_code=503, detail=f"embedding model not ready for language={req.language}")
        return {
            "dim": int(v.shape[0]),
            "is_query": req.is_query,
            "language": prof.get("lang"),
            "model_id": prof.get("model_id"),
            "vector": v.tolist()
        }

    return router
