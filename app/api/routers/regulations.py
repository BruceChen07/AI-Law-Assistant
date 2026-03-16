import os
import uuid
from typing import Optional
from fastapi import APIRouter, UploadFile, File, Form, BackgroundTasks, HTTPException, Depends
from app.core.database import get_conn
from app.services.importer import process_import
from app.services.crud import insert_job, insert_document
from app.services.search import search_regulations
from app.api.schemas import SearchQuery
from app.api.dependencies import get_current_user


def build_router(cfg, embedder):
    router = APIRouter()

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
        job_id = str(uuid.uuid4())
        insert_job(cfg, job_id)
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in [".docx", ".pdf"]:
            raise HTTPException(status_code=400, detail="unsupported file type, only docx and pdf are allowed")
        save_path = os.path.join(cfg["files_dir"], f"{job_id}{ext}")
        with open(save_path, "wb") as f:
            f.write(await file.read())
        # record document for admin dashboard
        doc_id = str(uuid.uuid4())
        insert_document(
            cfg,
            doc_id=doc_id,
            filename=os.path.basename(save_path),
            original_filename=file.filename,
            file_path=save_path,
            file_size=os.path.getsize(save_path),
            mime_type=getattr(file, "content_type", None),
            user_id=current_user["id"],
            title=title,
            category=reg_type,
            status="active",
        )
        background_tasks.add_task(
            process_import,
            cfg, embedder, job_id, save_path, title, doc_no, issuer, reg_type,
            status, effective_date, expiry_date, region, industry, regulation_id, language,
        )
        return {"job_id": job_id, "doc_id": doc_id}

    @router.get("/regulations/import/{job_id}")
    def import_status(job_id: str):
        conn = get_conn(cfg)
        cur = conn.cursor()
        cur.execute("SELECT * FROM ingest_job WHERE id=?", (job_id,))
        row = cur.fetchone()
        conn.close()
        if not row:
            raise HTTPException(status_code=404, detail="job not found")
        return dict(row)

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
            cur.execute("SELECT a.* FROM article a WHERE a.regulation_version_id=? ORDER BY a.article_no", (version_id,))
        else:
            cur.execute("SELECT a.* FROM article a JOIN regulation_version v ON v.id=a.regulation_version_id WHERE v.regulation_id=? ORDER BY a.article_no", (regulation_id,))
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    @router.post("/regulations/search")
    def search(q: SearchQuery):
        return search_regulations(cfg, q, embedder)

    return router
