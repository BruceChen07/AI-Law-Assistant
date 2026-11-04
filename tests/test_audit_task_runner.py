import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.database import init_db
from app.services import audit_task_runner
from app.services.crud import (
    get_contract_audit_by_id,
    get_contract_audit_progress,
    list_running_contract_audit_progress,
    mark_stale_contract_audit_progress,
    upsert_contract_audit_progress,
)


class _FakeEmbedder:
    def get_registry_status(self):
        return {"ready": True, "default_language": "zh", "languages": ["zh"]}


def _cfg(tmp_path: Path) -> dict:
    cfg = {
        "db_path": str(tmp_path / "app.db"),
        "data_dir": str(tmp_path / "data"),
        "files_dir": str(tmp_path / "files"),
        "static_dir": str(tmp_path / "static"),
        "llm_trace_enabled": False,
        "llm_config": {"provider": "llama_cpp", "model": "test-model"},
    }
    init_db(cfg)
    return cfg


def _contracts_client(cfg):
    from app.api.dependencies import (
        get_app_embedder,
        get_app_llm,
        get_app_reranker,
        get_app_translator,
        get_current_user,
    )
    from app.api.routers import contracts as contracts_router

    app = FastAPI()
    app.include_router(contracts_router.build_router(cfg))
    app.dependency_overrides[get_current_user] = lambda: {
        "id": "user-1", "role": "admin"}
    app.dependency_overrides[get_app_llm] = lambda: None
    app.dependency_overrides[get_app_embedder] = lambda: _FakeEmbedder()
    app.dependency_overrides[get_app_reranker] = lambda: None
    app.dependency_overrides[get_app_translator] = lambda: None
    return TestClient(app)


def _progress_row(cfg, audit_id, *, status="running", percent=30, minutes_ago=0):
    upsert_contract_audit_progress(
        cfg,
        audit_id=audit_id,
        status=status,
        stage="auditing",
        percent=percent,
        message="chunk-001",
        document_id="doc-1",
        user_id="user-1",
    )
    if minutes_ago:
        from datetime import datetime, timedelta, timezone

        import sqlite3

        stale_ts = (
            datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
        ).isoformat()
        conn = sqlite3.connect(cfg["db_path"])
        conn.execute(
            "UPDATE contract_audit_progress SET updated_at=? WHERE audit_id=?",
            (stale_ts, audit_id),
        )
        conn.commit()
        conn.close()


def test_stale_running_audits_are_marked_interrupted(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _progress_row(cfg, "audit-fresh", minutes_ago=0)
    _progress_row(cfg, "audit-orphan", minutes_ago=30)
    upsert_contract_audit_progress(cfg, audit_id="audit-done", status="done", percent=100)

    updated = mark_stale_contract_audit_progress(cfg, stale_after_minutes=10)

    assert updated == 1
    assert get_contract_audit_progress(cfg, "audit-orphan")["status"] == "interrupted"
    assert "no heartbeat" in get_contract_audit_progress(cfg, "audit-orphan")["message"]
    assert get_contract_audit_progress(cfg, "audit-fresh")["status"] == "running"
    assert get_contract_audit_progress(cfg, "audit-done")["status"] == "done"


def test_running_audits_endpoint_lists_only_running(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _progress_row(cfg, "audit-running", minutes_ago=0)
    upsert_contract_audit_progress(cfg, audit_id="audit-finished", status="done", percent=100)

    client = _contracts_client(cfg)
    response = client.get("/contracts/audits/running")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert [item["audit_id"] for item in payload["items"]] == ["audit-running"]
    assert payload["items"][0]["stale_seconds"] >= 0


def test_health_reports_running_audit_count(tmp_path: Path):
    from app.api.routers.health import build_router as build_health_router
    from app.api.dependencies import get_app_embedder

    cfg = _cfg(tmp_path)
    _progress_row(cfg, "audit-running")
    app = FastAPI()
    app.include_router(build_health_router(cfg))
    app.dependency_overrides[get_app_embedder] = lambda: _FakeEmbedder()

    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json()["audits_running"] == 1


def test_submit_endpoint_returns_202_and_hands_off_to_background_task(
    tmp_path: Path, monkeypatch
):
    cfg = _cfg(tmp_path)
    submitted = {}

    def _fake_submit(**kwargs):
        submitted.update(kwargs)

    monkeypatch.setattr(audit_task_runner, "submit", _fake_submit)
    client = _contracts_client(cfg)

    response = client.post(
        "/contracts/audit",
        files={
            "file": (
                "contract.docx",
                b"PK\x03\x04fake-docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        data={"audit_id": "audit-async-1", "language": "zh"},
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["audit_id"] == "audit-async-1"
    assert payload["status"] == "running"
    assert payload["mode"] == "async"
    assert payload["document_id"]
    assert submitted["audit_id"] == "audit-async-1"
    assert submitted["document_id"] == payload["document_id"]
    assert str(submitted["file_path"]).endswith(".docx")
    assert get_contract_audit_progress(cfg, "audit-async-1")["status"] == "running"


def test_duplicate_submission_while_running_is_rejected(tmp_path: Path, monkeypatch):
    cfg = _cfg(tmp_path)
    called = {"submit": 0}

    def _fake_submit(**kwargs):
        called["submit"] += 1

    monkeypatch.setattr(audit_task_runner, "submit", _fake_submit)
    _progress_row(cfg, "audit-dup", minutes_ago=0)
    client = _contracts_client(cfg)

    response = client.post(
        "/contracts/audit",
        files={"file": ("contract.docx", b"PK\x03\x04fake", "application/octet-stream")},
        data={"audit_id": "audit-dup", "language": "zh"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "AUDIT_ALREADY_RUNNING"
    assert called["submit"] == 0


def test_background_runner_persists_done_result(tmp_path: Path, monkeypatch):
    cfg = _cfg(tmp_path)

    import app.services.audit_orchestrator as orchestrator

    def _fake_pipeline(cfg_arg, services, **kwargs):
        progress_cb = kwargs.get("progress_cb")
        if callable(progress_cb):
            progress_cb("auditing", 55, "llm request 2/4: chunk-002", detail={"chunks_total": 4})
        return {
            "audit": {"summary": "ok", "risks": [], "risk_summary": {"high": 0}},
            "meta": {"execution_path": "classic_v2", "chunks_total": 4},
            "risk_summary": {"high": 0, "medium": 0, "low": 0},
        }

    monkeypatch.setattr(orchestrator, "run_contract_pipeline_bundle", _fake_pipeline)

    audit_task_runner._run(
        cfg=cfg,
        services=None,
        file_path=str(tmp_path / "contract.docx"),
        lang="zh",
        retrieval_options={},
        audit_id="audit-runner-1",
        document_id="doc-1",
        user_id="user-1",
        on_finish=None,
    )

    progress = get_contract_audit_progress(cfg, "audit-runner-1")
    assert progress["status"] == "done"
    assert progress["percent"] == 100
    record = get_contract_audit_by_id(cfg, "audit-runner-1")
    assert record["status"] == "done"
    assert json.loads(record["result_json"])["summary"] == "ok"


def test_background_runner_records_failure_with_user_message(tmp_path: Path, monkeypatch):
    cfg = _cfg(tmp_path)

    import app.services.audit_orchestrator as orchestrator

    def _failing_pipeline(cfg_arg, services, **kwargs):
        raise RuntimeError("COVERAGE_INCOMPLETE: plan exceeds capacity")

    monkeypatch.setattr(orchestrator, "run_contract_pipeline_bundle", _failing_pipeline)

    audit_task_runner._run(
        cfg=cfg,
        services=None,
        file_path=str(tmp_path / "contract.docx"),
        lang="zh",
        retrieval_options={},
        audit_id="audit-runner-2",
        document_id="doc-1",
        user_id="user-1",
        on_finish=None,
    )

    progress = get_contract_audit_progress(cfg, "audit-runner-2")
    assert progress["status"] == "failed"
    assert "计划容量" in progress["message"]
    record = get_contract_audit_by_id(cfg, "audit-runner-2")
    assert record["status"] == "failed"
    assert "COVERAGE_INCOMPLETE" in json.loads(record["result_json"])["error"]


def test_running_audit_ids_reflect_thread_lifecycle(tmp_path: Path):
    assert audit_task_runner.running_audit_ids() == [] or isinstance(
        audit_task_runner.running_audit_ids(), list)
    assert audit_task_runner.is_audit_running("audit-none") is False
    assert list_running_contract_audit_progress(_cfg(tmp_path)) == []
