import uuid
import logging
from datetime import datetime
from app.core.database import get_conn

logger = logging.getLogger("law_assistant")


def upsert_job(cfg, job_id, status, error=None, finished_at=None):
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("UPDATE ingest_job SET status=?, error=?, finished_at=? WHERE id=?",
                (status, error, finished_at, job_id))
    conn.commit()
    conn.close()


def insert_job(cfg, job_id):
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("INSERT INTO ingest_job(id,status,created_at) VALUES(?,?,?)",
                (job_id, "running", datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()


def create_regulation(cfg, title, doc_no, issuer, reg_type, status):
    rid = str(uuid.uuid4())
    version_group_id = str(uuid.uuid4())
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO regulation(id,title,doc_no,issuer,reg_type,status,version_group_id,created_at)
        VALUES(?,?,?,?,?,?,?,?)
    """, (rid, title, doc_no, issuer, reg_type, status, version_group_id, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()
    return rid


def create_version(cfg, regulation_id, effective_date, expiry_date, region, industry, source_file):
    vid = str(uuid.uuid4())
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO regulation_version(id,regulation_id,effective_date,expiry_date,is_current,region,industry,source_file,created_at)
        VALUES(?,?,?,?,?,?,?,?,?)
    """, (vid, regulation_id, effective_date, expiry_date, 1, region, industry, source_file, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()
    return vid


def insert_articles(cfg, version_id, items, job_id=None, language: str = "zh", embedder=None):
    conn = get_conn(cfg)
    cur = conn.cursor()
    total = len(items)
    for idx, (article_no, content) in enumerate(items, 1):
        aid = str(uuid.uuid4())
        cur.execute("""
            INSERT INTO article(id,regulation_version_id,article_no,level_path,content,keywords)
            VALUES(?,?,?,?,?,?)
        """, (aid, version_id, article_no, article_no, content, ""))
        cur.execute("""
            INSERT INTO article_fts(content,article_id,regulation_version_id)
            VALUES(?,?,?)
        """, (content, aid, version_id))
        prof = embedder.get_embed_profile(language) if embedder else None
        v = embedder.compute_embedding(content, lang=language) if embedder else None
        if v is not None:
            cur.execute("""
                INSERT OR REPLACE INTO article_embedding(article_id, lang, model_id, dim, vec)
                VALUES(?,?,?,?,?)
            """, (aid, (prof or {}).get("lang", language), (prof or {}).get("model_id", "unknown"), int(v.shape[0]), v.tobytes()))
        if job_id and total > 0 and (idx % max(1, total // 10) == 0 or idx == total):
            upsert_job(cfg, job_id, "running")
    conn.commit()
    conn.close()


def insert_contract_audit(cfg, audit_id, document_id, status, result_json, model_provider, model_name, created_at):
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO contract_audit(id, document_id, status, result_json, model_provider, model_name, created_at)
        VALUES(?,?,?,?,?,?,?)
    """, (audit_id, document_id, status, result_json, model_provider, model_name, created_at))
    conn.commit()
    conn.close()


def insert_document(cfg, doc_id, filename, original_filename, file_path, file_size, mime_type, user_id,
                    title=None, category=None, status="active"):
    conn = get_conn(cfg)
    cur = conn.cursor()
    
    # Check if document with same original_filename already exists
    cur.execute("SELECT id FROM documents WHERE original_filename = ? AND status = 'active'", (original_filename,))
    row = cur.fetchone()
    
    if row:
        # Update existing document
        existing_id = row[0]
        cur.execute("""
            UPDATE documents 
            SET filename=?, file_path=?, file_size=?, mime_type=?, user_id=?, 
                title=?, category=?, created_at=?
            WHERE id=?
        """, (filename, file_path, int(file_size), mime_type, user_id, 
              title, category, datetime.utcnow().isoformat(), existing_id))
        doc_id = existing_id
    else:
        cur.execute(
            """
            INSERT INTO documents (id, filename, original_filename, file_path, file_size, mime_type, user_id, title, category, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc_id,
                filename,
                original_filename,
                file_path,
                int(file_size),
                mime_type,
                user_id,
                title,
                category,
                status,
                datetime.utcnow().isoformat(),
            ),
        )
    conn.commit()
    conn.close()
    return doc_id


def backfill_legal_document_categories(cfg):
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE documents
        SET category = 'legal'
        WHERE status = 'active'
          AND (category IS NULL OR TRIM(category) = '')
          AND EXISTS (
              SELECT 1
              FROM regulation_version v
              WHERE v.source_file = documents.file_path
          )
        """
    )
    updated = cur.rowcount
    conn.commit()
    conn.close()
    return updated
