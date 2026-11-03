"""Migrations for Core identity and document tables: users, sessions, documents, audit_logs."""
from __future__ import annotations


def apply(cur) -> None:
    """Core identity and document tables: users, sessions, documents, audit_logs."""
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
