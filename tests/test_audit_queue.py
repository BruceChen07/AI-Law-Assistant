"""审计队列：严格串行、FIFO、可取消。"""

import threading
import time

from app.services import audit_task_runner as runner


def _job(audit_id, tag=""):
    return {
        "cfg": {},
        "services": None,
        "file_path": "fixture.pdf",
        "lang": "zh",
        "retrieval_options": None,
        "audit_id": audit_id,
        "document_id": f"doc-{audit_id}",
        "user_id": "u1",
        "title": tag or audit_id,
    }


def _reset():
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


def _wait_idle(timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        snap = runner.queue_snapshot()
        if not snap["queued_count"] and not snap["running_count"]:
            return True
        time.sleep(0.02)
    return False


def _fake_run_factory(handler):
    """构造与真实 _run 相同签名的假实现，用于捕获多余参数。"""

    def fake_run(*, cfg, services, file_path, lang, retrieval_options,
                 audit_id, document_id, user_id="", on_finish=None):
        return handler(audit_id)

    return fake_run


def test_queue_runs_one_job_at_a_time(monkeypatch):
    _reset()
    monkeypatch.setattr(runner, "_record_progress", lambda *a, **k: None)
    started = []
    release_first = threading.Event()

    def handler(audit_id):
        started.append(audit_id)
        if audit_id == "A":
            release_first.wait(5)

    monkeypatch.setattr(runner, "_run", _fake_run_factory(handler))

    first = runner.enqueue(**_job("A"))
    second = runner.enqueue(**_job("B"))
    time.sleep(0.2)

    # ahead 表示「前面还有几个任务」，包含正在执行的那个。
    assert first["ahead"] == 0
    assert second["ahead"] == 1

    snap = runner.queue_snapshot()
    assert started == ["A"], "第二个任务不应与第一个并行"
    assert [item["audit_id"] for item in snap["queued"]] == ["B"]

    release_first.set()
    assert _wait_idle()
    assert started == ["A", "B"]


def test_cancel_queued_job(monkeypatch):
    _reset()
    monkeypatch.setattr(runner, "_record_progress", lambda *a, **k: None)
    release_first = threading.Event()
    seen = []

    def handler(audit_id):
        seen.append(audit_id)
        if audit_id == "RUN":
            release_first.wait(5)

    monkeypatch.setattr(runner, "_run", _fake_run_factory(handler))

    runner.enqueue(**_job("RUN"))
    runner.enqueue(**_job("WAIT"))
    time.sleep(0.2)

    assert runner.cancel("WAIT") == "cancelled"
    assert runner.cancel("WAIT") == "missing"
    assert [item["audit_id"] for item in runner.queue_snapshot()["queued"]] == []

    release_first.set()
    assert _wait_idle()
    assert seen == ["RUN"]


def test_cannot_cancel_running_job(monkeypatch):
    _reset()
    monkeypatch.setattr(runner, "_record_progress", lambda *a, **k: None)
    release = threading.Event()

    def handler(audit_id):
        release.wait(5)

    monkeypatch.setattr(runner, "_run", _fake_run_factory(handler))

    runner.enqueue(**_job("BUSY"))
    time.sleep(0.2)

    assert runner.cancel("BUSY") == "running"

    release.set()
    assert _wait_idle()


def test_queue_snapshot_positions(monkeypatch):
    _reset()
    monkeypatch.setattr(runner, "_record_progress", lambda *a, **k: None)
    release = threading.Event()

    def handler(audit_id):
        release.wait(5)

    monkeypatch.setattr(runner, "_run", _fake_run_factory(handler))

    runner.enqueue(**_job("P0"))
    runner.enqueue(**_job("P1"))
    runner.enqueue(**_job("P2"))
    time.sleep(0.2)

    snap = runner.queue_snapshot()
    assert [item["position"] for item in snap["queued"]] == [1, 2]
    assert [item["audit_id"] for item in snap["queued"]] == ["P1", "P2"]

    release.set()
    assert _wait_idle()


def test_title_does_not_break_worker(monkeypatch):
    """回归：job 里的展示字段（title）不能被透传给 _run。"""
    _reset()
    monkeypatch.setattr(runner, "_record_progress", lambda *a, **k: None)
    executed = []

    monkeypatch.setattr(runner, "_run", _fake_run_factory(executed.append))

    runner.enqueue(**_job("T1", tag="合同标题"))

    assert _wait_idle()
    assert executed == ["T1"]


def test_summary_runs_before_audit(monkeypatch):
    """摘要优先：同一个任务里先生成摘要，再跑审计。"""
    _reset()
    monkeypatch.setattr(runner, "_record_progress", lambda *a, **k: None)
    order = []

    monkeypatch.setattr(
        runner, "_run", _fake_run_factory(lambda audit_id: order.append(("audit", audit_id))))

    import app.core.utils as utils_mod
    import app.services.contract_summary as summary_mod

    monkeypatch.setattr(
        utils_mod, "extract_text_with_config",
        lambda cfg, path: ("合同正文", {"page_count": 1}))
    monkeypatch.setattr(utils_mod, "file_digest", lambda path: "digest-1")
    monkeypatch.setattr(
        summary_mod, "build_contract_summary",
        lambda *a, **k: order.append(("summary", k.get("document_id"))) or {"status": "ok"})

    class _Services:
        llm = object()

    job = _job("ORDER")
    job["services"] = _Services()
    job["cfg"] = {"contract_summary_enabled": True}

    runner.enqueue(**job)

    assert _wait_idle()
    assert [item[0] for item in order] == ["summary", "audit"]


def test_summary_failure_does_not_block_audit(monkeypatch):
    _reset()
    monkeypatch.setattr(runner, "_record_progress", lambda *a, **k: None)
    ran = []
    monkeypatch.setattr(runner, "_run", _fake_run_factory(ran.append))

    import app.core.utils as utils_mod
    import app.services.contract_summary as summary_mod

    monkeypatch.setattr(
        utils_mod, "extract_text_with_config",
        lambda cfg, path: ("合同正文", {"page_count": 1}))
    monkeypatch.setattr(utils_mod, "file_digest", lambda path: "digest-2")

    def boom(*a, **k):
        raise RuntimeError("summary failed")

    monkeypatch.setattr(summary_mod, "build_contract_summary", boom)

    class _Services:
        llm = object()

    job = _job("FAILSUM")
    job["services"] = _Services()
    job["cfg"] = {"contract_summary_enabled": True}

    runner.enqueue(**job)

    assert _wait_idle()
    assert ran == ["FAILSUM"]


def test_has_active_document(monkeypatch):
    _reset()
    monkeypatch.setattr(runner, "_record_progress", lambda *a, **k: None)
    release = threading.Event()
    monkeypatch.setattr(runner, "_run", _fake_run_factory(lambda audit_id: release.wait(5)))

    job = _job("ACTIVE")
    runner.enqueue(**job)
    time.sleep(0.2)

    assert runner.has_active_document("doc-ACTIVE") is True
    assert runner.has_active_document("doc-OTHER") is False

    release.set()
    assert _wait_idle()
    assert runner.has_active_document("doc-ACTIVE") is False
