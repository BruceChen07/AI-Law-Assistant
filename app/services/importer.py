import logging
from datetime import datetime
from app.core.utils import extract_text_with_config, split_articles
from app.services.crud import create_regulation, create_version, insert_articles, upsert_job

logger = logging.getLogger("law_assistant")


def process_import(cfg, embedder, job_id, file_path, title, doc_no, issuer, reg_type, status,
                   effective_date, expiry_date, region, industry, regulation_id, language):
    try:
        logger.info("import_start job_id=%s file=%s", job_id, file_path)
        text, _ = extract_text_with_config(cfg, file_path)
        articles = split_articles(text)
        if not regulation_id:
            regulation_id = create_regulation(
                cfg, title, doc_no, issuer, reg_type, status)
        version_id = create_version(
            cfg, regulation_id, effective_date, expiry_date, region, industry, file_path)
        insert_articles(cfg, version_id, articles, language=language, embedder=embedder)
        logger.info("import_embedding_lang job_id=%s language=%s",
                    job_id, language)
        upsert_job(cfg, job_id, "done", None, datetime.utcnow().isoformat())
        logger.info("import_done job_id=%s version_id=%s article_count=%s",
                    job_id, version_id, len(articles))
    except Exception as e:
        upsert_job(cfg, job_id, "failed", str(
            e), datetime.utcnow().isoformat())
        logger.exception("import_failed job_id=%s error=%s", job_id, str(e))
