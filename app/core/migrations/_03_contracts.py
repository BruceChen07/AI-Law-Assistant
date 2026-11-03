"""Migrations for Contract audit and PDF digitization tables."""
from __future__ import annotations


def apply(cur) -> None:
    """Contract audit and PDF digitization tables."""
    cur.execute("""
    CREATE TABLE IF NOT EXISTS contract_audit(
        id TEXT PRIMARY KEY,
        document_id TEXT NOT NULL,
        status TEXT NOT NULL,
        result_json TEXT,
        model_provider TEXT,
        model_name TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT,
        FOREIGN KEY (document_id) REFERENCES documents(id)
    )
    """)

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_contract_audit_document_id ON contract_audit(document_id)")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_contract_audit_created_at ON contract_audit(created_at)")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS document_digitization(
        id TEXT PRIMARY KEY,
        document_id TEXT NOT NULL,
        version_no INTEGER NOT NULL,
        status TEXT NOT NULL,
        source_file_path TEXT NOT NULL,
        source_sha256 TEXT,
        source_ext TEXT NOT NULL,
        digitized_docx_path TEXT,
        artifact_dir TEXT,
        md_path TEXT,
        middle_path TEXT,
        content_list_path TEXT,
        ocr_engine TEXT,
        ocr_used INTEGER NOT NULL DEFAULT 0,
        layout_retained INTEGER NOT NULL DEFAULT 0,
        source_text_length INTEGER NOT NULL DEFAULT 0,
        digitized_text_length INTEGER NOT NULL DEFAULT 0,
        text_match_ratio REAL NOT NULL DEFAULT 0,
        paragraph_count INTEGER NOT NULL DEFAULT 0,
        table_count INTEGER NOT NULL DEFAULT 0,
        image_count INTEGER NOT NULL DEFAULT 0,
        page_count INTEGER NOT NULL DEFAULT 0,
        note TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT,
        FOREIGN KEY (document_id) REFERENCES documents(id)
    )
    """)

    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_document_digitization_document_version ON document_digitization(document_id, version_no)")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_document_digitization_document_status ON document_digitization(document_id, status)")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_document_digitization_created_at ON document_digitization(created_at)")

    cur.execute("PRAGMA table_info(document_digitization)")

    digitization_cols = {str(row[1]) for row in cur.fetchall()}

    if "source_sha256" not in digitization_cols:
        cur.execute("ALTER TABLE document_digitization ADD COLUMN source_sha256 TEXT")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_document_digitization_source_sha256_status ON document_digitization(source_sha256, status)")
