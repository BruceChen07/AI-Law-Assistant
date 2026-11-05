"""Migrations for durable audit progress and the audit queue."""
from __future__ import annotations


def apply(cur) -> None:
    """Durable audit progress + FIFO audit queue."""
    cur.execute("""
    CREATE TABLE IF NOT EXISTS contract_audit_progress(
        audit_id TEXT PRIMARY KEY,
        document_id TEXT,
        user_id TEXT,
        status TEXT NOT NULL,
        stage TEXT,
        percent INTEGER DEFAULT 0,
        message TEXT,
        detail_json TEXT,
        plan_json TEXT,
        plan_hash TEXT,
        source_hash TEXT,
        chunks_total INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT
    )
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_contract_audit_progress_document ON contract_audit_progress(document_id, updated_at)")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit_queue(
        audit_id TEXT PRIMARY KEY,
        document_id TEXT,
        file_path TEXT NOT NULL,
        lang TEXT,
        retrieval_options_json TEXT,
        title TEXT,
        user_id TEXT,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT
    )
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_queue_status ON audit_queue(status, created_at)")
