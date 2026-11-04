"""
Chunk Store.
职责: 把分批审计的每个 chunk 结果即时落库，并支持按 (source_hash, plan_hash) 复用已完成 chunk。
输入/输出: 接收审计上下文与 chunk 计划/结果，返回可复用的历史 chunk 结果或空字典。
异常场景: 落库失败只记录日志，绝不阻断审计主流程。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Sequence

import structlog

from app.services.crud import (
    find_reusable_contract_audit_chunks,
    upsert_contract_audit_chunk,
    upsert_contract_audit_progress,
)

logger = structlog.get_logger(__name__)

DEFAULT_MAX_RAW_CHARS = 20000


def _safe_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def get_chunk_policy(cfg: Dict[str, Any]) -> Dict[str, Any]:
    raw = cfg.get("audit_chunk_persistence") if isinstance(
        cfg.get("audit_chunk_persistence"), dict) else {}
    try:
        max_raw = int(raw.get("max_raw_chars") or DEFAULT_MAX_RAW_CHARS)
    except Exception:
        max_raw = DEFAULT_MAX_RAW_CHARS
    return {
        "enabled": _safe_bool(raw.get("enabled"), True),
        "reuse_enabled": _safe_bool(raw.get("reuse_enabled"), True),
        "store_raw_response": _safe_bool(raw.get("store_raw_response"), True),
        "max_raw_chars": max(1000, max_raw),
    }


def source_signature(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def chunk_plan_signature(plan: Any) -> str:
    """Stable signature over chunk boundaries so a re-run can reuse finished chunks."""
    parts: List[str] = []
    for chunk in list(getattr(plan, "chunks", []) or []):
        bundle = getattr(chunk, "bundle", None)
        parts.append(
            "|".join(
                [
                    str(getattr(chunk, "chunk_id", "") or ""),
                    ",".join(str(x) for x in (getattr(bundle, "primary_unit_ids", ()) or ())),
                    str(getattr(bundle, "prompt_hash", "") or ""),
                ]
            )
        )
    payload = "\n".join(parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def chunk_clause_ids(chunk_clauses: Sequence[Dict[str, Any]]) -> List[str]:
    return [
        str(item.get("clause_id") or "")
        for item in list(chunk_clauses or [])
        if isinstance(item, dict) and str(item.get("clause_id") or "")
    ]


def chunk_source_spans(chunk: Any) -> List[Dict[str, Any]]:
    spans: List[Dict[str, Any]] = []
    for unit in list(getattr(chunk, "units", ()) or ()):
        for ref in list(getattr(unit, "source_refs", ()) or ()):
            spans.append(
                {
                    "start": int(getattr(ref, "start", 0) or 0),
                    "end": int(getattr(ref, "end", 0) or 0),
                    "unit_id": str(getattr(unit, "unit_id", "") or ""),
                    "clause_ids": [
                        str(x) for x in (getattr(ref, "preview_clause_ids", ()) or ())
                    ],
                }
            )
    return spans


def plan_snapshot(plan: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for chunk in list(getattr(plan, "chunks", []) or []):
        bundle = getattr(chunk, "bundle", None)
        unit_ids = [str(x) for x in (getattr(bundle, "primary_unit_ids", ()) or ())]
        clause_ids: List[str] = []
        for unit in list(getattr(chunk, "units", ()) or ()):
            for ref in list(getattr(unit, "source_refs", ()) or ()):
                clause_ids.extend(str(x) for x in (getattr(ref, "preview_clause_ids", ()) or ()))
        rows.append(
            {
                "chunk_id": str(getattr(chunk, "chunk_id", "") or ""),
                "ordinal": int(getattr(chunk, "ordinal", 0) or 0),
                "unit_ids": unit_ids,
                "clause_ids": clause_ids,
                "input_tokens": int(getattr(bundle, "input_tokens", 0) or 0),
                "output_tokens": int(getattr(bundle, "output_tokens", 0) or 0),
            }
        )
    return rows


def persist_plan(
    cfg: Dict[str, Any],
    *,
    audit_id: str,
    source_hash: str,
    plan_hash: str,
    plan: Any,
    chunks_total: int,
) -> None:
    policy = get_chunk_policy(cfg)
    if not policy["enabled"]:
        return
    try:
        upsert_contract_audit_progress(
            cfg,
            audit_id=audit_id,
            status="running",
            stage="planning",
            percent=25,
            message=f"planned {chunks_total} audit chunks",
            detail={
                "execution_path": "classic_v2",
                "chunks_total": int(chunks_total),
                "plan_hash": plan_hash,
                "source_hash": source_hash,
            },
            plan_json={"plan_hash": plan_hash, "source_hash": source_hash, "chunks": plan_snapshot(plan)},
            plan_hash=plan_hash,
            source_hash=source_hash,
            chunks_total=int(chunks_total),
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("audit_plan_persist_failed", audit_id=audit_id, error=str(exc))


def record_chunk(
    cfg: Dict[str, Any],
    *,
    audit_id: str,
    chunk: Any,
    source_hash: str,
    plan_hash: str,
    chunk_clauses: Sequence[Dict[str, Any]],
    attempts: int,
    status: str,
    raw_response: str = "",
    parsed: Optional[Dict[str, Any]] = None,
    error: str = "",
    reviewed: bool = False,
) -> None:
    policy = get_chunk_policy(cfg)
    if not policy["enabled"]:
        return
    bundle = getattr(chunk, "bundle", None)
    raw_text = str(raw_response or "")
    if not policy["store_raw_response"]:
        raw_text = ""
    elif len(raw_text) > policy["max_raw_chars"]:
        raw_text = raw_text[: policy["max_raw_chars"]]
    try:
        upsert_contract_audit_chunk(
            cfg,
            audit_id=audit_id,
            chunk_id=str(getattr(chunk, "chunk_id", "") or ""),
            ordinal=int(getattr(chunk, "ordinal", 0) or 0),
            source_hash=source_hash,
            plan_hash=plan_hash,
            clause_ids=chunk_clause_ids(chunk_clauses),
            source_spans=chunk_source_spans(chunk),
            prompt_hash=str(getattr(bundle, "prompt_hash", "") or ""),
            prefix_hash=str(getattr(bundle, "prefix_hash", "") or ""),
            input_tokens=int(getattr(bundle, "input_tokens", 0) or 0),
            output_tokens=int(getattr(bundle, "output_tokens", 0) or 0),
            attempts=int(attempts or 0),
            status=str(status or ""),
            raw_response=raw_text,
            parsed_json=parsed,
            error=str(error or ""),
            reviewed=1 if reviewed else 0,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "audit_chunk_persist_failed",
            audit_id=audit_id,
            chunk_id=str(getattr(chunk, "chunk_id", "") or ""),
            error=str(exc),
        )


def load_reusable_chunks(
    cfg: Dict[str, Any],
    *,
    source_hash: str,
    plan_hash: str,
) -> Dict[str, Dict[str, Any]]:
    """Completed chunk results for an identical source + plan, keyed by chunk_id."""
    policy = get_chunk_policy(cfg)
    if not policy["enabled"] or not policy["reuse_enabled"]:
        return {}
    if not source_hash or not plan_hash:
        return {}
    try:
        rows = find_reusable_contract_audit_chunks(cfg, source_hash, plan_hash)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("audit_chunk_reuse_lookup_failed", error=str(exc))
        return {}
    reusable: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        chunk_id = str(row.get("chunk_id") or "")
        if not chunk_id or chunk_id in reusable:
            continue
        parsed = row.get("parsed_json") or ""
        try:
            parsed_obj = json.loads(parsed) if parsed else None
        except Exception:
            parsed_obj = None
        if not isinstance(parsed_obj, dict):
            continue
        reusable[chunk_id] = {
            "parsed": parsed_obj,
            "raw_response": str(row.get("raw_response") or ""),
            "output_tokens": int(row.get("output_tokens") or 0),
            "attempts": int(row.get("attempts") or 1),
            "reviewed": bool(int(row.get("reviewed") or 0)),
            "source_audit_id": str(row.get("audit_id") or ""),
        }
    return reusable


def reusable_round_entry(
    *,
    chunk: Any,
    chunk_clauses: Sequence[Dict[str, Any]],
    reusable: Dict[str, Any],
    index: int,
) -> Dict[str, Any]:
    """Rehydrate an in-memory round entry from a stored chunk result."""
    bundle = getattr(chunk, "bundle", None)
    parsed = reusable.get("parsed") if isinstance(reusable.get("parsed"), dict) else {}
    return {
        "round_index": index,
        "chunk_id": str(getattr(chunk, "chunk_id", "") or ""),
        "clause_ids": chunk_clause_ids(chunk_clauses),
        "estimated_clause_tokens": int(getattr(bundle, "input_tokens", 0) or 0),
        "audit": {},
        "meta": {
            "classic_parse_failed": False,
            "classic_response_truncated": False,
            "classic_completion_tokens": int(parsed.get("completion_tokens") or 0),
            "classic_max_tokens": int(parsed.get("max_output_tokens") or 0),
            "chunk_reused": True,
            "chunk_reused_from_audit_id": str(reusable.get("source_audit_id") or ""),
        },
        "chunk_audit_result": parsed,
        "rejected_risks": [],
        "attempt_count": int(reusable.get("attempts") or 1),
        "retry_index": 0,
    }
