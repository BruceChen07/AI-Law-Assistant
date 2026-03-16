import os
import time
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import get_config, ensure_dirs
from app.core.logger import setup_logging
from app.core.database import init_db, ensure_embedding_columns
from app.core.embedding import EmbeddingService
from app.core.reranker import RerankerService
from app.core.llm import LLMService
from app.api.routers.health import build_router as build_health_router
from app.api.routers.embedding import build_router as build_embedding_router
from app.api.routers.regulations import build_router as build_regulations_router
from app.api.routers.auth import build_router as build_auth_router
from app.api.routers.admin import router as admin_router
from app.api.routers.contracts import build_router as build_contracts_router

def init_only():
    cfg = get_config()
    ensure_dirs(cfg)
    init_db(cfg)
    ensure_embedding_columns(cfg)


def create_app():
    cfg = get_config()
    ensure_dirs(cfg)
    logger = setup_logging(cfg)
    init_db(cfg)
    ensure_embedding_columns(cfg)

    embedder = EmbeddingService(default_language=str(
        cfg.get("default_language", "zh")).lower())
    embedder_count = embedder.load_embedders(cfg)
    status = embedder.get_registry_status()
    logger.info(
        "service_start db=%s embedding_ready=%s langs=%s",
        cfg.get("db_path"),
        embedder_count > 0,
        status["languages"],
    )

    reranker = RerankerService(
        cfg.get("reranker_model_path"),
        profiles=cfg.get("reranker_profiles"),
        batch_size=cfg.get("rerank_batch_size", 8),
        max_len=cfg.get("rerank_max_len", 512),
    )
    llm = LLMService(cfg)

    app = FastAPI(title="Law Assistant")

    @app.get("/")
    def root():
        return {
            "service": "Law Assistant API",
            "status": "ok",
            "docs": "/docs",
            "health": "/health",
            "port": os.environ.get("APP_PORT", "8000")
        }

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.get("cors_allow_origins", ["*"]),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    static_dir = cfg.get("static_dir")
    if static_dir and os.path.exists(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir,
                  html=True), name="static")

    @app.middleware("http")
    async def access_log(request: Request, call_next):
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
            ms = int((time.perf_counter() - t0) * 1000)
            logger.info("http %s %s status=%s cost_ms=%s", request.method,
                        request.url.path, response.status_code, ms)
            return response
        except Exception:
            ms = int((time.perf_counter() - t0) * 1000)
            logger.exception("http %s %s status=500 cost_ms=%s",
                             request.method, request.url.path, ms)
            raise

    app.include_router(build_health_router(embedder))
    app.include_router(build_embedding_router(embedder))
    app.include_router(build_regulations_router(cfg, embedder, reranker))
    app.include_router(build_auth_router())
    app.include_router(admin_router)
    app.include_router(build_contracts_router(cfg, llm))

    app.state.cfg = cfg
    app.state.embedder = embedder
    app.state.reranker = reranker
    app.state.llm = llm
    app.state.logger = logger
    return app
