"""Migrations for Regulation / article / FTS / embedding tables for retrieval."""
from __future__ import annotations


def apply(cur) -> None:
    """Regulation / article / FTS / embedding tables for retrieval."""
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
        keywords TEXT,
        rule_type TEXT,
        conditions_json TEXT,
        constraints_json TEXT
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

    cur.execute("""
    CREATE TABLE IF NOT EXISTS regulation_document(
        id TEXT PRIMARY KEY,
        original_filename TEXT NOT NULL,
        file_path TEXT NOT NULL,
        file_type TEXT NOT NULL,
        file_size INTEGER NOT NULL,
        checksum TEXT,
        parse_status TEXT NOT NULL DEFAULT 'pending',
        uploaded_by TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT
    )
    """)

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_regulation_document_created_at ON regulation_document(created_at)")
