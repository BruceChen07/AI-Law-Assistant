from fastapi.testclient import TestClient

from app.api.routers import health as health_router
from app.main import app

client = TestClient(app)


def _patch_config(monkeypatch, model_source: str) -> None:
    monkeypatch.setattr(
        health_router, "get_config",
        lambda: {"mineru": {"model_source": model_source}},
    )


def test_health_exposes_mineru_block(monkeypatch):
    _patch_config(monkeypatch, "local")
    monkeypatch.setattr(
        health_router.mineru_models, "inspect_local_models",
        lambda cfg=None, deep=False, verify_sha256=False: {
            "ok": True,
            "root": "/models/mineru",
            "config_path": "/models/mineru/mineru.json",
            "manifest_path": "/models/mineru/manifest.json",
            "required_kinds": ["pipeline", "vlm"],
            "missing": [],
            "optional_missing": [],
            "message": "",
        },
    )

    body = client.get("/health").json()

    assert body["status"] == "ok"
    assert body["mineru"]["local_mode"] is True
    assert body["mineru"]["local_ready"] is True
    assert "download" in body["mineru"]


def test_mineru_health_ok_when_models_ready(monkeypatch):
    _patch_config(monkeypatch, "local")
    monkeypatch.setattr(
        health_router.mineru_models, "inspect_local_models",
        lambda cfg=None, deep=False, verify_sha256=False: {
            "ok": True, "root": "/models/mineru", "missing": [],
            "required_kinds": ["pipeline", "vlm"], "message": "",
            "config_path": "/models/mineru/mineru.json",
            "manifest_path": "/models/mineru/manifest.json",
            "optional_missing": [],
        },
    )
    monkeypatch.setattr(
        health_router.mineru_models, "get_status",
        lambda: {"running": False, "stage": "done", "ok": True, "error": "",
                 "started_at": "", "finished_at": ""},
    )

    body = client.get("/health/mineru").json()

    assert body["status"] == "ok"
    assert body["deep_checked"] is False


def test_mineru_health_unavailable_when_models_missing(monkeypatch):
    _patch_config(monkeypatch, "local")
    monkeypatch.setattr(
        health_router.mineru_models, "inspect_local_models",
        lambda cfg=None, deep=False, verify_sha256=False: {
            "ok": False, "root": "/models/mineru",
            "missing": ["vlm/model.safetensors"],
            "required_kinds": ["pipeline", "vlm"],
            "message": "模型不完整",
            "config_path": "", "manifest_path": "", "optional_missing": [],
        },
    )
    monkeypatch.setattr(
        health_router.mineru_models, "get_status",
        lambda: {"running": True, "stage": "downloading", "ok": False,
                 "error": "", "started_at": "", "finished_at": ""},
    )

    body = client.get("/health/mineru").json()

    assert body["local_ready"] is False
    assert body["status"] == "degraded"
    assert body["missing"] == ["vlm/model.safetensors"]


def test_mineru_health_non_local_source(monkeypatch):
    _patch_config(monkeypatch, "huggingface")
    monkeypatch.setattr(
        health_router.mineru_models, "get_status",
        lambda: {"running": False, "stage": "idle", "ok": False, "error": "",
                 "started_at": "", "finished_at": ""},
    )

    body = client.get("/health/mineru").json()

    assert body["local_mode"] is False
    assert body["status"] == "unavailable"
