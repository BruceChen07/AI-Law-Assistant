import json
import threading
import time
from pathlib import Path

import pytest

from app.core.database import init_db
from app.services import audit_task_runner
from app.services.crud import (
    get_contract_audit_progress,
    list_audit_queue_items,
    save_audit_queue_item,
)


def _cfg(tmp_path: Path) -> dict:
    cfg = {
        "db_path": str(tmp_path / "app.db"),
        "data_dir": str(tmp_path / "data"),
        "files_dir": str(tmp_path / "files"),
        "static_dir": str(tmp_path / "static"),
        "llm_trace_enabled": False,
    }
    init_db(cfg)
    return cfg


def _contracts_client(cfg):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

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
    app.dependency_overrides[get_current_user] = lambda: {
        "id": "user-1", "role": "admin"}
    app.dependency_overrides[get_app_llm] = lambda: None
    app.dependency_overrides[get_app_embedder] = lambda: _FakeEmbedder()
    app.dependency_overrides[get_app_reranker] = lambda: None
    app.dependency_overrides[get_app_translator] = lambda: None
    return TestClient(app)


def _wait_until(predicate, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _queue_idle() -> bool:
    with audit_task_runner._lock:
        return not audit_task_runner._pending and not audit_task_runner._running


@pytest.fixture(autouse=True)
def _drain_queue():
    yield
    assert _wait_until(_queue_idle), "audit queue did not drain"


def _install_pipeline(monkeypatch, gate=None, execution_order=None):
    import app.services.audit_orchestrator as orchestrator

    def _pipeline(cfg, services, **kwargs):
        audit_id = str(kwargs.get("audit_id") or "")
        if execution_order is not None:
            execution_order.append(audit_id)
        if gate is not None:
            gate.started.set()
            gate.release.wait(10)
        progress_cb = kwargs.get("progress_cb")
        if callable(progress_cb):
            progress_cb("auditing", 50, "chunk-001")
        return {
            "audit": {"summary": "ok", "risks": [], "risk_summary": {"high": 0}},
            "meta": {"execution_path": "classic_v2", "chunks_total": 1,
                     "language": "zh", "text_length": 10},
        }

    monkeypatch.setattr(orchestrator, "run_contract_pipeline_bundle", _pipeline)


def _submit(cfg, audit_id, *, document_id="doc-1"):
    return audit_task_runner.submit(
        cfg=cfg,
        services=None,
        file_path=str(Path(cfg["data_dir"]) / f"{audit_id}.docx"),
        lang="zh",
        retrieval_options={},
        audit_id=audit_id,
        document_id=document_id,
        user_id="user-1",
        title=audit_id,
    )


def test_second_audit_waits_in_queue_and_runs_after_the_first(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    gate = type("Gate", (), {"started": threading.Event(), "release": threading.Event()})()
    order = []
    _install_pipeline(monkeypatch, gate=gate, execution_order=order)

    first = _submit(cfg, "audit-first")
    assert gate.started.wait(5), "first audit did not start"
    second = _submit(cfg, "audit-second")

    assert first["position"] == 1
    assert second["position"] == 1 and second["ahead"] == 1
    queued_progress = get_contract_audit_progress(cfg, "audit-second")
    assert queued_progress["status"] == "queued"
    detail = json.loads(queued_progress["detail_json"])
    assert detail["position"] == 1 and detail["ahead"] == 1

    gate.release.set()
    assert _wait_until(lambda: not audit_task_runner.queue_snapshot()["queued"])
    assert _wait_until(_queue_idle)
    assert order == ["audit-first", "audit-second"]
    assert get_contract_audit_progress(cfg, "audit-first")["status"] == "done"
    assert get_contract_audit_progress(cfg, "audit-second")["status"] == "done"
    assert _wait_until(lambda: list_audit_queue_items(cfg) == [])


def test_cancel_removes_a_queued_audit(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    gate = type("Gate", (), {"started": threading.Event(), "release": threading.Event()})()
    _install_pipeline(monkeypatch, gate=gate)

    _submit(cfg, "audit-running")
    assert gate.started.wait(5)
    _submit(cfg, "audit-queued")

    assert audit_task_runner.cancel("audit-queued") == "cancelled"

    cancelled = get_contract_audit_progress(cfg, "audit-queued")
    assert cancelled["status"] == "cancelled"
    remaining = [row["audit_id"] for row in list_audit_queue_items(cfg)]
    assert "audit-queued" not in remaining

    assert audit_task_runner.cancel("audit-running") == "running"
    assert audit_task_runner.cancel("does-not-exist") == "missing"
    gate.release.set()


def test_queue_snapshot_reports_running_and_positions(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    gate = type("Gate", (), {"started": threading.Event(), "release": threading.Event()})()
    _install_pipeline(monkeypatch, gate=gate)

    _submit(cfg, "audit-a")
    assert gate.started.wait(5)
    _submit(cfg, "audit-b")
    _submit(cfg, "audit-c")

    snapshot = audit_task_runner.queue_snapshot()
    assert snapshot["running_count"] == 1
    assert snapshot["running"][0]["audit_id"] == "audit-a"
    assert [item["audit_id"] for item in snapshot["queued"]] == ["audit-b", "audit-c"]
    assert [item["position"] for item in snapshot["queued"]] == [1, 2]

    gate.release.set()


def test_resume_pending_requeues_after_restart(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _install_pipeline(monkeypatch)
    save_audit_queue_item(
        cfg,
        audit_id="audit-recovered",
        document_id="doc-9",
        file_path=str(Path(cfg["data_dir"]) / "audit-recovered.docx"),
        lang="zh",
        retrieval_options={"audit_mode": "rag"},
        title="recovered",
        user_id="user-1",
    )

    monkeypatch.setattr(audit_task_runner, "_allow_resume_in_tests", True)
    resumed = audit_task_runner.resume_pending(cfg, lambda: None, min_age_seconds=0)

    assert resumed == 1
    assert _wait_until(
        lambda: get_contract_audit_progress(cfg, "audit-recovered") is not None)
    assert _wait_until(
        lambda: get_contract_audit_progress(cfg, "audit-recovered")["status"] == "done")
    # the durable queue row is dropped after the job returns, not with the result
    assert _wait_until(lambda: list_audit_queue_items(cfg) == [])

def test_pin_moves_a_queued_audit_to_the_front(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    gate = type("Gate", (), {"started": threading.Event(), "release": threading.Event()})()
    _install_pipeline(monkeypatch, gate=gate)

    _submit(cfg, "audit-run")
    assert gate.started.wait(5)
    _submit(cfg, "audit-first")
    _submit(cfg, "audit-second")
    _submit(cfg, "audit-third")

    assert [item["audit_id"] for item in audit_task_runner.queue_snapshot()["queued"]] == [
        "audit-first", "audit-second", "audit-third"]

    assert audit_task_runner.pin("audit-third") == "pinned"

    queued = audit_task_runner.queue_snapshot()["queued"]
    assert [item["audit_id"] for item in queued] == [
        "audit-third", "audit-first", "audit-second"]
    assert [item["position"] for item in queued] == [1, 2, 3]
    pinned = get_contract_audit_progress(cfg, "audit-third")
    assert pinned["status"] == "queued"
    detail = json.loads(pinned["detail_json"])
    assert detail["position"] == 1 and detail["ahead"] == 1

    # pinning the head again is a no-op, unknown ids and running audits are refused
    assert audit_task_runner.pin("audit-third") == "already_first"
    assert audit_task_runner.pin("missing-audit") == "missing"
    assert audit_task_runner.pin("audit-run") == "running"

    gate.release.set()


def test_queue_snapshot_lists_contract_titles_in_order(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    gate = type("Gate", (), {"started": threading.Event(), "release": threading.Event()})()
    _install_pipeline(monkeypatch, gate=gate)

    _submit(cfg, "audit-alpha")
    assert gate.started.wait(5)
    _submit(cfg, "audit-beta")
    _submit(cfg, "audit-gamma")

    snapshot = audit_task_runner.queue_snapshot()
    assert snapshot["running"][0]["title"] == "audit-alpha"
    assert [item["title"] for item in snapshot["queued"]] == ["audit-beta", "audit-gamma"]
    assert snapshot["order"] == ["audit-alpha", "audit-beta", "audit-gamma"]

    gate.release.set()


def test_pin_endpoint_rejects_running_and_unknown(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    client = _contracts_client(cfg)

    monkeypatch.setattr(audit_task_runner, "pin", lambda audit_id: "running")
    running = client.post("/contracts/audit/queue/audit-x/pin")
    assert running.status_code == 409
    assert running.json()["detail"]["code"] == "AUDIT_ALREADY_RUNNING"

    monkeypatch.setattr(audit_task_runner, "pin", lambda audit_id: "missing")
    missing = client.post("/contracts/audit/queue/audit-y/pin")
    assert missing.status_code == 404

    monkeypatch.setattr(audit_task_runner, "pin", lambda audit_id: "pinned")
    monkeypatch.setattr(audit_task_runner, "queue_snapshot", lambda: {
        "running": [], "queued": [{"audit_id": "audit-y", "position": 1}],
        "running_count": 0, "queued_count": 1,
    })
    pinned = client.post("/contracts/audit/queue/audit-y/pin")
    assert pinned.status_code == 200
    body = pinned.json()
    assert body["pinned"] is True and body["position"] == 1
