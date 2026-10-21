"""Migrations for Audit skills, skill files/versions, rule packs and templates."""
from __future__ import annotations


def apply(cur) -> None:
    """Audit skills, skill files/versions, rule packs and templates."""
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

    cur.execute("PRAGMA table_info(audit_skill)")

    skill_columns = {str(row[1]) for row in cur.fetchall()}

    skill_extra_columns = [
        ("publisher_name", "TEXT"),
        ("publisher_handle", "TEXT"),
        ("install_command", "TEXT"),
        ("skill_md_text", "TEXT"),
        ("skill_card_json", "TEXT"),
        ("current_version", "TEXT"),
        ("license_name", "TEXT"),
        ("security_audit_status", "TEXT"),
        ("downloads_30d", "INTEGER NOT NULL DEFAULT 0"),
        ("downloads_all_time", "INTEGER NOT NULL DEFAULT 0"),
        ("last_published_at", "TEXT"),
    ]

    for col_name, col_type in skill_extra_columns:
        if col_name not in skill_columns:
            cur.execute(f"ALTER TABLE audit_skill ADD COLUMN {col_name} {col_type}")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit_skill_file(
        id TEXT PRIMARY KEY,
        skill_id TEXT NOT NULL,
        path TEXT NOT NULL,
        entry_type TEXT NOT NULL DEFAULT 'file',
        size_bytes INTEGER NOT NULL DEFAULT 0,
        branch_name TEXT,
        content_text TEXT,
        sort_order INTEGER NOT NULL DEFAULT 100,
        created_at TEXT NOT NULL,
        updated_at TEXT,
        FOREIGN KEY (skill_id) REFERENCES audit_skill(id)
    )
    """)

    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_audit_skill_file_skill_path ON audit_skill_file(skill_id, path)")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_skill_file_skill_sort ON audit_skill_file(skill_id, sort_order)")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit_skill_version(
        id TEXT PRIMARY KEY,
        skill_id TEXT NOT NULL,
        version_tag TEXT NOT NULL,
        release_label TEXT,
        published_at TEXT,
        is_latest INTEGER NOT NULL DEFAULT 0,
        download_url TEXT,
        changelog_text TEXT,
        changelog_json TEXT,
        sort_order INTEGER NOT NULL DEFAULT 100,
        created_at TEXT NOT NULL,
        updated_at TEXT,
        FOREIGN KEY (skill_id) REFERENCES audit_skill(id)
    )
    """)

    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_audit_skill_version_skill_tag ON audit_skill_version(skill_id, version_tag)")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_skill_version_skill_sort ON audit_skill_version(skill_id, sort_order)")

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
