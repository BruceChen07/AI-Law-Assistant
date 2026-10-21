"""Migrations for Prompt management tables and prompt column migrations."""
from __future__ import annotations


def apply(cur) -> None:
    """Prompt management tables and prompt column migrations."""
    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit_prompt(
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        prompt_key TEXT,
        description TEXT,
        system_text TEXT NOT NULL DEFAULT '',
        template_text TEXT NOT NULL,
        localized_system_texts TEXT NOT NULL DEFAULT '{}',
        localized_focus_texts TEXT NOT NULL DEFAULT '{}',
        localized_template_texts TEXT NOT NULL DEFAULT '{}',
        placeholder_map_json TEXT NOT NULL DEFAULT '{}',
        sample_input_json TEXT NOT NULL DEFAULT '{}',
        source_module TEXT NOT NULL DEFAULT '',
        managed_scope TEXT NOT NULL DEFAULT 'custom',
        status TEXT NOT NULL DEFAULT 'enabled',
        created_by TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """)

    cur.execute("PRAGMA table_info(audit_prompt)")

    prompt_columns = {str(row[1]) for row in cur.fetchall()}

    if "prompt_key" not in prompt_columns:
        cur.execute("ALTER TABLE audit_prompt ADD COLUMN prompt_key TEXT")

    if "system_text" not in prompt_columns:
        cur.execute(
            "ALTER TABLE audit_prompt ADD COLUMN system_text TEXT NOT NULL DEFAULT ''")

    if "localized_system_texts" not in prompt_columns:
        cur.execute(
            "ALTER TABLE audit_prompt ADD COLUMN localized_system_texts TEXT NOT NULL DEFAULT '{}'")

    if "localized_focus_texts" not in prompt_columns:
        cur.execute(
            "ALTER TABLE audit_prompt ADD COLUMN localized_focus_texts TEXT NOT NULL DEFAULT '{}'")

    if "localized_template_texts" not in prompt_columns:
        cur.execute(
            "ALTER TABLE audit_prompt ADD COLUMN localized_template_texts TEXT NOT NULL DEFAULT '{}'")

    if "placeholder_map_json" not in prompt_columns:
        cur.execute(
            "ALTER TABLE audit_prompt ADD COLUMN placeholder_map_json TEXT NOT NULL DEFAULT '{}'")

    if "source_module" not in prompt_columns:
        cur.execute(
            "ALTER TABLE audit_prompt ADD COLUMN source_module TEXT NOT NULL DEFAULT ''")

    if "managed_scope" not in prompt_columns:
        cur.execute(
            "ALTER TABLE audit_prompt ADD COLUMN managed_scope TEXT NOT NULL DEFAULT 'custom'")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_prompt_status ON audit_prompt(status)")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_prompt_updated_at ON audit_prompt(updated_at)")

    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_audit_prompt_prompt_key ON audit_prompt(prompt_key)")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_prompt_managed_scope ON audit_prompt(managed_scope)")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit_prompt_binding(
        scene_key TEXT PRIMARY KEY,
        prompt_id TEXT NOT NULL,
        updated_by TEXT,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (prompt_id) REFERENCES audit_prompt(id)
    )
    """)

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_prompt_binding_prompt_id ON audit_prompt_binding(prompt_id)")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_prompt_binding_updated_at ON audit_prompt_binding(updated_at)")
