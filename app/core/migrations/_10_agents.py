"""Migrations for Agent profiles."""
from __future__ import annotations


def apply(cur) -> None:
    """Agent profiles."""
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
