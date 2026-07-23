from typing import Any, Callable, Dict, Optional


def report_progress(
    progress_cb: Optional[Callable[..., None]],
    stage: str,
    percent: int,
    message: str = "",
    detail: Optional[Dict[str, Any]] = None,
) -> None:
    if not callable(progress_cb):
        return
    try:
        progress_cb(stage, int(percent), message, detail)
    except TypeError:
        try:
            progress_cb(stage, int(percent), message)
        except Exception:
            return
    except Exception:
        return


def compute_llm_progress_percent(
    completed_count: int,
    total_count: int,
    *,
    base_percent: int = 70,
    span: int = 25,
) -> int:
    total = max(1, int(total_count or 0))
    completed = max(0, min(int(completed_count or 0), total))
    return min(99, max(int(base_percent or 0), int(base_percent or 0) + int(round((int(span or 0) * completed) / total))))


def build_llm_progress_detail(
    *,
    execution_path: str,
    total_planned: int = 0,
    total_upper_bound: int = 0,
    exact_total_known: bool = False,
    started: int = 0,
    completed: int = 0,
    current_index: int = 0,
    current_label: str = "",
    request_kind: str = "",
    request_status: str = "",
    retry_index: int = 0,
    round_index: int = 0,
    clause_id: str = "",
    last_completed_label: str = "",
    last_error: str = "",
) -> Dict[str, Any]:
    planned = max(0, int(total_planned or 0))
    upper_bound = max(planned, int(total_upper_bound or 0))
    return {
        "execution_path": str(execution_path or ""),
        "llm_request_total_planned": planned,
        "llm_request_total_upper_bound": upper_bound,
        "llm_request_exact_total_known": bool(exact_total_known),
        "llm_request_started": max(0, int(started or 0)),
        "llm_request_completed": max(0, int(completed or 0)),
        "llm_request_current_index": max(0, int(current_index or 0)),
        "llm_request_current_label": str(current_label or ""),
        "llm_request_kind": str(request_kind or ""),
        "llm_request_status": str(request_status or ""),
        "llm_request_retry_index": max(0, int(retry_index or 0)),
        "llm_request_round_index": max(0, int(round_index or 0)),
        "llm_request_clause_id": str(clause_id or ""),
        "llm_request_last_completed_label": str(last_completed_label or ""),
        "llm_request_last_error": str(last_error or ""),
    }


def build_llm_progress_message(detail: Optional[Dict[str, Any]]) -> str:
    info = detail if isinstance(detail, dict) else {}
    current_index = int(info.get("llm_request_current_index") or 0)
    total = int(info.get("llm_request_total_planned") or 0)
    if total <= 0:
        total = int(info.get("llm_request_total_upper_bound") or 0)
    current_label = str(info.get("llm_request_current_label") or "").strip()
    request_status = str(info.get("llm_request_status") or "").strip().lower()
    if current_index > 0 and total > 0 and current_label:
        return f"llm request {current_index}/{total}: {current_label} ({request_status or 'running'})"
    if current_index > 0 and total > 0:
        return f"llm request {current_index}/{total} ({request_status or 'running'})"
    if total > 0:
        return f"llm request planned: {total}"
    return str(request_status or "auditing")
