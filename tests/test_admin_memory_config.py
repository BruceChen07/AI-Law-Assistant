from copy import deepcopy

from fastapi.testclient import TestClient

from app.main import app
from app.core.config import get_config, update_config_patch
from app.core.auth import create_user


client = TestClient(app)


def _admin_headers():
    username = "admin_mem_cfg_test"
    password = "adminpassword"
    login_resp = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    if login_resp.status_code != 200:
        try:
            create_user(username, "admin_mem_cfg_test@example.com",
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


def test_admin_memory_config_get_and_update():
    headers = _admin_headers()
    cfg_before = get_config()
    old_mem_cfg = deepcopy(cfg_before.get("memory_runtime_config"))
    try:
        get_resp = client.get("/api/admin/memory-config", headers=headers)
        assert get_resp.status_code == 200
        payload = get_resp.json()
        assert "memory_module_enabled" in payload
        assert "risk_notice" in payload
        assert payload["memory_mode_when_disabled"] == "classic"

        update_resp = client.put(
            "/api/admin/memory-config",
            headers=headers,
            json={
                "memory_module_enabled": False,
                "memory_token_guard_enabled": True,
                "memory_max_llm_calls_per_audit": 9,
                "memory_max_prompt_chars_per_clause": 1800,
            },
        )
        assert update_resp.status_code == 200
        data = update_resp.json()
        assert data["memory_module_enabled"] is False
        assert data["memory_token_guard_enabled"] is True
        assert int(data["memory_max_llm_calls_per_audit"]) == 9
        assert int(data["memory_max_prompt_chars_per_clause"]) == 1800

        verify_resp = client.get("/api/admin/memory-config", headers=headers)
        assert verify_resp.status_code == 200
        verify_data = verify_resp.json()
        assert verify_data["memory_module_enabled"] is False
    finally:
        restore = old_mem_cfg if isinstance(old_mem_cfg, dict) else {}
        update_config_patch({"memory_runtime_config": restore})
