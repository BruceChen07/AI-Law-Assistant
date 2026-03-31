import logging
import time
from datetime import datetime
from app.core.utils import extract_text_with_config, split_articles
from app.core.logger import get_pipeline_logger
from app.services.crud import create_regulation, create_version, insert_articles, upsert_job
from app.services.tax_contract_parser import detect_text_language
from app.services.vector_store_migration import trigger_migration

logger = logging.getLogger("law_assistant")


def process_import(cfg, embedder, job_id, file_path, title, doc_no, issuer, reg_type, status,
                   effective_date, expiry_date, region, industry, regulation_id, language):
    class_name = "RegulationImporter"
    rag_logger = get_pipeline_logger(
        cfg, name="rag_pipeline", filename="rag_pipeline.log")
    t0 = time.perf_counter()
    try:
        rag_logger.info(
            "class=%s stage=import_start job_id=%s file_path=%s lang=%s",
            class_name, job_id, file_path, language)
        logger.info("import_start job_id=%s file=%s", job_id, file_path)
        t_extract = time.perf_counter()
        text, _ = extract_text_with_config(cfg, file_path)
        rag_logger.info(
            "class=%s stage=extract_done job_id=%s chars=%s cost_ms=%s",
            class_name, job_id, len(str(text or "")), int((time.perf_counter() - t_extract) * 1000))

        # Auto-detect language to avoid mismatch when UI language differs from doc language
        actual_lang = detect_text_language(text, default=language)
        if actual_lang != language:
            logger.info("import_language_override job_id=%s req_lang=%s detected_lang=%s",
                        job_id, language, actual_lang)
            rag_logger.info(
                "class=%s stage=language_override job_id=%s requested=%s detected=%s",
                class_name, job_id, language, actual_lang)
            language = actual_lang

        t_split = time.perf_counter()
        articles = split_articles(text)
        rag_logger.info(
            "class=%s stage=split_done job_id=%s article_count=%s cost_ms=%s",
            class_name, job_id, len(articles), int((time.perf_counter() - t_split) * 1000))
        if not regulation_id:
            regulation_id = create_regulation(
                cfg, title, doc_no, issuer, reg_type, status)
            rag_logger.info(
                "class=%s stage=regulation_created job_id=%s regulation_id=%s",
                class_name, job_id, regulation_id)
        t_db = time.perf_counter()
        version_id = create_version(
            cfg, regulation_id, effective_date, expiry_date, region, industry, file_path)
        insert_articles(cfg, version_id, articles,
                        language=language, embedder=embedder)
        rag_logger.info(
            "class=%s stage=db_insert_done job_id=%s version_id=%s cost_ms=%s",
            class_name, job_id, version_id, int((time.perf_counter() - t_db) * 1000))
        logger.info("import_embedding_lang job_id=%s language=%s",
                    job_id, language)
        upsert_job(cfg, job_id, "done", None, datetime.utcnow().isoformat())
        trigger_migration(cfg)
        rag_logger.info(
            "class=%s stage=import_done job_id=%s version_id=%s article_count=%s total_ms=%s",
            class_name, job_id, version_id, len(articles), int((time.perf_counter() - t0) * 1000))
        logger.info("import_done job_id=%s version_id=%s article_count=%s",
                    job_id, version_id, len(articles))
    except Exception as e:
        upsert_job(cfg, job_id, "failed", str(
            e), datetime.utcnow().isoformat())
        rag_logger.exception(
            "class=%s stage=import_failed job_id=%s total_ms=%s error=%s",
            class_name, job_id, int((time.perf_counter() - t0) * 1000), str(e))
        logger.exception("import_failed job_id=%s error=%s", job_id, str(e))
