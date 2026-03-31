import logging
import os
import shutil
import sqlite3
import threading
import time
import uuid
from typing import Dict, List, Optional, Tuple

from app.core.database import get_conn
from app.core.embedding import EmbeddingService
from app.core.utils import extract_text_with_config, split_articles
from app.services.tax_contract_parser import detect_text_language
from app.vector_store.base import Chunk
from app.vector_store.factory import VectorStoreFactory

logger = logging.getLogger(__name__)


def trigger_migration(cfg: dict):
    thread = threading.Thread(target=_run_migration, args=(cfg,))
    thread.daemon = True
    thread.start()


def _split_long_text(text: str, max_chars: int, overlap: int) -> List[str]:
    content = str(text or "").strip()
    if not content:
        return []
    if len(content) <= max_chars:
        return [content]
    chunks: List[str] = []
    step = max(1, max_chars - max(0, overlap))
    start = 0
    while start < len(content):
        end = min(len(content), start + max_chars)
        piece = content[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(content):
            break
        start += step
    return chunks


def _load_embedder(cfg: dict) -> EmbeddingService:
    embedder = EmbeddingService(default_language=str(
        cfg.get("default_language", "zh")).lower())
    ready = embedder.load_embedders(cfg)
    if ready <= 0:
        raise RuntimeError("embedding models are not ready")
    return embedder


def _build_file_chunks(cfg: dict, file_record: Dict[str, str], embedder: EmbeddingService) -> Tuple[List[Chunk], str]:
    file_id = str(file_record.get("file_id") or "").strip()
    file_path = str(file_record.get("file_path") or "").strip()
    original_filename = str(file_record.get("original_filename") or "").strip()
    if not file_id or not file_path:
        return [], "zh"
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"file not found: {file_path}")

    text, meta = extract_text_with_config(cfg, file_path)
    if not str(text or "").strip():
        raise ValueError("empty extracted text")

    default_lang = str(cfg.get("default_language", "zh"))
    language = detect_text_language(text, default=default_lang)
    articles = split_articles(text)
    max_chars = max(200, int(cfg.get("vector_chunk_chars", 1000)))
    overlap = max(0, int(cfg.get("vector_chunk_overlap", 100)))
    chunks: List[Chunk] = []

    chunk_seq = 0
    for article_no, article_text in articles:
        for seg_idx, piece in enumerate(_split_long_text(article_text, max_chars=max_chars, overlap=overlap), 1):
            vector_np = embedder.compute_embedding(piece, lang=language)
            if vector_np is None:
                continue
            chunk_seq += 1
            chunk_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{file_id}:{chunk_seq}:{article_no}:{seg_idx}"))
            chunks.append(
                Chunk(
                    id=chunk_id,
                    file_id=file_id,
                    text=piece,
                    vector=vector_np.tolist(),
                    metadata={
                        "source_file": file_path,
                        "original_filename": original_filename,
                        "article_no": str(article_no or ""),
                        "segment_index": seg_idx,
                        "chunk_seq": chunk_seq,
                        "language": language,
                        "ocr_used": bool(meta.get("ocr_used")),
                    },
                )
            )
    return chunks, language


def _update_upload_status(cfg: dict, file_id: str, status: str, engine: str, from_status: Optional[str] = None) -> bool:
    sql = "UPDATE upload_log SET status = ?, engine = ?, updated_at = CURRENT_TIMESTAMP WHERE file_id = ?"
    params: Tuple[str, ...] = (status, engine, file_id)
    if from_status:
        sql += " AND status = ?"
        params = (status, engine, file_id, from_status)

    attempts = 4
    for i in range(attempts):
        try:
            with get_conn(cfg) as conn:
                cur = conn.execute(sql, params)
                conn.commit()
                return cur.rowcount > 0
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e).lower() and i < attempts - 1:
                time.sleep(0.2 * (i + 1))
                continue
            raise
    return False


def _run_migration(cfg: dict):
    engine = VectorStoreFactory.get_engine(cfg)
    logger.info("vector_store_migration_start engine=%s", engine)

    with get_conn(cfg) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT file_id, file_path, original_filename FROM upload_log WHERE status = 'pending' ORDER BY created_at ASC"
        )
        pending_files = [dict(r) for r in cur.fetchall()]

    if not pending_files:
        logger.info("vector_store_migration_empty")
        return

    embedder = _load_embedder(cfg)
    store = VectorStoreFactory.get_store(cfg, embedder=embedder)
    collection_name = str(
        cfg.get("vector_collection_name", "regulation_chunks"))
    store.initialize(collection_name)

    for file_record in pending_files:
        file_id = str(file_record.get("file_id") or "").strip()
        if not file_id:
            continue
        try:
            claimed = _update_upload_status(cfg, file_id, "processing", engine, from_status="pending")
            if not claimed:
                logger.info("vector_store_migration_skip file_id=%s reason=not_pending", file_id)
                continue
            chunks, language = _build_file_chunks(cfg, file_record, embedder)
            if not chunks:
                raise ValueError("no valid chunks generated")

            store.delete_by_file_id(file_id)
            if engine == "chromadb" and hasattr(store, "add_texts"):
                texts = [c.text for c in chunks]
                ids = [c.id for c in chunks]
                metadatas = [dict(c.metadata) for c in chunks]
                embeddings = [c.vector for c in chunks]
                store.add_texts(texts=texts, metadatas=metadatas,
                                ids=ids, embeddings=embeddings)
            else:
                store.insert_chunks(chunks)

            _update_upload_status(cfg, file_id, "done", engine)
            logger.info(
                "vector_store_migration_done file_id=%s engine=%s chunks=%s language=%s",
                file_id,
                engine,
                len(chunks),
                language,
            )
        except Exception as e:
            try:
                _update_upload_status(cfg, file_id, "failed", engine)
            except Exception:
                logger.exception("vector_store_migration_status_update_failed file_id=%s engine=%s", file_id, engine)
            logger.exception(
                "vector_store_migration_failed file_id=%s engine=%s err=%s", file_id, engine, str(e))


def cleanup_old_engine_data(cfg: dict) -> dict:
    engine = VectorStoreFactory.get_engine(cfg)
    logger.info("vector_store_cleanup_start engine=%s", engine)

    deleted_records = 0

    if engine == "chromadb":
        with get_conn(cfg) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='vector_chunks' LIMIT 1")
            has_vector_chunks = cur.fetchone() is not None
            if has_vector_chunks:
                cur.execute("SELECT count(*) as cnt FROM vector_chunks")
                row = cur.fetchone()
                deleted_records = int((row or {}).get(
                    "cnt", 0) if isinstance(row, dict) else row["cnt"])
                cur.execute("DELETE FROM vector_chunks")
            cur.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='vector_chunks_fts' LIMIT 1")
            has_vector_chunks_fts = cur.fetchone() is not None
            if has_vector_chunks_fts:
                cur.execute("DELETE FROM vector_chunks_fts")
            conn.commit()

    elif engine == "sqlite":
        chroma_path = os.path.join(cfg.get("data_dir", "./data"), "chroma_db")
        if os.path.exists(chroma_path):
            deleted_records = 1
            try:
                shutil.rmtree(chroma_path)
            except Exception as e:
                logger.error(
                    "vector_store_cleanup_chroma_failed err=%s", str(e))

    return {
        "status": "success",
        "message": f"Cleaned up data for engine other than {engine}",
        "deleted_records": deleted_records,
    }
