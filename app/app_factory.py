import os
import time
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import get_config, ensure_dirs, get_config_path
from app.core.logger import setup_logging, get_pipeline_logger
from app.core.database import init_db, ensure_embedding_columns
from app.core.embedding import EmbeddingService
from app.core.reranker import RerankerService
from app.core.llm import LLMService
from app.core.translation import TranslationService
from bin.ensure_local_models import ensure_models
from app.api.routers.health import build_router as build_health_router
from app.api.routers.embedding import build_router as build_embedding_router
from app.api.routers.regulations import build_router as build_regulations_router
from app.api.routers.auth import build_router as build_auth_router
from app.api.routers.admin import router as admin_router
from app.api.routers.contracts import build_router as build_contracts_router
from app.api.routers.tax_audit import build_router as build_tax_audit_router


def init_only():
    cfg = get_config()
    ensure_dirs(cfg)
    init_db(cfg)
    from app.core.database import ensure_embedding_columns, ensure_article_dsl_columns
    ensure_embedding_columns(cfg)
    ensure_article_dsl_columns(cfg)


def create_app():
    cfg = get_config()
    ensure_dirs(cfg)
    logger = setup_logging(cfg)
    rag_logger = get_pipeline_logger(
        cfg, name="rag_pipeline", filename="rag_pipeline.log")
    init_db(cfg)
    from app.core.database import ensure_embedding_columns, ensure_article_dsl_columns
    ensure_embedding_columns(cfg)
    ensure_article_dsl_columns(cfg)

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
    rag_logger.info("class=%s stage=service_ready db=%s",
                    "AppFactory", cfg.get("db_path"))

    reranker = RerankerService(
        cfg.get("reranker_model_path"),
        profiles=cfg.get("reranker_profiles"),
        batch_size=cfg.get("rerank_batch_size", 8),
        max_len=cfg.get("rerank_max_len", 512),
    )
    preflight_enabled = bool(cfg.get("model_preflight_check_on_startup", True))
    preflight_auto_download = bool(
        cfg.get("model_preflight_auto_download_on_startup", False))
    preflight_include_optional = bool(
        cfg.get("model_preflight_include_optional", True))
    preflight_require_all = bool(cfg.get("model_preflight_require_all", True))
    if preflight_enabled:
        logger.info(
            "model_preflight_start config_path=%s check_only=%s include_optional=%s types=all",
            get_config_path(),
            (not preflight_auto_download),
            preflight_include_optional,
        )
        preflight = ensure_models(
            cfg=cfg,
            check_only=not preflight_auto_download,
            include_optional=preflight_include_optional,
            model_types="all",
        )
        rows = preflight.get("models") or []
        logger.info(
            "model_preflight_summary all_ready=%s checked=%s auto_download=%s include_optional=%s",
            bool(preflight.get("all_ready", False)),
            len(rows),
            preflight_auto_download,
            preflight_include_optional,
        )
        if not rows:
            logger.warning("model_preflight_no_items_checked")
        for item in rows:
            logger.info(
                "model_preflight_item name=%s type=%s ok=%s downloaded=%s path=%s model_id=%s reason=%s message=%s",
                item.get("name", ""),
                item.get("type", ""),
                bool(item.get("ok", False)),
                bool(item.get("downloaded", False)),
                item.get("path", "") or (
                    item.get("detail") or {}).get("path", ""),
                item.get("model_id", ""),
                item.get("reason", ""),
                item.get("message", ""),
            )
        if not bool(preflight.get("all_ready", False)):
            missing = [
                str(x.get("name", ""))
                for x in rows
                if not bool(x.get("ok", False))
            ]
            if preflight_require_all:
                raise RuntimeError(
                    "local model preflight failed, run: python bin/ensure_local_models.py --types all --include-optional")
            logger.warning("model_preflight_not_ready missing=%s", missing)
    else:
        logger.info("model_preflight_disabled")
    llm = LLMService(cfg)
    translator = TranslationService(cfg)

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
    app.include_router(build_contracts_router(
        cfg, llm, embedder, reranker, translator))
    app.include_router(build_tax_audit_router(cfg))

    app.state.cfg = cfg
    app.state.embedder = embedder
    app.state.reranker = reranker
    app.state.llm = llm
    app.state.translator = translator
    app.state.logger = logger
    app.state.rag_logger = rag_logger
    return app
