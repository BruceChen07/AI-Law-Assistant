import sqlite3


def get_conn(cfg):
    conn = sqlite3.connect(cfg["db_path"])
    conn.row_factory = sqlite3.Row
    return conn


def init_db(cfg):
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS regulation(
        id TEXT PRIMARY KEY,
        title TEXT,
        doc_no TEXT,
        issuer TEXT,
        reg_type TEXT,
        status TEXT,
        version_group_id TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS regulation_version(
        id TEXT PRIMARY KEY,
        regulation_id TEXT,
        effective_date TEXT,
        expiry_date TEXT,
        is_current INTEGER,
        region TEXT,
        industry TEXT,
        source_file TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS article(
        id TEXT PRIMARY KEY,
        regulation_version_id TEXT,
        article_no TEXT,
        level_path TEXT,
        content TEXT,
        keywords TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ingest_job(
        id TEXT PRIMARY KEY,
        status TEXT,
        error TEXT,
        created_at TEXT,
        finished_at TEXT
    )
    """)
    cur.execute("""
    CREATE VIRTUAL TABLE IF NOT EXISTS article_fts
    USING fts5(content, article_id, regulation_version_id, tokenize='unicode61')
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS article_embedding(
        article_id TEXT PRIMARY KEY,
        lang TEXT,
        model_id TEXT,
        dim INTEGER,
        vec BLOB
    )
    """)
    conn.commit()
    conn.close()


def ensure_embedding_columns(cfg):
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(article_embedding)")
    cols = {r[1] for r in cur.fetchall()}
    if "lang" not in cols:
        cur.execute("ALTER TABLE article_embedding ADD COLUMN lang TEXT")
    if "model_id" not in cols:
        cur.execute("ALTER TABLE article_embedding ADD COLUMN model_id TEXT")
    conn.commit()
    conn.close()
