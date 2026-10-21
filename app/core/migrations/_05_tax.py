"""Migrations for Tax rules, contract clauses and clause-rule matching."""
from __future__ import annotations


def apply(cur) -> None:
    """Tax rules, contract clauses and clause-rule matching."""
    cur.execute("""
    CREATE TABLE IF NOT EXISTS tax_rule(
        id TEXT PRIMARY KEY,
        regulation_document_id TEXT NOT NULL,
        law_title TEXT,
        article_no TEXT,
        rule_type TEXT,
        trigger_condition TEXT,
        required_action TEXT,
        prohibited_action TEXT,
        numeric_constraints TEXT,
        deadline_constraints TEXT,
        region TEXT,
        industry TEXT,
        effective_date TEXT,
        expiry_date TEXT,
        source_page INTEGER,
        source_paragraph TEXT,
        source_text TEXT,
        created_by TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT,
        FOREIGN KEY (regulation_document_id) REFERENCES regulation_document(id)
    )
    """)

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_tax_rule_doc_id ON tax_rule(regulation_document_id)")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_tax_rule_rule_type ON tax_rule(rule_type)")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS contract_document(
        id TEXT PRIMARY KEY,
        original_filename TEXT NOT NULL,
        file_path TEXT NOT NULL,
        file_type TEXT NOT NULL,
        file_size INTEGER NOT NULL,
        parse_status TEXT NOT NULL DEFAULT 'pending',
        ocr_used INTEGER NOT NULL DEFAULT 0,
        uploaded_by TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT
    )
    """)

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_contract_document_created_at ON contract_document(created_at)")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS contract_clause(
        id TEXT PRIMARY KEY,
        contract_document_id TEXT NOT NULL,
        clause_path TEXT,
        page_no INTEGER,
        paragraph_no TEXT,
        clause_text TEXT NOT NULL,
        entities_json TEXT,
        created_by TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT,
        FOREIGN KEY (contract_document_id) REFERENCES contract_document(id)
    )
    """)

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_contract_clause_contract_id ON contract_clause(contract_document_id)")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS clause_rule_match(
        id TEXT PRIMARY KEY,
        clause_id TEXT NOT NULL,
        rule_id TEXT NOT NULL,
        match_score REAL NOT NULL DEFAULT 0,
        match_label TEXT NOT NULL,
        evidence_json TEXT,
        created_by TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT,
        FOREIGN KEY (clause_id) REFERENCES contract_clause(id),
        FOREIGN KEY (rule_id) REFERENCES tax_rule(id)
    )
    """)

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_clause_rule_match_clause_id ON clause_rule_match(clause_id)")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_clause_rule_match_rule_id ON clause_rule_match(rule_id)")
