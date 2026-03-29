import sqlite3


def get_conn(cfg):
    conn = sqlite3.connect(cfg["db_path"])
    conn.row_factory = sqlite3.Row
    return conn


def _normalize_lang_tag(lang: str, default: str = "zh") -> str:
    s = str(lang or "").strip().lower()
    if s.startswith("en"):
        return "en"
    if s.startswith("zh"):
        return "zh"
    return default


def get_rag_db_path(cfg, lang: str):
    paths = cfg.get("rag_db_paths") if isinstance(cfg.get("rag_db_paths"), dict) else {}
    norm_lang = _normalize_lang_tag(lang, default="zh")
    p = str(paths.get(norm_lang) or "").strip()
    if p:
        return p
    fallback = str(cfg.get("db_path") or "").strip()
    return fallback


def get_rag_conn(cfg, lang: str):
    path = get_rag_db_path(cfg, lang)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(cfg):
    conn = get_conn(cfg)
    cur = conn.cursor()

    # Users table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users(
        id TEXT PRIMARY KEY,
        username TEXT UNIQUE NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT DEFAULT 'user',
        created_at TEXT NOT NULL,
        updated_at TEXT,
        is_active INTEGER DEFAULT 1
    )
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_users_role ON users(role)")

    # Sessions table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sessions(
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        token TEXT UNIQUE NOT NULL,
        ip_address TEXT,
        user_agent TEXT,
        expires_at TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token)")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id)")

    # Documents table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS documents(
        id TEXT PRIMARY KEY,
        filename TEXT NOT NULL,
        original_filename TEXT NOT NULL,
        file_path TEXT NOT NULL,
        file_size INTEGER NOT NULL,
        mime_type TEXT,
        file_hash TEXT,
        user_id TEXT NOT NULL,
        regulation_id TEXT,
        title TEXT,
        description TEXT,
        tags TEXT,
        category TEXT,
        status TEXT DEFAULT 'active',
        created_at TEXT NOT NULL,
        updated_at TEXT,
        deleted_at TEXT,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_documents_user_id ON documents(user_id)")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_documents_created_at ON documents(created_at)")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_documents_category ON documents(category)")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status)")

    # Audit logs table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit_logs(
        id TEXT PRIMARY KEY,
        user_id TEXT,
        action TEXT NOT NULL,
        resource_type TEXT,
        resource_id TEXT,
        ip_address TEXT,
        user_agent TEXT,
        details TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_logs_user_id ON audit_logs(user_id)")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs(created_at)")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_logs_action ON audit_logs(action)")

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

    # Existing tables
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
