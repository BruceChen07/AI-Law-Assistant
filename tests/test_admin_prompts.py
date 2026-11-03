from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.core.auth import create_user
from app.core.config import get_config
from app.core.database import get_conn


client = TestClient(app)


def _admin_headers():
    username = "admin_prompt_test"
    password = "adminpassword"
    login_resp = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    if login_resp.status_code != 200:
        try:
            create_user(username, "admin_prompt_test@example.com",
                        password, role="admin")
        except Exception:
            pass
        login_resp = client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
        )
    token = (login_resp.json() or {}).get("access_token")
    assert token
    return {"Authorization": f"Bearer {token}"}


def _cleanup_prompt(prompt_id: str):
    conn = get_conn(get_config())
    cur = conn.cursor()
    if prompt_id:
        cur.execute(
            "DELETE FROM audit_prompt_binding WHERE prompt_id = ?", (prompt_id,))
        cur.execute("DELETE FROM audit_prompt WHERE id = ?", (prompt_id,))
    cur.execute("DELETE FROM audit_prompt_binding WHERE scene_key = ?",
                ("contract_audit.classic_json",))
    conn.commit()
    conn.close()


def test_admin_prompt_crud_preview_and_status():
    headers = _admin_headers()
    prompt_id = ""
    unique_name = f"Prompt Admin {uuid4().hex[:8]}"
    try:
        create_resp = client.post(
            "/api/admin/prompts",
            headers=headers,
            json={
                "name": unique_name,
                "prompt_key": f"custom.prompt.{uuid4().hex[:8]}",
                "description": "Prompt for admin CRUD test",
                "editor_lang": "en",
                "display_system_text": "You are reviewing the contract summary.",
                "display_template_text": "Review the contract for [Counterparty] and return a short conclusion.",
                "sample_input_json": "{\"contract_title\": \"MSA\", \"counterparty\": \"Acme\"}",
            },
        )
        assert create_resp.status_code == 200
        created = create_resp.json()
        prompt_id = created["id"]
        assert created["status"] == "enabled"
        assert created["name"] == unique_name
        assert created["prompt_key"].startswith("custom.prompt.")
        assert created["display_system_text"] == "You are reviewing the contract summary."
        assert created["display_template_text"] == "Review the contract for [Counterparty] and return a short conclusion."

        list_resp = client.get("/api/admin/prompts", headers=headers)
        assert list_resp.status_code == 200
        assert any(
            item["id"] == prompt_id for item in list_resp.json()["items"])

        preview_resp = client.post(
            "/api/admin/prompts/preview",
            headers=headers,
            json={
                "editor_lang": "en",
                "display_system_text": "System role prompt.",
                "display_template_text": "Hello [User Name]",
                "sample_input_json": "{\"user_name\": \"Alice\"}",
            },
        )
        assert preview_resp.status_code == 200
        assert preview_resp.json(
        )["rendered_system_text"] == "System role prompt."
        assert preview_resp.json()["rendered_text"] == "Hello [User Name]"

        update_resp = client.put(
            f"/api/admin/prompts/{prompt_id}",
            headers=headers,
            json={
                "name": unique_name,
                "prompt_key": created["prompt_key"],
                "description": "Updated prompt",
                "editor_lang": "zh",
                "display_system_text": "你是一名合同审核助手。",
                "display_focus_text": "优先关注付款与发票约定。",
                "display_template_text": "请审阅合同并给出结论。合同标题：【合同标题】",
                "sample_input_json": "{\"contract_title\": \"NDA\"}",
            },
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["description"] == "Updated prompt"
        assert update_resp.json()["display_system_text"] == "你是一名合同审核助手。"
        assert update_resp.json()["display_focus_text"] == "优先关注付款与发票约定。"
        assert update_resp.json(
        )["display_template_text"] == "请审阅合同并给出结论。合同标题：【合同标题】"

        status_resp = client.put(
            f"/api/admin/prompts/{prompt_id}/status",
            headers=headers,
            json={"enabled": False},
        )
        assert status_resp.status_code == 200
        assert status_resp.json()["status"] == "disabled"

        detail_resp = client.get(
            f"/api/admin/prompts/{prompt_id}?editor_lang=zh", headers=headers)
        assert detail_resp.status_code == 200
        assert detail_resp.json(
        )["display_template_text"] == "请审阅合同并给出结论。合同标题：【合同标题】"
        assert "{%" not in detail_resp.json()["display_template_text"]
        assert "{{" not in detail_resp.json()["display_template_text"]

        delete_resp = client.delete(
            f"/api/admin/prompts/{prompt_id}", headers=headers)
        assert delete_resp.status_code == 200
        prompt_id = ""

        missing_resp = client.get(
            f"/api/admin/prompts/{created['id']}", headers=headers)
        assert missing_resp.status_code == 404
    finally:
        _cleanup_prompt(prompt_id)


def test_admin_prompt_rejects_invalid_json_input():
    headers = _admin_headers()
    resp = client.post(
        "/api/admin/prompts",
        headers=headers,
        json={
            "name": f"Prompt Invalid {uuid4().hex[:8]}",
            "description": "Invalid JSON payload",
            "template_text": "Hello {{ user_name }}",
            "sample_input_json": "{\"user_name\": ",
        },
    )
    assert resp.status_code == 400
    assert "sample_input_json" in resp.json()["detail"]


def test_admin_prompt_lists_builtin_prompts_from_code():
    headers = _admin_headers()
    _cleanup_prompt("")
    list_resp = client.get("/api/admin/prompts", headers=headers)
    assert list_resp.status_code == 200
    items = list_resp.json()["items"]
    system_ids = {item["id"] for item in items if item["source"] == "system"}
    assert "contract_audit.classic_json" in system_ids
    assert "audit_prompt.tax_audit_json" in system_ids

    detail_resp = client.get(
        "/api/admin/prompts/audit_prompt.tax_audit_json",
        headers=headers,
    )
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["readonly"] is False
    assert detail["source"] == "system"
    assert detail["managed_scope"] == "system"
    assert "app.services.audit_prompt._build_prompt" in detail["source_module"]
    assert "合同内容" in detail["display_template_text"]
    assert "{{" not in detail["display_template_text"]
    assert "{%" not in detail["display_template_text"]

    detail_en_resp = client.get(
        "/api/admin/prompts/audit_prompt.tax_audit_json?editor_lang=en",
        headers=headers,
    )
    assert detail_en_resp.status_code == 200
    detail_en = detail_en_resp.json()
    assert "Contract Content" in detail_en["display_template_text"]
    assert detail_en["display_template_text"] != detail["display_template_text"]
    assert "{{" not in detail_en["display_template_text"]
    assert "{%" not in detail_en["display_template_text"]


def test_admin_prompt_binding_tracks_current_effective_prompt():
    headers = _admin_headers()
    prompt_id = ""
    unique_name = f"Prompt Binding {uuid4().hex[:8]}"
    try:
        create_resp = client.post(
            "/api/admin/prompts",
            headers=headers,
            json={
                "name": unique_name,
                "description": "Prompt for binding flow",
                "editor_lang": "en",
                "display_template_text": "Audit the contract clauses and summarize the main risks.",
                "sample_input_json": "{\"contract_clauses\": \"Clause A\"}",
            },
        )
        assert create_resp.status_code == 200
        prompt_id = create_resp.json()["id"]

        bindings_resp = client.get(
            "/api/admin/prompt-bindings", headers=headers)
        assert bindings_resp.status_code == 200
        before_items = bindings_resp.json()["items"]
        assert before_items[0]["scene_key"] == "contract_audit.classic_json"
        assert before_items[0]["active_source"] == "system_default"
        assert before_items[0]["active_prompt"]["id"] == "contract_audit.classic_json"

        bind_resp = client.put(
            "/api/admin/prompt-bindings/contract_audit.classic_json",
            headers=headers,
            json={"prompt_id": prompt_id},
        )
        assert bind_resp.status_code == 200
        bound = bind_resp.json()
        assert bound["active_source"] == "bound_prompt"
        assert bound["active_prompt"]["id"] == prompt_id

        list_resp = client.get("/api/admin/prompts", headers=headers)
        assert list_resp.status_code == 200
        prompt_item = next(item for item in list_resp.json()[
                           "items"] if item["id"] == prompt_id)
        assert "contract_audit.classic_json" in prompt_item["binding_scene_keys"]

        disable_resp = client.put(
            f"/api/admin/prompts/{prompt_id}/status",
            headers=headers,
            json={"enabled": False},
        )
        assert disable_resp.status_code == 200

        after_disable_resp = client.get(
            "/api/admin/prompt-bindings", headers=headers)
        assert after_disable_resp.status_code == 200
        after_disable = after_disable_resp.json()["items"][0]
        assert after_disable["active_source"] == "system_default"
        assert after_disable["active_prompt"]["id"] == "contract_audit.classic_json"
    finally:
        _cleanup_prompt(prompt_id)


def test_admin_system_prompt_can_restore_default_but_not_delete_or_disable():
    headers = _admin_headers()
    prompt_id = "audit_prompt.tax_audit_json"

    detail_resp = client.get(
        f"/api/admin/prompts/{prompt_id}", headers=headers)
    assert detail_resp.status_code == 200
    original = detail_resp.json()

    update_resp = client.put(
        f"/api/admin/prompts/{prompt_id}",
        headers=headers,
        json={
            "name": original["name"],
            "prompt_key": original["prompt_key"],
            "description": "Edited system prompt",
            "editor_lang": "en",
            "display_system_text": "You are the restored audit assistant.",
            "display_template_text": "Review the contract content and return strict JSON.",
            "sample_input_json": "{\"lang\": \"zh\", \"contract_text\": \"abc\"}",
        },
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["description"] == "Edited system prompt"

    disable_resp = client.put(
        f"/api/admin/prompts/{prompt_id}/status",
        headers=headers,
        json={"enabled": False},
    )
    assert disable_resp.status_code == 400

    delete_resp = client.delete(
        f"/api/admin/prompts/{prompt_id}", headers=headers)
    assert delete_resp.status_code == 400

    restore_resp = client.post(
        f"/api/admin/prompts/{prompt_id}/restore-default",
        headers=headers,
    )
    assert restore_resp.status_code == 200
    restored = restore_resp.json()
    assert restored["description"] == original["description"]
    assert restored["display_template_text"] == original["display_template_text"]


def test_admin_prompt_extracts_business_text_from_legacy_runtime_template():
    headers = _admin_headers()
    prompt_id = str(uuid4())
    conn = get_conn(get_config())
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO audit_prompt(
                id, name, prompt_key, description, system_text, template_text,
                localized_system_texts, localized_focus_texts, localized_template_texts,
                placeholder_map_json, sample_input_json, source_module, managed_scope, status,
                created_by, created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                prompt_id,
                f"Legacy Prompt {uuid4().hex[:8]}",
                f"legacy.prompt.{uuid4().hex[:8]}",
                "Legacy prompt migration case",
                "{% if lang == 'en' %}You are a contract audit assistant. Output strict JSON only.{% else %}你是合同审计助手，仅输出严格 JSON。{% endif %}",
                "{% if lang == 'en' %}{% if tax_focus %}Focus on tax clauses. {% endif %}Review the contract. Evidence:\\n{{ evidence_block }}\\nContract:\\n{{ contract_text }}{% else %}{% if tax_focus %}优先关注涉税条款。{% endif %}请审阅合同。法规证据：\\n{{ evidence_block }}\\n合同内容：\\n{{ contract_text }}{% endif %}",
                "{}",
                "{}",
                "{}",
                "{\"evidence_block\":{\"zh\":\"【法规证据列表】\",\"en\":\"[Regulation Evidence List]\"},\"contract_text\":{\"zh\":\"【合同内容】\",\"en\":\"[Contract Content]\"}}",
                "{}",
                "",
                "custom",
                "enabled",
                "test",
                "2026-09-15T00:00:00",
                "2026-09-15T00:00:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()
    try:
        zh_resp = client.get(
            f"/api/admin/prompts/{prompt_id}?editor_lang=zh", headers=headers)
        assert zh_resp.status_code == 200
        zh_detail = zh_resp.json()
        assert zh_detail["display_focus_text"] == "优先关注涉税条款。"
        assert "【法规证据列表】" in zh_detail["display_template_text"]
        assert "【合同内容】" in zh_detail["display_template_text"]
        assert "{{" not in zh_detail["display_template_text"]
        assert "{%" not in zh_detail["display_template_text"]

        en_resp = client.get(
            f"/api/admin/prompts/{prompt_id}?editor_lang=en", headers=headers)
        assert en_resp.status_code == 200
        en_detail = en_resp.json()
        assert en_detail["display_focus_text"] == "Focus on tax clauses."
        assert "[Regulation Evidence List]" in en_detail["display_template_text"]
        assert "[Contract Content]" in en_detail["display_template_text"]
        assert "{{" not in en_detail["display_template_text"]
        assert "{%" not in en_detail["display_template_text"]
    finally:
        _cleanup_prompt(prompt_id)
