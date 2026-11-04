"""Background contract audit runner.

P0: submit and execution are decoupled. The HTTP submit endpoint writes the file,
registers a durable progress row and returns 202 immediately; this runner performs
the actual pipeline call in a daemon thread and records the outcome, so a client
timeout, closed tab or dropped connection can no longer abort or orphan an audit.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import structlog

from app.services.audit_artifacts import write_audit_artifacts
from app.services.audit_error_messages import build_audit_error_detail
from app.services.crud import (
    insert_contract_audit,
    list_contract_audit_chunks,
    upsert_contract_audit_progress,
)

logger = structlog.get_logger(__name__)

_running: Dict[str, Dict[str, Any]] = {}
_pending: List[Dict[str, Any]] = []
_lock = threading.Lock()
_cond = threading.Condition(_lock)
_worker_started = False

# Small subset of the audit meta the UI needs to restore a finished task.
_RESULT_META_KEYS = (
    "language",
    "text_length",
    "ocr_used",
    "ocr_engine",
    "page_count",
    "execution_path",
    "engine_version",
    "plan_version",
    "chunks_total",
    "chunks_completed",
    "coverage_planned",
    "coverage_audited",
    "chunk_reused_count",
    "review_retry_applied_count",
    "reliability",
)


def _trim_result_meta(meta: Any) -> Dict[str, Any]:
    if not isinstance(meta, dict):
        return {}
    return {key: meta[key] for key in _RESULT_META_KEYS if key in meta}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def running_audit_ids() -> list[str]:
    with _lock:
        return sorted(_running.keys())


def is_audit_running(audit_id: str) -> bool:
    with _lock:
        return str(audit_id or "") in _running


def _record_progress(
    cfg: Dict[str, Any],
    audit_id: str,
    *,
    status: str,
    stage: str,
    percent: int,
    message: str,
    detail: Optional[Dict[str, Any]] = None,
    document_id: Optional[str] = None,
) -> None:
    try:
        upsert_contract_audit_progress(
            cfg,
            audit_id=audit_id,
            status=status,
            stage=stage,
            percent=percent,
            message=message,
            detail=detail,
            document_id=document_id,
        )
    except Exception as exc:  # pragma: no cover - durability is best effort
        logger.warning("audit_task_progress_write_failed",
                       audit_id=audit_id, error=str(exc))


def _ensure_worker_locked() -> None:
    global _worker_started
    if _worker_started:
        return
    _worker_started = True
    threading.Thread(
        target=_worker_loop,
        name="audit-queue-worker",
        daemon=True,
    ).start()


def _worker_loop() -> None:
    while True:
        with _cond:
            while not _pending:
                _cond.wait()
            job = _pending.pop(0)
        audit_id = str(job.get("audit_id") or "")
        with _lock:
            _running[audit_id] = {
                "audit_id": audit_id,
                "document_id": str(job.get("document_id") or ""),
                "title": str(job.get("title") or ""),
                "started_at": _utc_now_iso(),
            }
        _record_progress(
            job["cfg"], audit_id,
            status="running", stage="starting", percent=1,
            message="audit started",
            document_id=str(job.get("document_id") or "") or None,
        )
        try:
            # 只传 _run 认识的参数；job 里还带 title 等展示用字段。
            _run(**{
                key: job[key]
                for key in (
                    "cfg", "services", "file_path", "lang",
                    "retrieval_options", "audit_id", "document_id",
                    "user_id", "on_finish",
                )
            })
        except Exception:  # pragma: no cover - worker must survive any job
            logger.exception("audit_queue_worker_failed", audit_id=audit_id)
        finally:
            with _lock:
                _running.pop(audit_id, None)


def enqueue(
    *,
    cfg: Dict[str, Any],
    services: Any,
    file_path: str,
    lang: str,
    retrieval_options: Optional[Dict[str, Any]],
    audit_id: str,
    document_id: str,
    user_id: str = "",
    on_finish: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    title: str = "",
) -> Dict[str, Any]:
    """把审计加入 FIFO 队列；空闲时由唯一的工作线程立即执行。

    同一时刻只跑一个审计，避免多个任务争抢同一个 llama.cpp 槽位。
    """
    job = {
        "cfg": cfg,
        "services": services,
        "file_path": file_path,
        "lang": lang,
        "retrieval_options": retrieval_options,
        "audit_id": audit_id,
        "document_id": document_id,
        "user_id": user_id,
        "on_finish": on_finish,
        "title": title,
    }
    with _cond:
        _pending.append(job)
        position = len(_pending)
        running_count = len(_running)
        _ensure_worker_locked()
        _cond.notify()
    # ahead：前面还有几个任务（含正在执行的那个），用于前端提示“前面还有 N 个”。
    ahead = (1 if running_count else 0) + (position - 1)
    _record_progress(
        cfg, audit_id,
        status="queued", stage="queued", percent=0,
        message=f"waiting in queue (position {position})",
        detail={"position": position, "ahead": ahead},
        document_id=document_id or None,
    )
    logger.info("audit_task_enqueued", audit_id=audit_id,
                position=position, ahead=ahead)
    return {
        "audit_id": audit_id,
        "position": position,
        "ahead": ahead,
        "queued": True,
    }


def submit(**kwargs: Any) -> Dict[str, Any]:
    """兼容旧调用：等价于 enqueue。"""
    return enqueue(**kwargs)


def queue_snapshot() -> Dict[str, Any]:
    with _lock:
        queued = [
            {
                "audit_id": str(job.get("audit_id") or ""),
                "document_id": str(job.get("document_id") or ""),
                "title": str(job.get("title") or ""),
                "position": index + 1,
            }
            for index, job in enumerate(_pending)
        ]
        running = [dict(item) for item in _running.values()]
    return {
        "running": running,
        "queued": queued,
        "running_count": len(running),
        "queued_count": len(queued),
    }


def cancel(audit_id: str) -> str:
    """取消排队中的任务；已开始执行的不允许取消。"""
    target = str(audit_id or "")
    with _lock:
        if target in _running:
            return "running"
        for index, job in enumerate(_pending):
            if str(job.get("audit_id") or "") == target:
                _pending.pop(index)
                cfg = job.get("cfg") or {}
                document_id = str(job.get("document_id") or "")
                break
        else:
            return "missing"
    _record_progress(
        cfg, target,
        status="cancelled", stage="cancelled", percent=100,
        message="cancelled by user",
        detail={"cancelled": True},
        document_id=document_id or None,
    )
    logger.info("audit_task_cancelled", audit_id=target)
    return "cancelled"


def _run(
    *,
    cfg: Dict[str, Any],
    services: Any,
    file_path: str,
    lang: str,
    retrieval_options: Optional[Dict[str, Any]],
    audit_id: str,
    document_id: str,
    user_id: str,
    on_finish: Optional[Callable[[str, Dict[str, Any]], None]],
) -> None:
    from app.services.audit_orchestrator import run_contract_pipeline_bundle

    model_cfg = cfg.get("llm_config") or {}
    started = time.perf_counter()
    result: Dict[str, Any] = {}
    status = "done"
    error_message = ""
    error_detail = ""

    def _progress_cb(stage: str, percent: int, message: str = "", detail=None) -> None:
        _record_progress(
            cfg,
            audit_id,
            status="running",
            stage=stage,
            percent=percent,
            message=message,
            detail=detail,
            document_id=document_id,
        )

    try:
        result = run_contract_pipeline_bundle(
            cfg,
            services,
            file_path=file_path,
            lang=lang,
            retrieval_options=retrieval_options,
            progress_cb=_progress_cb,
            audit_id=audit_id,
        )
        insert_contract_audit(
            cfg,
            audit_id=audit_id,
            document_id=document_id,
            status="done",
            result_json=json.dumps(result.get("audit"), ensure_ascii=False),
            model_provider=str(model_cfg.get("provider", "")),
            model_name=str(model_cfg.get("model", "")),
            created_at=_utc_now_iso(),
        )
        _record_progress(
            cfg,
            audit_id,
            status="done",
            stage="done",
            percent=100,
            message="completed",
            detail={
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "execution_path": str(((result.get("meta") or {}) or {}).get("execution_path") or ""),
                "result_meta": _trim_result_meta(result.get("meta")),
            },
            document_id=document_id,
        )
        logger.info(
            "audit_task_done audit_id=%s document_id=%s duration_ms=%s",
            audit_id, document_id, int((time.perf_counter() - started) * 1000),
        )
    except Exception as exc:
        status = "failed"
        error_message = str(exc)
        error_detail = build_audit_error_detail(exc, lang)
        logger.exception(
            "audit_task_failed audit_id=%s document_id=%s file=%s",
            audit_id, document_id, file_path,
        )
        try:
            insert_contract_audit(
                cfg,
                audit_id=audit_id,
                document_id=document_id,
                status="failed",
                result_json=json.dumps(
                    {"error": error_message, "detail": error_detail},
                    ensure_ascii=False,
                ),
                model_provider=str(model_cfg.get("provider", "")),
                model_name=str(model_cfg.get("model", "")),
                created_at=_utc_now_iso(),
            )
        except Exception as write_exc:  # pragma: no cover - best effort
            logger.warning("audit_task_failure_write_failed",
                           audit_id=audit_id, error=str(write_exc))
        _record_progress(
            cfg,
            audit_id,
            status="failed",
            stage="failed",
            percent=100,
            message=error_detail,
            detail={
                "error": error_message,
                "duration_ms": int((time.perf_counter() - started) * 1000),
            },
            document_id=document_id,
        )
    finally:
        # Mirror the outcome to a plain-file directory so results stay readable
        # even when the UI is closed or the database is unavailable.
        write_audit_artifacts(
            cfg,
            audit_id=audit_id,
            document_id=document_id,
            status=status,
            audit=result.get("audit") if status == "done" else None,
            meta=_trim_result_meta(result.get("meta")) if status == "done" else {},
            error=(
                {"error": error_message, "detail": error_detail}
                if status != "done"
                else None
            ),
            chunks=list_contract_audit_chunks(cfg, audit_id),
            created_at=_utc_now_iso(),
        )
        with _lock:
            _running.pop(audit_id, None)
        if callable(on_finish):
            try:
                on_finish(
                    audit_id,
                    {
                        "status": status,
                        "document_id": document_id,
                        "result": result.get("audit") if status == "done" else None,
                        "meta": result.get("meta") if status == "done" else None,
                        "error": error_message,
                        "detail": error_detail,
                    },
                )
            except Exception:  # pragma: no cover - callback must not break the task
                logger.warning("audit_task_callback_failed", audit_id=audit_id)
