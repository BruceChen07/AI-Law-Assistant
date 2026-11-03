"""Migrations for Vector store config and upload log."""
from __future__ import annotations


def apply(cur) -> None:
    """Vector store config and upload log."""
    cur.execute("""
    CREATE TABLE IF NOT EXISTS vector_store_config(
        id INTEGER PRIMARY KEY CHECK (id = 1),
        engine TEXT NOT NULL DEFAULT 'sqlite',
        updated_at TEXT NOT NULL
    )
    """)

    cur.execute(
        "INSERT OR IGNORE INTO vector_store_config (id, engine, updated_at) VALUES (1, 'sqlite', CURRENT_TIMESTAMP)")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS upload_log(
        file_id TEXT PRIMARY KEY,
        original_filename TEXT NOT NULL,
        md5_hash TEXT,
        file_path TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        engine TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT
    )
    """)

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_upload_log_status ON upload_log(status)")
