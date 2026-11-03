from copy import deepcopy
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.core.auth import create_user
from app.core.config import get_config, update_config_patch
from app.api.routers import admin as admin_router


client = TestClient(app)


def _admin_headers():
    username = "admin_llamacpp_cfg_test"
    password = "adminpassword"
    login_resp = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    if login_resp.status_code != 200:
        try:
            create_user(username, "admin_llamacpp_cfg_test@example.com",
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


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_admin_llamacpp_models_endpoint_uses_cache():
    headers = _admin_headers()
    payload = {
        "data": [
            {
                "id": "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
                "owned_by": "local-edge",
                "metadata": {
                    "context_length": 8192,
                    "size_bytes": 123456,
                    "family": "qwen3",
                    "quantization": "Q4_K_M",
                },
            }
        ]
    }
    old_cache = deepcopy(admin_router._LLAMACPP_MODELS_CACHE)
    admin_router._LLAMACPP_MODELS_CACHE.update(
        {"host": "", "ts": 0.0, "models": [], "error": ""}
    )
    try:
        with patch("app.api.routers.admin.httpx.get", return_value=_FakeResponse(payload)) as mocked_get:
            first = client.get("/api/admin/llama-cpp/models", headers=headers)
            second = client.get("/api/admin/llama-cpp/models", headers=headers)
        assert first.status_code == 200
        assert second.status_code == 200
        first_data = first.json()
        second_data = second.json()
        assert first_data["models"][0]["name"] == "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
        assert first_data["cached"] is False
        assert second_data["cached"] is True
        assert mocked_get.call_count == 1
    finally:
        admin_router._LLAMACPP_MODELS_CACHE.clear()
        admin_router._LLAMACPP_MODELS_CACHE.update(old_cache)


def test_admin_llamacpp_models_endpoint_reports_unreachable_host():
    headers = _admin_headers()
    old_cache = deepcopy(admin_router._LLAMACPP_MODELS_CACHE)
    admin_router._LLAMACPP_MODELS_CACHE.update(
        {"host": "", "ts": 0.0, "models": [], "error": ""}
    )
    try:
        with patch("app.api.routers.admin.httpx.get", side_effect=RuntimeError("[WinError 10061] connection refused")):
            resp = client.get(
                "/api/admin/llama-cpp/models?force_refresh=true", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["reachable"] is False
        assert data["models"] == []
        assert "10061" in data["error"]
    finally:
        admin_router._LLAMACPP_MODELS_CACHE.clear()
        admin_router._LLAMACPP_MODELS_CACHE.update(old_cache)


def test_admin_update_llm_config_syncs_local_main_model_for_llamacpp():
    headers = _admin_headers()
    cfg_before = get_config()
    old_llm_cfg = deepcopy(cfg_before.get("llm_config"))
    old_local_cfg = deepcopy(cfg_before.get("local_llm"))
    try:
        resp = client.put(
            "/api/admin/llm-config",
            headers=headers,
            json={
                "provider": "llama_cpp",
                "api_base": "http://127.0.0.1:18080/v1",
                "model": "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
                "temperature": 0.2,
                "max_tokens": 2048,
                "timeout": 600,
                "headers": {},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["provider"] == "llama_cpp"
        assert data["model"] == "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
        assert data["config_source"] == "local_main_model"

        cfg_after = get_config()
        assert cfg_after["llm_config"]["provider"] == "llama_cpp"
        assert cfg_after["local_llm"]["main_model"]["provider"] == "llama_cpp"
        assert cfg_after["local_llm"]["main_model"]["api_base"] == "http://127.0.0.1:18080/v1"
    finally:
        update_config_patch({
            "llm_config": old_llm_cfg if isinstance(old_llm_cfg, dict) else {},
            "local_llm": old_local_cfg if isinstance(old_local_cfg, dict) else {},
        })


def test_admin_update_llm_config_rejects_non_llamacpp_provider():
    headers = _admin_headers()
    cfg_before = get_config()
    old_llm_cfg = deepcopy(cfg_before.get("llm_config"))
    old_local_cfg = deepcopy(cfg_before.get("local_llm"))
    try:
        resp = client.put(
            "/api/admin/llm-config",
            headers=headers,
            json={
                "provider": "ollama",
                "api_base": "http://127.0.0.1:11434/v1",
                "model": "qwen3:4b",
                "temperature": 0.1,
                "max_tokens": 1024,
                "timeout": 45,
                "headers": {},
            },
        )
        assert resp.status_code == 400
        assert "llama_cpp" in resp.json()["detail"]
    finally:
        update_config_patch({
            "llm_config": old_llm_cfg if isinstance(old_llm_cfg, dict) else {},
            "local_llm": old_local_cfg if isinstance(old_local_cfg, dict) else {},
        })


def _activate_llamacpp_runtime(model: str, timeout: int = 5) -> tuple[dict, dict, dict]:
    cfg_before = get_config()
    old_llm_cfg = deepcopy(cfg_before.get("llm_config"))
    old_local_cfg = deepcopy(cfg_before.get("local_llm"))
    old_runtime_cfg = deepcopy(getattr(app.state.llm, "cfg", {}))
    target = {
        "provider": "llama_cpp",
        "api_base": "http://127.0.0.1:18080/v1",
        "api_key": "",
        "model": model,
        "temperature": 0.2,
        "max_tokens": 256,
        "timeout": timeout,
        "headers": {},
    }
    update_config_patch({
        "llm_config": dict(target),
        "local_llm": {
            **(old_local_cfg if isinstance(old_local_cfg, dict) else {}),
            "enabled": True,
            "main_model": dict(target),
        },
    })
    app.state.llm.cfg = get_config()
    return old_llm_cfg, old_local_cfg, old_runtime_cfg


def _restore_runtime(old_llm_cfg: dict, old_local_cfg: dict, old_runtime_cfg: dict) -> None:
    update_config_patch({
        "llm_config": old_llm_cfg if isinstance(old_llm_cfg, dict) else {},
        "local_llm": old_local_cfg if isinstance(old_local_cfg, dict) else {},
    })
    app.state.llm.cfg = old_runtime_cfg


def test_admin_llm_test_returns_friendly_llamacpp_connection_error():
    headers = _admin_headers()
    old_llm_cfg, old_local_cfg, old_runtime_cfg = _activate_llamacpp_runtime(
        "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf")
    try:
        with patch.object(app.state.llm, "chat", side_effect=RuntimeError("[WinError 10061] connection refused")):
            resp = client.post("/api/admin/llm-test",
                               headers=headers, json={"prompt": "test"})
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "llama.cpp" in detail
        assert "http://127.0.0.1:18080" in detail
    finally:
        _restore_runtime(old_llm_cfg, old_local_cfg, old_runtime_cfg)


def test_admin_llm_test_returns_friendly_llamacpp_timeout():
    headers = _admin_headers()
    old_llm_cfg, old_local_cfg, old_runtime_cfg = _activate_llamacpp_runtime(
        "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf")
    try:
        with patch.object(app.state.llm, "chat", side_effect=RuntimeError("request timed out")):
            resp = client.post("/api/admin/llm-test",
                               headers=headers, json={"prompt": "test"})
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "llama.cpp" in detail
        assert "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf" in detail
    finally:
        _restore_runtime(old_llm_cfg, old_local_cfg, old_runtime_cfg)


def test_admin_llm_test_returns_friendly_llamacpp_model_not_found():
    headers = _admin_headers()
    old_llm_cfg, old_local_cfg, old_runtime_cfg = _activate_llamacpp_runtime(
        "missing.gguf")
    try:
        with patch.object(app.state.llm, "chat", side_effect=RuntimeError("model not found: missing.gguf")):
            resp = client.post("/api/admin/llm-test",
                               headers=headers, json={"prompt": "test"})
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "llama.cpp" in detail
        assert "missing.gguf" in detail
    finally:
        _restore_runtime(old_llm_cfg, old_local_cfg, old_runtime_cfg)
