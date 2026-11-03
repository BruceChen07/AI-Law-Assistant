"""Migrations for Report export jobs, snapshots, archive and cleanup jobs."""
from __future__ import annotations


def apply(cur) -> None:
    """Report export jobs, snapshots, archive and cleanup jobs."""
    cur.execute("""
    CREATE TABLE IF NOT EXISTS export_job(
        id TEXT PRIMARY KEY,
        export_id TEXT NOT NULL UNIQUE,
        contract_document_id TEXT NOT NULL,
        requester TEXT NOT NULL,
        export_format TEXT NOT NULL,
        template_version TEXT NOT NULL,
        locale TEXT NOT NULL DEFAULT 'zh-CN',
        include_appendix INTEGER NOT NULL DEFAULT 1,
        idempotency_key TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL,
        progress INTEGER NOT NULL DEFAULT 0,
        error_message TEXT,
        output_path TEXT,
        output_sha256 TEXT,
        created_by TEXT,
        created_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT,
        updated_at TEXT,
        FOREIGN KEY (contract_document_id) REFERENCES contract_document(id)
    )
    """)

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_export_job_contract_status ON export_job(contract_document_id, status)")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS export_snapshot(
        id TEXT PRIMARY KEY,
        export_job_id TEXT NOT NULL,
        snapshot_hash TEXT NOT NULL,
        data_manifest_json TEXT NOT NULL,
        created_by TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT,
        FOREIGN KEY (export_job_id) REFERENCES export_job(id)
    )
    """)

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_export_snapshot_job ON export_snapshot(export_job_id)")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_export_snapshot_hash ON export_snapshot(snapshot_hash)")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS tax_audit_archive_record(
        id TEXT PRIMARY KEY,
        contract_document_id TEXT NOT NULL UNIQUE,
        archive_path TEXT NOT NULL,
        archived_at TEXT NOT NULL,
        archived_by TEXT,
        source_job_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT,
        FOREIGN KEY (contract_document_id) REFERENCES contract_document(id)
    )
    """)

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_tax_archive_contract_id ON tax_audit_archive_record(contract_document_id)")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_tax_archive_archived_at ON tax_audit_archive_record(archived_at)")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS tax_audit_cleanup_job(
        id TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        retention_days INTEGER NOT NULL,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        archived_contracts INTEGER NOT NULL DEFAULT 0,
        deleted_files INTEGER NOT NULL DEFAULT 0,
        details_json TEXT,
        error TEXT,
        created_by TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT
    )
    """)

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_tax_cleanup_status ON tax_audit_cleanup_job(status)")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_tax_cleanup_started_at ON tax_audit_cleanup_job(started_at)")
