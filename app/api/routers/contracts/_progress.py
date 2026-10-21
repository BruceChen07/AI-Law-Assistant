"""In-memory audit progress registry shared by the contract routes."""
import hashlib
import logging
import threading
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from app.services.contract_audit_stage_log import write_contract_audit_stage_log

logger = logging.getLogger("law_assistant")

_audit_progress: Dict[str, Dict[str, Any]] = {}


_audit_progress_lock = threading.Lock()


_AUDIT_STAGE_TITLES = {
    "received": "request_received",
    "saved": "file_saved",
    "document": "document_recorded",
    "digitizing": "pdf_digitizing",
    "digitized": "digitized_word_ready",
    "extracting": "text_extracting",
    "extract_done": "text_extract_done",
    "parsing": "contract_parsing",
    "parsing_done": "contract_parse_done",
    "retrieval": "evidence_retrieving",
    "retrieval_done": "evidence_ready",
    "auditing": "ai_auditing",
    "reviewing": "high_risk_reviewing",
    "audit_done": "audit_result_assembling",
    "done": "audit_completed",
    "failed": "audit_failed",
}


def _set_audit_progress(audit_id: str, status: str, progress: int, stage: str, message: str = "") -> None:
    payload = {
        "audit_id": audit_id,
        "status": status,
        "progress": int(progress),
        "stage": stage,
        "message": message,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    should_log = True
    with _audit_progress_lock:
        previous = _audit_progress.get(audit_id)
        if isinstance(previous, dict):
            should_log = any([
                str(previous.get("status") or "") != payload["status"],
                int(previous.get("progress") or 0) != payload["progress"],
                str(previous.get("stage") or "") != payload["stage"],
                str(previous.get("message") or "") != payload["message"],
            ])
        _audit_progress[audit_id] = payload
    if should_log:
        logger.info(
            "contract_audit_progress audit_id=%s status=%s progress=%s stage=%s task=%s message=%s",
            audit_id,
            payload["status"],
            payload["progress"],
            payload["stage"],
            _AUDIT_STAGE_TITLES.get(payload["stage"], payload["stage"]),
            payload["message"],
        )


def _get_audit_progress(audit_id: str) -> Optional[Dict[str, Any]]:
    with _audit_progress_lock:
        return _audit_progress.get(audit_id)


def _make_audit_progress_writer(cfg: Dict[str, Any], audit_id: str):
    def _update(
        status: str,
        progress: int,
        stage: str,
        message: str = "",
        *,
        stage_result: str = "",
        audit_opinion: str = "",
        payload: Optional[Dict[str, Any]] = None,
        occurred_at_utc: str = "",
    ) -> None:
        _set_audit_progress(audit_id, status, progress, stage, message)
        stage_payload = dict(payload or {})
        stage_payload.setdefault("progress", int(progress))
        stage_payload.setdefault("status", str(status or ""))
        resolved_stage_result = str(
            stage_result or (
                "success" if status == "done" else "failed" if status == "failed" else "running")
        )
        event_key = "contract-audit-progress:" + hashlib.sha1(
            "|".join(
                [
                    str(audit_id or ""),
                    str(status or ""),
                    str(progress),
                    str(stage or ""),
                    str(message or ""),
                    resolved_stage_result,
                ]
            ).encode("utf-8")
        ).hexdigest()
        try:
            write_contract_audit_stage_log(
                cfg,
                audit_id=audit_id,
                stage_code=stage,
                stage_status=str(status or ""),
                stage_result=resolved_stage_result,
                stage_message=message,
                audit_opinion=audit_opinion,
                payload=stage_payload,
                occurred_at_utc=occurred_at_utc,
                event_key=event_key,
            )
        except Exception:
            logger.exception(
                "contract_audit_stage_log_write_failed audit_id=%s stage=%s",
                audit_id,
                stage,
            )

    return _update
