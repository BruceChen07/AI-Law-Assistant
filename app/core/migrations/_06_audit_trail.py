"""Migrations for Audit issues, trace records and evidence anchors."""
from __future__ import annotations


def apply(cur) -> None:
    """Audit issues, trace records and evidence anchors."""
    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit_issue(
        id TEXT PRIMARY KEY,
        contract_document_id TEXT NOT NULL,
        clause_id TEXT,
        rule_id TEXT,
        risk_level TEXT NOT NULL,
        issue_text TEXT NOT NULL,
        suggestion TEXT,
        reviewer_status TEXT NOT NULL DEFAULT 'pending',
        reviewer_note TEXT,
        created_by TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT,
        FOREIGN KEY (contract_document_id) REFERENCES contract_document(id),
        FOREIGN KEY (clause_id) REFERENCES contract_clause(id),
        FOREIGN KEY (rule_id) REFERENCES tax_rule(id)
    )
    """)

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_issue_contract_id ON audit_issue(contract_document_id)")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_issue_risk_level ON audit_issue(risk_level)")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit_trace(
        id TEXT PRIMARY KEY,
        issue_id TEXT NOT NULL,
        action_type TEXT NOT NULL,
        operator TEXT,
        payload_json TEXT,
        created_by TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT,
        FOREIGN KEY (issue_id) REFERENCES audit_issue(id)
    )
    """)

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_trace_issue_id ON audit_trace(issue_id)")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS evidence_anchor(
        id TEXT PRIMARY KEY,
        contract_document_id TEXT NOT NULL,
        issue_id TEXT,
        snapshot_hash TEXT NOT NULL,
        locator_type TEXT NOT NULL,
        start_offset INTEGER,
        end_offset INTEGER,
        page_no INTEGER,
        paragraph_no TEXT,
        clause_id TEXT,
        clause_path TEXT,
        quote_text TEXT NOT NULL,
        context_before TEXT,
        context_after TEXT,
        confidence REAL NOT NULL DEFAULT 0,
        is_stale INTEGER NOT NULL DEFAULT 0,
        created_by TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT,
        FOREIGN KEY (contract_document_id) REFERENCES contract_document(id),
        FOREIGN KEY (issue_id) REFERENCES audit_issue(id),
        FOREIGN KEY (clause_id) REFERENCES contract_clause(id)
    )
    """)

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_evidence_anchor_contract_issue ON evidence_anchor(contract_document_id, issue_id)")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_evidence_anchor_snapshot ON evidence_anchor(snapshot_hash)")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_evidence_anchor_page_para ON evidence_anchor(page_no, paragraph_no)")
