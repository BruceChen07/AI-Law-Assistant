from __future__ import annotations

from typing import Any, Dict, List, Tuple


def _is_enabled(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def get_audit_reliability_policy(cfg: Dict[str, Any]) -> Dict[str, Any]:
    raw = cfg.get("audit_reliability_policy") if isinstance(
        cfg.get("audit_reliability_policy"), dict) else {}
    return {
        "enabled": _is_enabled(raw.get("enabled"), True),
        "retry_enabled": _is_enabled(raw.get("retry_enabled"), True),
        "retry_on_parse_failed": _is_enabled(raw.get("retry_on_parse_failed"), True),
        "retry_on_truncated": _is_enabled(raw.get("retry_on_truncated"), True),
        "retry_on_high_risk_missing_citation": _is_enabled(
            raw.get("retry_on_high_risk_missing_citation"), True),
        "retry_on_conflict": _is_enabled(raw.get("retry_on_conflict"), True),
        "max_retry_per_chunk": max(
            0, _safe_int(raw.get("max_retry_per_chunk"), 1)),
        "max_retry_chunks": max(0, _safe_int(raw.get("max_retry_chunks"), 6)),
    }


def _chunk_by_id(chunk_audit_results: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {
        str(item.get("chunk_id") or ""): item
        for item in list(chunk_audit_results or [])
        if isinstance(item, dict) and str(item.get("chunk_id") or "").strip()
    }


def _chunk_for_clause_id(
    chunk_audit_results: List[Dict[str, Any]],
    clause_id: str,
) -> str:
    target = str(clause_id or "").strip()
    if not target:
        return ""
    for item in list(chunk_audit_results or []):
        if not isinstance(item, dict):
            continue
        clause_range = item.get("clause_range") if isinstance(
            item.get("clause_range"), dict) else {}
        clause_ids = [
            str(x).strip()
            for x in list(clause_range.get("clause_ids") or [])
            if str(x).strip()
        ]
        if target in clause_ids:
            return str(item.get("chunk_id") or "")
    return ""


def _append_reason(
    retry_map: Dict[str, Dict[str, Any]],
    chunk_id: str,
    reason: str,
    detail: Dict[str, Any],
) -> None:
    cid = str(chunk_id or "").strip()
    if not cid:
        return
    row = retry_map.setdefault(
        cid,
        {"chunk_id": cid, "reasons": [], "details": []},
    )
    if reason not in row["reasons"]:
        row["reasons"].append(reason)
    row["details"].append(detail)


def _reliability_level(review_count: int, retry_count: int, unresolved_count: int) -> str:
    if unresolved_count > 0:
        return "low"
    if review_count > 0 and retry_count > 0:
        return "medium"
    if review_count > 0:
        return "medium"
    return "high"


def build_reliability_review_plan(
    *,
    cfg: Dict[str, Any],
    aggregated_audit: Dict[str, Any],
    aggregated_meta: Dict[str, Any],
    chunk_audit_results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    policy = get_audit_reliability_policy(cfg)
    chunk_map = _chunk_by_id(chunk_audit_results)
    review_items = list(
        ((aggregated_audit or {}).get("legal_validation") or {}).get("issues") or [])
    retry_map: Dict[str, Dict[str, Any]] = {}

    for item in review_items:
        if not isinstance(item, dict):
            continue
        reason = str(item.get("reason") or "").strip()
        if reason == "parse_failed" and not policy["retry_on_parse_failed"]:
            continue
        if reason == "truncated" and not policy["retry_on_truncated"]:
            continue
        if reason == "high_risk_missing_citation" and not policy["retry_on_high_risk_missing_citation"]:
            continue
        chunk_id = str(item.get("chunk_id") or "").strip()
        if not chunk_id:
            chunk_id = _chunk_for_clause_id(
                chunk_audit_results, str(item.get("clause_id") or ""))
        _append_reason(retry_map, chunk_id, reason or "review_item", item)

    if policy["retry_on_conflict"]:
        for item in list(aggregated_meta.get("removed_conflicts") or []):
            if not isinstance(item, dict):
                continue
            chunk_id = _chunk_for_clause_id(
                chunk_audit_results, str(item.get("clause_id") or ""))
            if not chunk_id:
                chunk_id = _chunk_for_clause_id(
                    chunk_audit_results, str(item.get("counter_clause_id") or ""))
            _append_reason(retry_map, chunk_id, "conflict_detected", item)

    planned_chunks = list(retry_map.values())
    planned_chunks.sort(
        key=lambda x: (
            0 if "parse_failed" in x["reasons"] else 1,
            0 if "truncated" in x["reasons"] else 1,
            len(x["reasons"]) * -1,
            x["chunk_id"],
        )
    )
    if policy["max_retry_chunks"] > 0:
        planned_chunks = planned_chunks[: int(policy["max_retry_chunks"])]
    retry_chunk_ids = [str(item.get("chunk_id") or "") for item in planned_chunks]
    return {
        "policy": policy,
        "review_items": review_items,
        "retry_chunks": planned_chunks,
        "retry_chunk_ids": retry_chunk_ids,
        "should_retry": bool(policy["enabled"] and policy["retry_enabled"] and retry_chunk_ids),
        "review_item_count": len(review_items),
        "initial_reliability_level": _reliability_level(
            len(review_items), 0, len(review_items)),
        "chunk_index": list(chunk_map.keys()),
    }


def build_reliability_summary(
    *,
    review_plan: Dict[str, Any],
    retry_logs: List[Dict[str, Any]],
    final_audit: Dict[str, Any],
) -> Dict[str, Any]:
    review_items = list((review_plan or {}).get("review_items") or [])
    unresolved = list(
        ((final_audit or {}).get("legal_validation") or {}).get("issues") or [])
    retry_count = sum(1 for item in retry_logs if bool(item.get("retry_performed")))
    return {
        "review_item_count": len(review_items),
        "retry_count": retry_count,
        "unresolved_review_item_count": len(unresolved),
        "reliability_level": _reliability_level(
            len(review_items), retry_count, len(unresolved)),
    }
