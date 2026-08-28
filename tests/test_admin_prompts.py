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
    if not prompt_id:
        return
    conn = get_conn(get_config())
    cur = conn.cursor()
    cur.execute("DELETE FROM audit_prompt WHERE id = ?", (prompt_id,))
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
                "description": "Prompt for admin CRUD test",
                "template_text": "Contract {{ contract_title }} for {{ counterparty }}",
                "sample_input_json": "{\"contract_title\": \"MSA\", \"counterparty\": \"Acme\"}",
            },
        )
        assert create_resp.status_code == 200
        created = create_resp.json()
        prompt_id = created["id"]
        assert created["status"] == "enabled"
        assert created["name"] == unique_name

        list_resp = client.get("/api/admin/prompts", headers=headers)
        assert list_resp.status_code == 200
        assert any(
            item["id"] == prompt_id for item in list_resp.json()["items"])

        preview_resp = client.post(
            "/api/admin/prompts/preview",
            headers=headers,
            json={
                "template_text": "Hello {{ user_name }}",
                "sample_input_json": "{\"user_name\": \"Alice\"}",
            },
        )
        assert preview_resp.status_code == 200
        assert preview_resp.json()["rendered_text"] == "Hello Alice"

        update_resp = client.put(
            f"/api/admin/prompts/{prompt_id}",
            headers=headers,
            json={
                "name": unique_name,
                "description": "Updated prompt",
                "template_text": "Updated {{ contract_title }}",
                "sample_input_json": "{\"contract_title\": \"NDA\"}",
            },
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["description"] == "Updated prompt"

        status_resp = client.put(
            f"/api/admin/prompts/{prompt_id}/status",
            headers=headers,
            json={"enabled": False},
        )
        assert status_resp.status_code == 200
        assert status_resp.json()["status"] == "disabled"

        detail_resp = client.get(
            f"/api/admin/prompts/{prompt_id}", headers=headers)
        assert detail_resp.status_code == 200
        assert detail_resp.json(
        )["template_text"] == "Updated {{ contract_title }}"

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
