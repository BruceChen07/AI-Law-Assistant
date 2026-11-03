from datetime import datetime
from uuid import uuid4

from app.core.config import get_config
from app.core.database import get_conn
from app.services.prompt_binding import resolve_bound_prompt_text


def _insert_prompt(template_text: str, status: str = "enabled") -> str:
    prompt_id = str(uuid4())
    now = datetime.utcnow().isoformat()
    conn = get_conn(get_config())
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO audit_prompt(id, name, description, template_text, sample_input_json, status, created_by, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            prompt_id,
            f"Runtime Prompt {uuid4().hex[:8]}",
            "Runtime prompt binding test",
            template_text,
            "{\"contract_clauses\": \"Clause A\"}",
            status,
            "pytest",
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()
    return prompt_id


def _bind_prompt(prompt_id: str):
    conn = get_conn(get_config())
    cur = conn.cursor()
    cur.execute("DELETE FROM audit_prompt_binding WHERE scene_key = ?",
                ("contract_audit.classic_json",))
    cur.execute(
        """
        INSERT INTO audit_prompt_binding(scene_key, prompt_id, updated_by, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        ("contract_audit.classic_json", prompt_id,
         "pytest", datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def _cleanup(prompt_id: str):
    conn = get_conn(get_config())
    cur = conn.cursor()
    cur.execute("DELETE FROM audit_prompt_binding WHERE scene_key = ?",
                ("contract_audit.classic_json",))
    if prompt_id:
        cur.execute("DELETE FROM audit_prompt WHERE id = ?", (prompt_id,))
    conn.commit()
    conn.close()


def test_resolve_bound_prompt_text_uses_bound_template():
    prompt_id = _insert_prompt("Bound prompt for {{ contract_clauses }}")
    try:
        _bind_prompt(prompt_id)
        result = resolve_bound_prompt_text(
            "contract_audit.classic_json",
            "fallback",
            {"contract_clauses": "Clause A"},
        )
        assert result["source"] == "bound_prompt"
        assert result["prompt_id"] == prompt_id
        assert result["rendered_text"] == "Bound prompt for Clause A"
    finally:
        _cleanup(prompt_id)


def test_resolve_bound_prompt_text_falls_back_on_render_error():
    prompt_id = _insert_prompt("Bound prompt for {{ missing_field }}")
    try:
        _bind_prompt(prompt_id)
        result = resolve_bound_prompt_text(
            "contract_audit.classic_json",
            "fallback",
            {"contract_clauses": "Clause A"},
        )
        assert result["source"] == "system_default"
        assert result["prompt_id"] == "contract_audit.classic_json"
        assert result["rendered_text"]
    finally:
        _cleanup(prompt_id)


def test_resolve_bound_prompt_text_uses_builtin_default_when_unbound():
    _cleanup("")
    result = resolve_bound_prompt_text(
        "contract_audit.classic_json",
        "fallback",
        {
            "lang": "zh",
            "whitelist_text": "-",
            "contract_clauses": "Clause A",
            "json_schema": "{\"summary\":\"\"}",
        },
    )
    assert result["source"] == "system_default"
    assert result["prompt_id"] == "contract_audit.classic_json"
    assert "合同条款" in result["rendered_text"]
