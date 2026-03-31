import os
import sys
import logging

try:
    from app.core.config import get_config
    from app.core.database import get_conn, get_rag_conn
    from app.core.utils import tokenize_text_for_fts
except ModuleNotFoundError:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if BASE_DIR not in sys.path:
        sys.path.insert(0, BASE_DIR)
    from app.core.config import get_config
    from app.core.database import get_conn, get_rag_conn
    from app.core.utils import tokenize_text_for_fts


logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("rebuild_fts")


def rebuild_fts_for_db(conn):
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='article_fts'")
    if not cur.fetchone():
        logger.info("article_fts table not found, skipping.")
        return

    logger.info("Clearing article_fts...")
    cur.execute("DELETE FROM article_fts")

    logger.info("Fetching articles...")
    cur.execute("SELECT id, regulation_version_id, content FROM article")
    rows = cur.fetchall()

    logger.info(f"Rebuilding FTS for {len(rows)} articles...")
    count = 0
    for row in rows:
        aid = row["id"]
        vid = row["regulation_version_id"]
        content = row["content"]
        fts_content = tokenize_text_for_fts(content)
        cur.execute("""
            INSERT INTO article_fts(content, article_id, regulation_version_id)
            VALUES(?, ?, ?)
        """, (fts_content, aid, vid))
        count += 1
        if count % 500 == 0:
            conn.commit()
            logger.info(f"Processed {count}/{len(rows)}...")

    conn.commit()
    logger.info("Optimizing FTS index...")
    cur.execute("INSERT INTO article_fts(article_fts) VALUES('optimize')")
    conn.commit()
    logger.info("FTS rebuild complete.")


def main():
    cfg = get_config()
    db_path = cfg.get('db_path')
    if db_path and os.path.exists(db_path):
        logger.info(f"Rebuilding main DB: {db_path}")
        with get_conn(cfg) as conn:
            rebuild_fts_for_db(conn)
    else:
        logger.warning(f"Main DB not found at {db_path}")

    rag_paths = cfg.get("rag_db_paths", {})
    for lang, path in rag_paths.items():
        if os.path.exists(path):
            logger.info(f"Rebuilding RAG DB ({lang}): {path}")
            with get_rag_conn(cfg, lang) as conn:
                rebuild_fts_for_db(conn)


if __name__ == "__main__":
    main()
