import sqlite3


def get_conn(cfg):
    conn = sqlite3.connect(cfg["db_path"])
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
    cur.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)")
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
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id)")
    
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
    cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_user_id ON documents(user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_created_at ON documents(created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_category ON documents(category)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status)")
    
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
    cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_user_id ON audit_logs(user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs(created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_action ON audit_logs(action)")
    
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