"""Audit history carries the persisted contract snapshot (no LLM on list load)."""
import json
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.database import init_db
from app.services.crud import (
    get_contract_summary,
    insert_contract_audit,
    insert_document,
    list_contract_summaries,
    upsert_contract_summary,
)


def _cfg(tmp_path: Path) -> dict:
    cfg = {
        "db_path": str(tmp_path / "app.db"),
        "data_dir": str(tmp_path / "data"),
        "files_dir": str(tmp_path / "files"),
        "static_dir": str(tmp_path / "static"),
        "llm_trace_enabled": False,
        "contract_summary_enabled": True,
    }
    os.makedirs(cfg["data_dir"], exist_ok=True)
    init_db(cfg)
    return cfg


def _client(cfg):
    from app.api.dependencies import (
        get_app_embedder,
        get_app_llm,
        get_app_reranker,
        get_app_translator,
        get_current_user,
    )
    from app.api.routers import contracts as contracts_router

    class _FakeEmbedder:
        def get_registry_status(self):
            return {"ready": True, "default_language": "zh", "languages": ["zh"]}

    app = FastAPI()
    app.include_router(contracts_router.build_router(cfg))
    app.dependency_overrides[get_current_user] = lambda: {"id": "user-1", "role": "admin"}
    app.dependency_overrides[get_app_llm] = lambda: None
    app.dependency_overrides[get_app_embedder] = lambda: _FakeEmbedder()
    app.dependency_overrides[get_app_reranker] = lambda: None
    app.dependency_overrides[get_app_translator] = lambda: None
    return TestClient(app)


def _document(cfg, tmp_path: Path, name: str) -> str:
    path = tmp_path / f"{name}.docx"
    path.write_bytes(b"PK\x03\x04fake")
    return insert_document(
        cfg,
        doc_id=f"doc-{name}",
        filename=path.name,
        original_filename=f"{name}.docx",
        file_path=str(path),
        file_size=path.stat().st_size,
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        user_id="user-1",
        title=name,
        status="active",
        category="contract",
    )


def test_summary_roundtrip_and_batch_lookup(tmp_path):
    cfg = _cfg(tmp_path)
    upsert_contract_summary(
        cfg,
        document_id="doc-a",
        lang="zh",
        summary={"title": "A 标题", "about": "讲什么", "core": "核心", "tax": "税务"},
        status="ok",
        source="llm",
    )
    row = get_contract_summary(cfg, "doc-a", "zh")
    assert row["title"] == "A 标题"
    assert int(row["chars"]) > 0
    assert row["status"] == "ok"

    upsert_contract_summary(
        cfg, document_id="doc-b", lang="en",
        summary={"title": "B title", "about": "about"}, status="fallback", source="source_text")
    batch = list_contract_summaries(cfg, ["doc-a", "doc-b", "doc-missing"], None)
    assert set(batch) == {"doc-a", "doc-b"}
    assert batch["doc-b"]["title"] == "B title"
    assert get_contract_summary(cfg, "doc-missing") is None


def test_history_attaches_persisted_summary(tmp_path):
    cfg = _cfg(tmp_path)
    doc_with = _document(cfg, tmp_path, "with-summary")
    doc_without = _document(cfg, tmp_path, "without-summary")

    for doc_id, audit_id in ((doc_with, "audit-1"), (doc_without, "audit-2")):
        insert_contract_audit(
            cfg,
            audit_id=audit_id,
            document_id=doc_id,
            status="done",
            result_json=json.dumps({"risk_summary": {"high": 1, "medium": 2, "low": 0}}),
            model_provider="llama_cpp",
            model_name="test",
            created_at="2026-11-05T00:00:00+00:00",
        )
    upsert_contract_summary(
        cfg, document_id=doc_with, lang="zh",
        summary={"title": "带摘要的合同", "about": "合同讲什么", "core": "核心安排", "tax": "税务要点"},
        status="ok", source="llm")

    response = _client(cfg).get("/contracts/audits/history?lang=zh")
    assert response.status_code == 200
    items = {item["audit_id"]: item for item in response.json()["items"]}

    assert items["audit-1"]["summary"]["title"] == "带摘要的合同"
    assert items["audit-1"]["summary_status"] == "ok"
    assert items["audit-2"]["summary"] is None
    # the list must not invent anything for the row without a stored snapshot
    assert items["audit-2"]["summary_status"] == ""


def test_summary_endpoint_persists_the_result(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    doc_id = _document(cfg, tmp_path, "endpoint-doc")

    import app.api.routers.contracts as contracts_router

    monkeypatch.setattr(
        contracts_router, "extract_text_with_config",
        lambda cfg_arg, path: ("第一条 合同正文", {"page_count": 1}),
    )
    monkeypatch.setattr(
        contracts_router, "build_preview_clauses", lambda text: ["第一条 合同正文"],
    )
    monkeypatch.setattr(
        contracts_router, "build_contract_summary",
        lambda *args, **kwargs: {
            "enabled": True,
            "status": "ok",
            "source": "llm",
            "summary": {"title": "端点标题", "about": "端点摘要", "core": "", "tax": ""},
            "error": "",
        },
    )

    response = _client(cfg).get(f"/contracts/{doc_id}/summary?language=zh")
    assert response.status_code == 200
    assert response.json()["summary"]["title"] == "端点标题"

    stored = get_contract_summary(cfg, doc_id, "zh")
    assert stored is not None
    assert stored["title"] == "端点标题"
    assert stored["status"] == "ok"
    assert stored["source"] == "llm"
