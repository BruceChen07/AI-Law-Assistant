"""审计队列持久化：重启后恢复未完成任务。"""

import time

from app.core.database import init_db
from app.services import audit_task_runner as runner
from app.services import crud


def _cfg(tmp_path):
    cfg = {"db_path": str(tmp_path / "app.db"), "data_dir": str(tmp_path)}
    init_db(cfg)
    return cfg


def _reset_runner():
    # 等上一个用例的任务真正结束，避免跨用例串扰。
    deadline = time.time() + 5
    while time.time() < deadline:
        snap = runner.queue_snapshot()
        if not snap["queued_count"] and not snap["running_count"]:
            break
        time.sleep(0.02)
    with runner._lock:
        runner._pending.clear()
        runner._running.clear()


def test_queue_rows_roundtrip(tmp_path):
    cfg = _cfg(tmp_path)

    crud.save_audit_queue_item(
        cfg, audit_id="a1", document_id="d1", file_path="f.pdf", lang="zh",
        retrieval_options={"top_k": 5}, title="合同A", user_id="u1")

    rows = crud.list_audit_queue_items(cfg)
    assert len(rows) == 1
    assert rows[0]["audit_id"] == "a1"
    assert rows[0]["file_path"] == "f.pdf"
    assert rows[0]["status"] == "queued"

    crud.update_audit_queue_status(cfg, "a1", "running")
    assert crud.list_audit_queue_items(cfg, statuses=("running",))[0]["audit_id"] == "a1"

    crud.delete_audit_queue_item(cfg, "a1")
    assert crud.list_audit_queue_items(cfg) == []


def test_resume_pending_reenqueues_in_order(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    for index in (1, 2):
        crud.save_audit_queue_item(
            cfg, audit_id=f"a{index}", document_id=f"d{index}",
            file_path=f"f{index}.pdf", lang="zh", title=f"合同{index}")

    _reset_runner()
    order = []
    monkeypatch.setattr(runner, "_record_progress", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_run", lambda **job: order.append(job["audit_id"]))

    resumed = runner.resume_pending(cfg, services_factory=lambda: object())

    deadline = time.time() + 5
    while time.time() < deadline:
        snap = runner.queue_snapshot()
        if not snap["queued_count"] and not snap["running_count"]:
            break
        time.sleep(0.02)

    assert resumed == 2
    assert order == ["a1", "a2"]
    assert crud.list_audit_queue_items(cfg) == [], "完成后应清理队列行"


def test_resume_without_factory_keeps_rows_queued(tmp_path):
    cfg = _cfg(tmp_path)
    crud.save_audit_queue_item(
        cfg, audit_id="a9", document_id="d9", file_path="f9.pdf", lang="zh")

    resumed = runner.resume_pending(cfg, services_factory=None)

    assert resumed == 0
    rows = crud.list_audit_queue_items(cfg, statuses=("queued",))
    assert [row["audit_id"] for row in rows] == ["a9"]
