import sqlite3


def get_conn(cfg):
    conn = sqlite3.connect(cfg["db_path"], timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _normalize_lang_tag(lang: str, default: str = "zh") -> str:
    s = str(lang or "").strip().lower()
    if s.startswith("en"):
        return "en"
    if s.startswith("zh"):
        return "zh"
    return default


def get_rag_db_path(cfg, lang: str):
    paths = cfg.get("rag_db_paths") if isinstance(
        cfg.get("rag_db_paths"), dict) else {}
    norm_lang = _normalize_lang_tag(lang, default="zh")
    p = str(paths.get(norm_lang) or "").strip()
    if p:
        return p
    fallback = str(cfg.get("db_path") or "").strip()
    return fallback


def get_rag_conn(cfg, lang: str):
    path = get_rag_db_path(cfg, lang)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
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

    # Vector store configuration
    cur.execute("""
    CREATE TABLE IF NOT EXISTS vector_store_config(
        id INTEGER PRIMARY KEY CHECK (id = 1),
        engine TEXT NOT NULL DEFAULT 'sqlite',
        updated_at TEXT NOT NULL
    )
    """)
    # Insert default config if not exists
    cur.execute(
        "INSERT OR IGNORE INTO vector_store_config (id, engine, updated_at) VALUES (1, 'sqlite', CURRENT_TIMESTAMP)")

    # Upload cache log
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

    # Audit capability registry: skills / rule packs / templates
    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit_skill(
        id TEXT PRIMARY KEY,
        display_name TEXT NOT NULL,
        category TEXT NOT NULL,
        scene TEXT NOT NULL,
        description TEXT,
        owner_type TEXT NOT NULL DEFAULT 'system',
        owner_id TEXT,
        visibility TEXT NOT NULL DEFAULT 'public',
        status TEXT NOT NULL DEFAULT 'active',
        source_url TEXT,
        reference_summary TEXT,
        input_schema_json TEXT,
        output_schema_json TEXT,
        config_schema_json TEXT,
        tags_json TEXT,
        sort_order INTEGER NOT NULL DEFAULT 100,
        created_at TEXT NOT NULL,
        updated_at TEXT
    )
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_skill_scene_status ON audit_skill(scene, status)")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_skill_category ON audit_skill(category)")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit_rule_pack(
        id TEXT PRIMARY KEY,
        display_name TEXT NOT NULL,
        scene TEXT NOT NULL,
        description TEXT,
        owner_type TEXT NOT NULL DEFAULT 'system',
        owner_id TEXT,
        visibility TEXT NOT NULL DEFAULT 'public',
        status TEXT NOT NULL DEFAULT 'active',
        selector_json TEXT,
        source_note TEXT,
        sort_order INTEGER NOT NULL DEFAULT 100,
        created_at TEXT NOT NULL,
        updated_at TEXT
    )
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_rule_pack_scene_status ON audit_rule_pack(scene, status)")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit_rule_pack_draft(
        id TEXT PRIMARY KEY,
        base_pack_id TEXT NOT NULL,
        owner_id TEXT NOT NULL,
        display_name TEXT NOT NULL,
        scene TEXT NOT NULL,
        description TEXT,
        selector_json TEXT NOT NULL,
        source_note TEXT,
        change_summary TEXT,
        status TEXT NOT NULL DEFAULT 'draft',
        review_comment TEXT,
        submitted_at TEXT,
        reviewed_by TEXT,
        reviewed_at TEXT,
        published_version_no INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT,
        FOREIGN KEY (base_pack_id) REFERENCES audit_rule_pack(id),
        FOREIGN KEY (owner_id) REFERENCES users(id)
    )
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_rule_pack_draft_owner_status ON audit_rule_pack_draft(owner_id, status)")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_rule_pack_draft_base_status ON audit_rule_pack_draft(base_pack_id, status)")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit_rule_pack_version(
        id TEXT PRIMARY KEY,
        pack_id TEXT NOT NULL,
        draft_id TEXT,
        version_no INTEGER NOT NULL,
        display_name TEXT NOT NULL,
        scene TEXT NOT NULL,
        description TEXT,
        selector_json TEXT NOT NULL,
        source_note TEXT,
        change_summary TEXT,
        published_by TEXT,
        published_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT,
        FOREIGN KEY (pack_id) REFERENCES audit_rule_pack(id),
        FOREIGN KEY (draft_id) REFERENCES audit_rule_pack_draft(id)
    )
    """)
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_audit_rule_pack_version_pack_no ON audit_rule_pack_version(pack_id, version_no)")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_rule_pack_version_pack_published ON audit_rule_pack_version(pack_id, published_at)")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit_template(
        id TEXT PRIMARY KEY,
        display_name TEXT NOT NULL,
        scene TEXT NOT NULL,
        description TEXT,
        owner_type TEXT NOT NULL DEFAULT 'system',
        owner_id TEXT,
        visibility TEXT NOT NULL DEFAULT 'public',
        status TEXT NOT NULL DEFAULT 'active',
        skill_ids_json TEXT,
        rule_pack_ids_json TEXT,
        max_llm_steps INTEGER NOT NULL DEFAULT 2,
        max_skills_per_run INTEGER NOT NULL DEFAULT 6,
        sort_order INTEGER NOT NULL DEFAULT 100,
        created_at TEXT NOT NULL,
        updated_at TEXT
    )
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_template_scene_status ON audit_template(scene, status)")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS agent_profile(
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        display_name TEXT NOT NULL,
        description TEXT,
        scene TEXT NOT NULL,
        template_id TEXT,
        enabled_skill_ids_json TEXT NOT NULL,
        enabled_rule_pack_ids_json TEXT NOT NULL,
        system_prompt TEXT,
        max_llm_steps INTEGER NOT NULL DEFAULT 2,
        max_skills_per_run INTEGER NOT NULL DEFAULT 6,
        status TEXT NOT NULL DEFAULT 'active',
        created_at TEXT NOT NULL,
        updated_at TEXT,
        FOREIGN KEY (owner_id) REFERENCES users(id),
        FOREIGN KEY (template_id) REFERENCES audit_template(id)
    )
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_profile_owner_status ON agent_profile(owner_id, status)")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_profile_scene ON agent_profile(scene)")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit_runtime_session(
        id TEXT PRIMARY KEY,
        contract_document_id TEXT NOT NULL,
        owner_id TEXT NOT NULL,
        operator_id TEXT,
        agent_profile_id TEXT,
        template_id TEXT,
        replay_of_session_id TEXT,
        sandbox_mode TEXT NOT NULL DEFAULT 'builtin_only',
        status TEXT NOT NULL DEFAULT 'running',
        request_json TEXT NOT NULL,
        runtime_json TEXT,
        result_json TEXT,
        error_message TEXT,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT,
        FOREIGN KEY (contract_document_id) REFERENCES contract_document(id),
        FOREIGN KEY (owner_id) REFERENCES users(id),
        FOREIGN KEY (agent_profile_id) REFERENCES agent_profile(id),
        FOREIGN KEY (replay_of_session_id) REFERENCES audit_runtime_session(id)
    )
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_runtime_session_contract_started ON audit_runtime_session(contract_document_id, started_at)")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_runtime_session_owner_started ON audit_runtime_session(owner_id, started_at)")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_runtime_session_replay_of ON audit_runtime_session(replay_of_session_id)")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit_runtime_skill_run(
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        skill_id TEXT NOT NULL,
        stage TEXT NOT NULL,
        sandbox_mode TEXT NOT NULL DEFAULT 'builtin_only',
        status TEXT NOT NULL,
        llm_cost INTEGER NOT NULL DEFAULT 0,
        position_no INTEGER NOT NULL DEFAULT 0,
        reason TEXT,
        input_summary_json TEXT,
        output_summary_json TEXT,
        error_message TEXT,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT,
        FOREIGN KEY (session_id) REFERENCES audit_runtime_session(id)
    )
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_runtime_skill_run_session_position ON audit_runtime_skill_run(session_id, position_no)")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_runtime_skill_run_session_status ON audit_runtime_skill_run(session_id, status)")

    conn.commit()
    conn.close()


def ensure_article_dsl_columns(cfg):
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(article)")
    cols = {r[1] for r in cur.fetchall()}
    if "rule_type" not in cols:
        cur.execute("ALTER TABLE article ADD COLUMN rule_type TEXT")
    if "conditions_json" not in cols:
        cur.execute("ALTER TABLE article ADD COLUMN conditions_json TEXT")
    if "constraints_json" not in cols:
        cur.execute("ALTER TABLE article ADD COLUMN constraints_json TEXT")
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
