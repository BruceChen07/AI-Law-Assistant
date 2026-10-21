import os
from contextlib import contextmanager

from fastapi.testclient import TestClient

from app.main import app
from app.core.auth import create_user


client = TestClient(app)


@contextmanager
def temp_env(mapping):
    old = {}
    missing = object()
    for key, value in mapping.items():
        old[key] = os.environ.get(key, missing)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    try:
        yield
    finally:
        for key, value in old.items():
            if value is missing:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _admin_headers():
    username = "admin_llm_env_test"
    password = "adminpassword"
    login_resp = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    if login_resp.status_code != 200:
        try:
            create_user(username, "admin_llm_env_test@example.com",
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


def test_admin_llm_config_reports_env_status():
    headers = _admin_headers()
    with temp_env({
        # Pin the runtime to the env-managed cloud target so the assertions do
        # not depend on whether the checked-out config ships a local or cloud
        # runtime_mode.
        "LLM_RUNTIME_MODE": "cloud",
        "LLM_PROVIDER": "openai",
        "OPENAI_API_KEY": None,
        "LLM_API_KEY": None,
        "OPENAI_MODEL": "gpt-4o-mini",
        "OPENAI_BASE_URL": "https://api.openai.com/v1",
    }):
        resp = client.get("/api/admin/llm-config", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["provider"] == "openai"
    assert data["env_managed"] is True
    assert "OPENAI_API_KEY" in data["missing_env_vars"]
    assert data["env_sources"]["provider"] == "LLM_PROVIDER"


def test_admin_llm_config_update_is_blocked():
    headers = _admin_headers()
    resp = client.put(
        "/api/admin/llm-config",
        headers=headers,
        json={
            "provider": "openai",
            "api_base": "https://api.openai.com/v1",
            "model": "gpt-4o-mini",
            "temperature": 0.2,
            "max_tokens": 1024,
            "timeout": 30,
            "headers": {},
        },
    )
    assert resp.status_code == 405
    assert "environment-managed" in resp.json()["detail"]

