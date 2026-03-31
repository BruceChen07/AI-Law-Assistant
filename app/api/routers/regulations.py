import os
import uuid
import logging
from typing import Optional
from fastapi import APIRouter, UploadFile, File, Form, BackgroundTasks, HTTPException, Depends
from app.core.database import get_conn
from app.core.logger import get_pipeline_logger
from app.services.importer import process_import
from app.services.crud import insert_job, insert_document, backfill_legal_document_categories
from app.services.search import search_regulations
from app.api.schemas import SearchQuery
from app.api.dependencies import get_current_user


def build_router(cfg, embedder, reranker=None):
    router = APIRouter()
    logger = logging.getLogger("law_assistant")
    rag_logger = get_pipeline_logger(
        cfg, name="rag_pipeline", filename="rag_pipeline.log")

    @router.post("/regulations/import")
    async def import_regulation(
        background_tasks: BackgroundTasks,
        file: UploadFile = File(...),
        title: str = Form(...),
        doc_no: str = Form(""),
        issuer: str = Form(""),
        reg_type: str = Form(""),
        status: str = Form("current"),
        effective_date: str = Form(""),
        expiry_date: str = Form(""),
        region: str = Form(""),
        industry: str = Form(""),
        regulation_id: str = Form(""),
        language: str = Form("zh"),
        current_user: dict = Depends(get_current_user),
    ):
        class_name = "RegulationsRouter"
        job_id = str(uuid.uuid4())
        rag_logger.info(
            "class=%s stage=import_request_received job_id=%s filename=%s title=%s user_id=%s",
            class_name, job_id, file.filename, title, current_user.get("id", ""))
        insert_job(cfg, job_id)
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in [".docx", ".pdf"]:
            rag_logger.warning(
                "class=%s stage=import_request_rejected job_id=%s ext=%s",
                class_name, job_id, ext)
            raise HTTPException(
                status_code=400, detail="unsupported file type, only docx and pdf are allowed")
                
        cache_dir = os.path.join(cfg.get("data_dir", "./data"), "uploads", "cache")
        os.makedirs(cache_dir, exist_ok=True)
        save_path = os.path.join(cache_dir, f"{job_id}{ext}")
        with open(save_path, "wb") as f:
            f.write(await file.read())
        rag_logger.info(
            "class=%s stage=import_file_saved job_id=%s save_path=%s size_bytes=%s",
            class_name, job_id, save_path, os.path.getsize(save_path))
            
        # Also insert into new upload_log table
        with get_conn(cfg) as conn:
            conn.execute(
                "INSERT INTO upload_log (file_id, original_filename, file_path, created_at, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                (job_id, file.filename, save_path)
            )
            conn.commit()

        # record document for admin dashboard
        doc_id = str(uuid.uuid4())
        doc_id = insert_document(
            cfg,
            doc_id=doc_id,
            filename=os.path.basename(save_path),
            original_filename=file.filename,
            file_path=save_path,
            file_size=os.path.getsize(save_path),
            mime_type=getattr(file, "content_type", None),
            user_id=current_user["id"],
            title=title,
            category="legal",
            status="active",
        )
        backfill_legal_document_categories(cfg)
        background_tasks.add_task(
            process_import,
            cfg, embedder, job_id, save_path, title, doc_no, issuer, reg_type,
            status, effective_date, expiry_date, region, industry, regulation_id, language,
        )
        rag_logger.info(
            "class=%s stage=import_task_enqueued job_id=%s doc_id=%s language=%s",
            class_name, job_id, doc_id, language)
        logger.info("regulation_import_enqueued job_id=%s doc_id=%s", job_id, doc_id)
        return {"job_id": job_id, "doc_id": doc_id}

    @router.get("/regulations/import/{job_id}")
    def import_status(job_id: str):
        class_name = "RegulationsRouter"
        conn = get_conn(cfg)
        cur = conn.cursor()
        cur.execute("SELECT * FROM ingest_job WHERE id=?", (job_id,))
        row = cur.fetchone()
        conn.close()
        if not row:
            rag_logger.warning(
                "class=%s stage=import_status_not_found job_id=%s",
                class_name, job_id)
            raise HTTPException(status_code=404, detail="job not found")
        data = dict(row)
        rag_logger.info(
            "class=%s stage=import_status_poll job_id=%s status=%s error=%s",
            class_name, job_id, data.get("status", ""), bool(data.get("error")))
        return data

    @router.get("/regulations")
    def list_regulations():
        conn = get_conn(cfg)
        cur = conn.cursor()
        cur.execute("SELECT * FROM regulation ORDER BY created_at DESC")
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    @router.get("/regulations/{regulation_id}/articles")
    def list_articles(regulation_id: str, version_id: Optional[str] = None):
        conn = get_conn(cfg)
        cur = conn.cursor()
        if version_id:
            cur.execute(
                "SELECT a.* FROM article a WHERE a.regulation_version_id=? ORDER BY a.article_no", (version_id,))
        else:
            cur.execute(
                "SELECT a.* FROM article a JOIN regulation_version v ON v.id=a.regulation_version_id WHERE v.regulation_id=? ORDER BY a.article_no", (regulation_id,))
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    @router.post("/regulations/search")
    def search(q: SearchQuery):
        rag_logger.info(
            "class=%s stage=search_request query=%s lang=%s top_k=%s semantic=%s",
            "RegulationsRouter", str(q.query or "")[:80], q.language, q.top_k, q.use_semantic)
        return search_regulations(cfg, q, embedder, reranker)

    return router
