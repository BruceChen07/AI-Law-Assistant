"""Migrations for Audit runtime session / skill-run tables."""
from __future__ import annotations


def apply(cur) -> None:
    """Audit runtime session / skill-run tables."""
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
