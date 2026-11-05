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

    resumed = audit_task_runner.resume_pending(cfg, lambda: None)

    assert resumed == 1
    assert _wait_until(
        lambda: get_contract_audit_progress(cfg, "audit-recovered") is not None)
    assert _wait_until(
        lambda: get_contract_audit_progress(cfg, "audit-recovered")["status"] == "done")
    # the durable queue row is dropped after the job returns, not with the result
    assert _wait_until(lambda: list_audit_queue_items(cfg) == [])
