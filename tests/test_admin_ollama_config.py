from copy import deepcopy
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.core.auth import create_user
from app.core.config import get_config, update_config_patch
from app.api.routers import admin as admin_router


client = TestClient(app)


def _admin_headers():
    username = "admin_ollama_cfg_test"
    password = "adminpassword"
    login_resp = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    if login_resp.status_code != 200:
        try:
            create_user(username, "admin_ollama_cfg_test@example.com",
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


def test_admin_ollama_models_endpoint_uses_cache():
    headers = _admin_headers()
    payload = {
        "models": [
            {
                "name": "qwen3.6:27b",
                "model": "qwen3.6:27b",
                "modified_at": "2026-07-02T00:00:00+08:00",
                "size": 123,
                "details": {
                    "parameter_size": "27.8B",
                    "family": "qwen35",
                    "quantization_level": "Q4_K_M",
                },
                "capabilities": ["completion", "tools"],
            }
        ]
    }
    old_cache = deepcopy(admin_router._OLLAMA_MODELS_CACHE)
    admin_router._OLLAMA_MODELS_CACHE.update(
        {"host": "", "ts": 0.0, "models": [], "error": ""}
    )
    try:
        with patch("app.api.routers.admin.httpx.get", return_value=_FakeResponse(payload)) as mocked_get:
            first = client.get("/api/admin/ollama/models", headers=headers)
            second = client.get("/api/admin/ollama/models", headers=headers)
        assert first.status_code == 200
        assert second.status_code == 200
        first_data = first.json()
        second_data = second.json()
        assert first_data["models"][0]["name"] == "qwen3.6:27b"
        assert first_data["cached"] is False
        assert second_data["cached"] is True
        assert mocked_get.call_count == 1
    finally:
        admin_router._OLLAMA_MODELS_CACHE.clear()
        admin_router._OLLAMA_MODELS_CACHE.update(old_cache)


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


def test_admin_update_llm_config_syncs_local_main_model():
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
                "api_key": "",
                "model": "qwen3:4b",
                "temperature": 0.1,
                "max_tokens": 1024,
                "timeout": 45,
                "headers": {},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["provider"] == "ollama"
        assert data["model"] == "qwen3:4b"
        assert data["config_source"] == "local_main_model"

        cfg_after = get_config()
        assert cfg_after["llm_config"]["provider"] == "ollama"
        assert cfg_after["llm_config"]["model"] == "qwen3:4b"
        assert cfg_after["local_llm"]["main_model"]["provider"] == "ollama"
        assert cfg_after["local_llm"]["main_model"]["model"] == "qwen3:4b"
    finally:
        update_config_patch({
            "llm_config": old_llm_cfg if isinstance(old_llm_cfg, dict) else {},
            "local_llm": old_local_cfg if isinstance(old_local_cfg, dict) else {},
        })


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
                "api_key": "",
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


def test_admin_ollama_models_endpoint_reports_unreachable_host():
    headers = _admin_headers()
    old_cache = deepcopy(admin_router._OLLAMA_MODELS_CACHE)
    admin_router._OLLAMA_MODELS_CACHE.update(
        {"host": "", "ts": 0.0, "models": [], "error": ""}
    )
    try:
        with patch("app.api.routers.admin.httpx.get", side_effect=RuntimeError("[WinError 10061] connection refused")):
            resp = client.get(
                "/api/admin/ollama/models?force_refresh=true", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["reachable"] is False
        assert data["models"] == []
        assert "10061" in data["error"]
    finally:
        admin_router._OLLAMA_MODELS_CACHE.clear()
        admin_router._OLLAMA_MODELS_CACHE.update(old_cache)


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


def test_admin_llm_test_returns_friendly_ollama_connection_error():
    headers = _admin_headers()
    cfg_before = get_config()
    old_llm_cfg = deepcopy(cfg_before.get("llm_config"))
    old_local_cfg = deepcopy(cfg_before.get("local_llm"))
    old_runtime_cfg = deepcopy(getattr(app.state.llm, "cfg", {}))
    try:
        update_config_patch({
            "llm_config": {
                "provider": "ollama",
                "api_base": "http://127.0.0.1:11435/v1",
                "api_key": "",
                "model": "qwen3:4b",
                "temperature": 0.1,
                "max_tokens": 256,
                "timeout": 5,
                "headers": {},
            },
            "local_llm": {
                **(old_local_cfg if isinstance(old_local_cfg, dict) else {}),
                "enabled": True,
                "main_model": {
                    "provider": "ollama",
                    "api_base": "http://127.0.0.1:11435/v1",
                    "api_key": "",
                    "model": "qwen3:4b",
                    "temperature": 0.1,
                    "max_tokens": 256,
                    "timeout": 5,
                    "headers": {},
                },
            },
        })
        app.state.llm.cfg = get_config()
        with patch.object(app.state.llm, "chat", side_effect=RuntimeError("[WinError 10061] connection refused")):
            resp = client.post("/api/admin/llm-test",
                               headers=headers, json={"prompt": "test"})
        assert resp.status_code == 400
        assert "http://127.0.0.1:11435" in resp.json()["detail"]
    finally:
        update_config_patch({
            "llm_config": old_llm_cfg if isinstance(old_llm_cfg, dict) else {},
            "local_llm": old_local_cfg if isinstance(old_local_cfg, dict) else {},
        })
        app.state.llm.cfg = old_runtime_cfg


def test_admin_llm_test_returns_friendly_ollama_timeout():
    headers = _admin_headers()
    cfg_before = get_config()
    old_llm_cfg = deepcopy(cfg_before.get("llm_config"))
    old_local_cfg = deepcopy(cfg_before.get("local_llm"))
    old_runtime_cfg = deepcopy(getattr(app.state.llm, "cfg", {}))
    try:
        update_config_patch({
            "llm_config": {
                "provider": "ollama",
                "api_base": "http://127.0.0.1:11434/v1",
                "api_key": "",
                "model": "qwen3:4b",
                "temperature": 0.1,
                "max_tokens": 256,
                "timeout": 1,
                "headers": {},
            },
            "local_llm": {
                **(old_local_cfg if isinstance(old_local_cfg, dict) else {}),
                "enabled": True,
                "main_model": {
                    "provider": "ollama",
                    "api_base": "http://127.0.0.1:11434/v1",
                    "api_key": "",
                    "model": "qwen3:4b",
                    "temperature": 0.1,
                    "max_tokens": 256,
                    "timeout": 1,
                    "headers": {},
                },
            },
        })
        app.state.llm.cfg = get_config()
        with patch.object(app.state.llm, "chat", side_effect=RuntimeError("request timed out")):
            resp = client.post("/api/admin/llm-test",
                               headers=headers, json={"prompt": "test"})
        assert resp.status_code == 400
        assert "Ollama 模型响应超时" in resp.json()["detail"]
        assert "qwen3:4b" in resp.json()["detail"]
    finally:
        update_config_patch({
            "llm_config": old_llm_cfg if isinstance(old_llm_cfg, dict) else {},
            "local_llm": old_local_cfg if isinstance(old_local_cfg, dict) else {},
        })
        app.state.llm.cfg = old_runtime_cfg


def test_admin_llm_test_returns_friendly_ollama_model_not_found():
    headers = _admin_headers()
    cfg_before = get_config()
    old_llm_cfg = deepcopy(cfg_before.get("llm_config"))
    old_local_cfg = deepcopy(cfg_before.get("local_llm"))
    old_runtime_cfg = deepcopy(getattr(app.state.llm, "cfg", {}))
    try:
        update_config_patch({
            "llm_config": {
                "provider": "ollama",
                "api_base": "http://127.0.0.1:11434/v1",
                "api_key": "",
                "model": "missing-model:1b",
                "temperature": 0.1,
                "max_tokens": 64,
                "timeout": 15,
                "headers": {},
            },
            "local_llm": {
                **(old_local_cfg if isinstance(old_local_cfg, dict) else {}),
                "enabled": True,
                "main_model": {
                    "provider": "ollama",
                    "api_base": "http://127.0.0.1:11434/v1",
                    "api_key": "",
                    "model": "missing-model:1b",
                    "temperature": 0.1,
                    "max_tokens": 64,
                    "timeout": 15,
                    "headers": {},
                },
            },
        })
        app.state.llm.cfg = get_config()
        with patch.object(app.state.llm, "chat", side_effect=RuntimeError("ollama model not found: missing-model:1b")):
            resp = client.post("/api/admin/llm-test", headers=headers, json={"prompt": "test"})
        assert resp.status_code == 400
        assert "Ollama 模型不存在" in resp.json()["detail"]
        assert "missing-model:1b" in resp.json()["detail"]
    finally:
        update_config_patch({
            "llm_config": old_llm_cfg if isinstance(old_llm_cfg, dict) else {},
            "local_llm": old_local_cfg if isinstance(old_local_cfg, dict) else {},
        })
        app.state.llm.cfg = old_runtime_cfg


def test_admin_llm_test_returns_friendly_llamacpp_connection_error():
    headers = _admin_headers()
    cfg_before = get_config()
    old_llm_cfg = deepcopy(cfg_before.get("llm_config"))
    old_local_cfg = deepcopy(cfg_before.get("local_llm"))
    old_runtime_cfg = deepcopy(getattr(app.state.llm, "cfg", {}))
    try:
        update_config_patch({
            "llm_config": {
                "provider": "llama_cpp",
                "api_base": "http://127.0.0.1:18080/v1",
                "api_key": "",
                "model": "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
                "temperature": 0.2,
                "max_tokens": 256,
                "timeout": 5,
                "headers": {},
            },
            "local_llm": {
                **(old_local_cfg if isinstance(old_local_cfg, dict) else {}),
                "enabled": True,
                "main_model": {
                    "provider": "llama_cpp",
                    "api_base": "http://127.0.0.1:18080/v1",
                    "api_key": "",
                    "model": "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
                    "temperature": 0.2,
                    "max_tokens": 256,
                    "timeout": 5,
                    "headers": {},
                },
            },
        })
        app.state.llm.cfg = get_config()
        with patch.object(app.state.llm, "chat", side_effect=RuntimeError("[WinError 10061] connection refused")):
            resp = client.post("/api/admin/llm-test", headers=headers, json={"prompt": "test"})
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "llama.cpp" in detail
        assert "http://127.0.0.1:18080" in detail
    finally:
        update_config_patch({
            "llm_config": old_llm_cfg if isinstance(old_llm_cfg, dict) else {},
            "local_llm": old_local_cfg if isinstance(old_local_cfg, dict) else {},
        })
        app.state.llm.cfg = old_runtime_cfg


def test_admin_llm_test_returns_friendly_llamacpp_timeout():
    headers = _admin_headers()
    cfg_before = get_config()
    old_llm_cfg = deepcopy(cfg_before.get("llm_config"))
    old_local_cfg = deepcopy(cfg_before.get("local_llm"))
    old_runtime_cfg = deepcopy(getattr(app.state.llm, "cfg", {}))
    try:
        update_config_patch({
            "llm_config": {
                "provider": "llama_cpp",
                "api_base": "http://127.0.0.1:18080/v1",
                "api_key": "",
                "model": "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
                "temperature": 0.2,
                "max_tokens": 256,
                "timeout": 5,
                "headers": {},
            },
            "local_llm": {
                **(old_local_cfg if isinstance(old_local_cfg, dict) else {}),
                "enabled": True,
                "main_model": {
                    "provider": "llama_cpp",
                    "api_base": "http://127.0.0.1:18080/v1",
                    "api_key": "",
                    "model": "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
                    "temperature": 0.2,
                    "max_tokens": 256,
                    "timeout": 5,
                    "headers": {},
                },
            },
        })
        app.state.llm.cfg = get_config()
        with patch.object(app.state.llm, "chat", side_effect=RuntimeError("request timed out")):
            resp = client.post("/api/admin/llm-test", headers=headers, json={"prompt": "test"})
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "llama.cpp" in detail
        assert "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf" in detail
    finally:
        update_config_patch({
            "llm_config": old_llm_cfg if isinstance(old_llm_cfg, dict) else {},
            "local_llm": old_local_cfg if isinstance(old_local_cfg, dict) else {},
        })
        app.state.llm.cfg = old_runtime_cfg


def test_admin_llm_test_returns_friendly_llamacpp_model_not_found():
    headers = _admin_headers()
    cfg_before = get_config()
    old_llm_cfg = deepcopy(cfg_before.get("llm_config"))
    old_local_cfg = deepcopy(cfg_before.get("local_llm"))
    old_runtime_cfg = deepcopy(getattr(app.state.llm, "cfg", {}))
    try:
        update_config_patch({
            "llm_config": {
                "provider": "llama_cpp",
                "api_base": "http://127.0.0.1:18080/v1",
                "api_key": "",
                "model": "missing.gguf",
                "temperature": 0.2,
                "max_tokens": 256,
                "timeout": 5,
                "headers": {},
            },
            "local_llm": {
                **(old_local_cfg if isinstance(old_local_cfg, dict) else {}),
                "enabled": True,
                "main_model": {
                    "provider": "llama_cpp",
                    "api_base": "http://127.0.0.1:18080/v1",
                    "api_key": "",
                    "model": "missing.gguf",
                    "temperature": 0.2,
                    "max_tokens": 256,
                    "timeout": 5,
                    "headers": {},
                },
            },
        })
        app.state.llm.cfg = get_config()
        with patch.object(app.state.llm, "chat", side_effect=RuntimeError("model not found: missing.gguf")):
            resp = client.post("/api/admin/llm-test", headers=headers, json={"prompt": "test"})
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "llama.cpp" in detail
        assert "missing.gguf" in detail
    finally:
        update_config_patch({
            "llm_config": old_llm_cfg if isinstance(old_llm_cfg, dict) else {},
            "local_llm": old_local_cfg if isinstance(old_local_cfg, dict) else {},
        })
        app.state.llm.cfg = old_runtime_cfg
