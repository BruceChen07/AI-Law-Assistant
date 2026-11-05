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


def has_active_document(document_id: str) -> bool:
    """True when the document already has a queued or running audit."""
    target = str(document_id or "")
    if not target:
        return False
    with _lock:
        if any(str(item.get("document_id") or "") == target
               for item in _running.values()):
            return True
        return any(str(job.get("document_id") or "") == target
                   for job in _pending)


def _persist_queue_row(cfg: Dict[str, Any], job: Dict[str, Any]) -> None:
    try:
        from app.services.crud import save_audit_queue_item

        save_audit_queue_item(
            cfg,
            audit_id=str(job.get("audit_id") or ""),
            document_id=str(job.get("document_id") or ""),
            file_path=str(job.get("file_path") or ""),
            lang=str(job.get("lang") or ""),
            retrieval_options=job.get("retrieval_options"),
            title=str(job.get("title") or ""),
            user_id=str(job.get("user_id") or ""),
        )
    except Exception as exc:  # pragma: no cover - persistence is best effort
        logger.warning("audit_queue_persist_failed",
                       audit_id=str(job.get("audit_id") or ""), error=str(exc))


def _set_queue_row_status(cfg: Dict[str, Any], audit_id: str, status: str) -> None:
    try:
        from app.services.crud import update_audit_queue_status

        update_audit_queue_status(cfg, audit_id, status)
    except Exception:  # pragma: no cover
        logger.warning("audit_queue_status_update_failed", audit_id=audit_id)


def _drop_queue_row(cfg: Dict[str, Any], audit_id: str) -> None:
    try:
        from app.services.crud import delete_audit_queue_item

        delete_audit_queue_item(cfg, audit_id)
    except Exception:  # pragma: no cover
        logger.warning("audit_queue_row_delete_failed", audit_id=audit_id)


def _ensure_worker_locked() -> None:
    global _worker_started
    if _worker_started:
        return
    _worker_started = True
    threading.Thread(target=_worker_loop, name="audit-queue-worker",
                     daemon=True).start()


def _worker_loop() -> None:
    while True:
        with _cond:
            while not _pending:
                _cond.wait()
            job = _pending.pop(0)
        audit_id = str(job.get("audit_id") or "")
        cfg = job.get("cfg") or {}
        with _lock:
            _running[audit_id] = {
                "audit_id": audit_id,
                "document_id": str(job.get("document_id") or ""),
                "title": str(job.get("title") or ""),
                "started_at": _utc_now_iso(),
            }
        _set_queue_row_status(cfg, audit_id, "running")
        _record_progress(
            cfg, audit_id,
            status="running", stage="starting", percent=5,
            message="audit started",
            detail={"position": 0, "ahead": 0},
            document_id=str(job.get("document_id") or "") or None,
        )
        try:
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
            _drop_queue_row(cfg, audit_id)
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
    """Queue an audit (FIFO, one at a time) and return its queue position.

    The local llama.cpp server has a single slot, so serialising audits keeps one
    long contract from starving every other request. Queued jobs are persisted and
    recovered after a restart.
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
    # Write the queued state and the durable row BEFORE the worker can pick the job,
    # otherwise a fast audit could finish first and then be overwritten as "queued".
    with _cond:
        _pending.append(job)
        position = len(_pending)
        running_count = len(_running)
        ahead = (1 if running_count else 0) + (position - 1)
        _record_progress(
            cfg, audit_id,
            status="queued", stage="queued", percent=0,
            message=f"waiting in queue (position {position})",
            detail={"position": position, "ahead": ahead,
                    "document_id": document_id},
            document_id=document_id or None,
        )
        _persist_queue_row(cfg, job)
        _ensure_worker_locked()
        _cond.notify()
    logger.info("audit_task_enqueued", audit_id=audit_id,
                position=position, ahead=ahead)
    return {"audit_id": audit_id, "position": position,
            "ahead": ahead, "queued": True}


def submit(**kwargs: Any) -> Dict[str, Any]:
    """Backwards-compatible alias for enqueue()."""
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
    """Cancel a queued audit; a running audit cannot be cancelled."""
    target = str(audit_id or "")
    cfg: Dict[str, Any] = {}
    document_id = ""
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
    _drop_queue_row(cfg, target)
    logger.info("audit_task_cancelled", audit_id=target)
    return "cancelled"


def resume_pending(
    cfg: Dict[str, Any],
    services_factory: Optional[Callable[[], Any]] = None,
) -> int:
    """Re-enqueue audits left queued/running by a previous process."""
    resumed = 0
    try:
        from app.services.crud import list_audit_queue_items

        items = list_audit_queue_items(cfg, statuses=("queued", "running"))
    except Exception as exc:  # pragma: no cover - startup must never fail
        logger.warning("audit_queue_resume_read_failed", error=str(exc))
        return 0
    for row in items:
        audit_id = str(row.get("audit_id") or "")
        file_path = str(row.get("file_path") or "")
        if not audit_id or not file_path:
            continue
        if is_audit_running(audit_id):
            continue
        if not callable(services_factory):
            _set_queue_row_status(cfg, audit_id, "queued")
            continue
        try:
            services = services_factory()
        except Exception:  # pragma: no cover
            logger.exception("audit_queue_resume_services_failed",
                             audit_id=audit_id)
            continue
        _set_queue_row_status(cfg, audit_id, "queued")
        enqueue(
            cfg=cfg,
            services=services,
            file_path=file_path,
            lang=str(row.get("lang") or "zh"),
            retrieval_options=_loads_json(row.get("retrieval_options_json")),
            audit_id=audit_id,
            document_id=str(row.get("document_id") or ""),
            user_id=str(row.get("user_id") or ""),
            title=str(row.get("title") or ""),
        )
        resumed += 1
    if resumed:
        logger.info("audit_queue_resumed count=%s", resumed)
    return resumed


def _loads_json(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(str(raw or "") or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


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
