import csv
import hashlib
import json
import logging
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence


logger = logging.getLogger("law_assistant")

_CONTEXT_LOCK = threading.Lock()
_RUNTIME_CONTEXTS: Dict[str, Dict[str, Any]] = {}

_EXPORT_FIELDS = [
    "id",
    "event_key",
    "audit_id",
    "document_id",
    "contract_name",
    "occurred_at_utc",
    "stage_code",
    "stage_status",
    "stage_result",
    "stage_message",
    "audit_opinion",
    "operator_id",
    "ip_address",
    "source_endpoint",
    "payload",
]


def _utc_now_iso_ms() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _normalize_utc_iso_ms(value: Optional[str]) -> str:
    raw = str(value or "").strip()
    if not raw:
        return _utc_now_iso_ms()
    try:
        if raw.endswith("Z"):
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        else:
            parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except ValueError:
        return _utc_now_iso_ms()


def _db_path(cfg: Dict[str, Any]) -> str:
    configured = str(cfg.get("contract_audit_log_db_path") or "").strip()
    if configured:
        return configured
    data_dir = str(cfg.get("data_dir") or "./data").strip()
    return os.path.join(data_dir, "audit_logs", "contract_audit_stage_logs.db")


def _export_dir(cfg: Dict[str, Any]) -> str:
    configured = str(cfg.get("contract_audit_log_export_dir") or "").strip()
    if configured:
        return configured
    data_dir = str(cfg.get("data_dir") or "./data").strip()
    return os.path.join(data_dir, "exports", "contract_audit_logs")


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _get_conn(cfg: Dict[str, Any]) -> sqlite3.Connection:
    path = _db_path(cfg)
    _ensure_parent_dir(path)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init_contract_audit_log_db(cfg: Dict[str, Any]) -> None:
    with _get_conn(cfg) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS contract_audit_stage_log(
            id TEXT PRIMARY KEY,
            event_key TEXT NOT NULL UNIQUE,
            audit_id TEXT NOT NULL,
            document_id TEXT,
            contract_name TEXT NOT NULL,
            occurred_at_utc TEXT NOT NULL,
            stage_code TEXT NOT NULL,
            stage_status TEXT NOT NULL,
            stage_result TEXT NOT NULL,
            stage_message TEXT,
            audit_opinion TEXT,
            operator_id TEXT NOT NULL,
            ip_address TEXT,
            source_endpoint TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_contract_audit_stage_log_audit_id ON contract_audit_stage_log(audit_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_contract_audit_stage_log_contract_name ON contract_audit_stage_log(contract_name)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_contract_audit_stage_log_occurred_at ON contract_audit_stage_log(occurred_at_utc)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_contract_audit_stage_log_stage_code ON contract_audit_stage_log(stage_code)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_contract_audit_stage_log_operator_id ON contract_audit_stage_log(operator_id)"
        )


def register_contract_audit_log_context(
    audit_id: str,
    *,
    document_id: str = "",
    contract_name: str = "",
    operator_id: str = "",
    ip_address: str = "",
    source_endpoint: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    with _CONTEXT_LOCK:
        _RUNTIME_CONTEXTS[audit_id] = {
            "document_id": str(document_id or ""),
            "contract_name": str(contract_name or ""),
            "operator_id": str(operator_id or ""),
            "ip_address": str(ip_address or ""),
            "source_endpoint": str(source_endpoint or ""),
            "extra": dict(extra or {}),
        }


def clear_contract_audit_log_context(audit_id: str) -> None:
    with _CONTEXT_LOCK:
        _RUNTIME_CONTEXTS.pop(audit_id, None)


def _get_context(audit_id: str) -> Dict[str, Any]:
    with _CONTEXT_LOCK:
        return dict(_RUNTIME_CONTEXTS.get(audit_id) or {})


def _build_event_key(
    *,
    audit_id: str,
    document_id: str,
    contract_name: str,
    stage_code: str,
    stage_status: str,
    stage_result: str,
    stage_message: str,
    audit_opinion: str,
    occurred_at_utc: str,
    payload_json: str,
    source_endpoint: str,
) -> str:
    digest = hashlib.sha1(
        "|".join(
            [
                str(audit_id or ""),
                str(document_id or ""),
                str(contract_name or ""),
                str(stage_code or ""),
                str(stage_status or ""),
                str(stage_result or ""),
                str(stage_message or ""),
                str(audit_opinion or ""),
                str(occurred_at_utc or ""),
                str(source_endpoint or ""),
                str(payload_json or ""),
            ]
        ).encode("utf-8")
    ).hexdigest()
    return f"contract-audit-stage:{digest}"


def write_contract_audit_stage_log(
    cfg: Dict[str, Any],
    *,
    audit_id: str,
    stage_code: str,
    stage_status: str,
    stage_result: str,
    stage_message: str = "",
    audit_opinion: str = "",
    operator_id: str = "",
    ip_address: str = "",
    contract_name: str = "",
    document_id: str = "",
    source_endpoint: str = "",
    occurred_at_utc: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    event_key: str = "",
) -> Dict[str, Any]:
    init_contract_audit_log_db(cfg)
    context = _get_context(audit_id)
    doc_id = str(document_id or context.get("document_id") or "")
    name = str(contract_name or context.get("contract_name") or "")
    operator = str(operator_id or context.get("operator_id") or "")
    ip = str(ip_address or context.get("ip_address") or "")
    endpoint = str(source_endpoint or context.get("source_endpoint") or "")
    if not name:
        name = doc_id or audit_id
    if not operator:
        operator = "unknown"
    normalized_ts = _normalize_utc_iso_ms(occurred_at_utc)
    payload_obj = dict(context.get("extra") or {})
    payload_obj.update(dict(payload or {}))
    payload_json = json.dumps(payload_obj, ensure_ascii=False, sort_keys=True)
    unique_event_key = str(event_key or "").strip() or _build_event_key(
        audit_id=audit_id,
        document_id=doc_id,
        contract_name=name,
        stage_code=stage_code,
        stage_status=stage_status,
        stage_result=stage_result,
        stage_message=stage_message,
        audit_opinion=audit_opinion,
        occurred_at_utc=normalized_ts,
        payload_json=payload_json,
        source_endpoint=endpoint,
    )
    row_id = str(uuid.uuid5(uuid.NAMESPACE_URL, unique_event_key))
    now = _utc_now_iso_ms()
    record = {
        "id": row_id,
        "event_key": unique_event_key,
        "audit_id": str(audit_id or ""),
        "document_id": doc_id,
        "contract_name": name,
        "occurred_at_utc": normalized_ts,
        "stage_code": str(stage_code or ""),
        "stage_status": str(stage_status or ""),
        "stage_result": str(stage_result or ""),
        "stage_message": str(stage_message or ""),
        "audit_opinion": str(audit_opinion or ""),
        "operator_id": operator,
        "ip_address": ip,
        "source_endpoint": endpoint,
        "payload_json": payload_json,
        "created_at": now,
        "updated_at": now,
    }
    with _get_conn(cfg) as conn:
        conn.execute(
            """
            INSERT INTO contract_audit_stage_log(
                id, event_key, audit_id, document_id, contract_name,
                occurred_at_utc, stage_code, stage_status, stage_result,
                stage_message, audit_opinion, operator_id, ip_address,
                source_endpoint, payload_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_key) DO UPDATE SET
                stage_status = excluded.stage_status,
                stage_result = excluded.stage_result,
                stage_message = excluded.stage_message,
                audit_opinion = CASE
                    WHEN excluded.audit_opinion <> '' THEN excluded.audit_opinion
                    ELSE contract_audit_stage_log.audit_opinion
                END,
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at
            """,
            (
                record["id"],
                record["event_key"],
                record["audit_id"],
                record["document_id"],
                record["contract_name"],
                record["occurred_at_utc"],
                record["stage_code"],
                record["stage_status"],
                record["stage_result"],
                record["stage_message"],
                record["audit_opinion"],
                record["operator_id"],
                record["ip_address"],
                record["source_endpoint"],
                record["payload_json"],
                record["created_at"],
                record["updated_at"],
            ),
        )
        conn.commit()
    return _serialize_record(record)


def _serialize_record(row: Any) -> Dict[str, Any]:
    data = dict(row)
    payload_text = str(data.pop("payload_json", data.pop("payload", "{}")) or "{}")
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        payload = {"raw": payload_text}
    data["payload"] = payload
    return data


def _normalize_stage_filters(stage_filters: Optional[Sequence[str]]) -> List[str]:
    if not stage_filters:
        return []
    normalized: List[str] = []
    for item in stage_filters:
        for token in str(item or "").split(","):
            stage = token.strip()
            if stage:
                normalized.append(stage)
    return normalized


def query_contract_audit_stage_logs(
    cfg: Dict[str, Any],
    *,
    operator_id: str,
    contract_name: str = "",
    started_at_utc: str = "",
    ended_at_utc: str = "",
    stage_filters: Optional[Sequence[str]] = None,
    audit_id: str = "",
    document_id: str = "",
    limit: int = 100,
    offset: int = 0,
) -> Dict[str, Any]:
    init_contract_audit_log_db(cfg)
    where = ["operator_id = ?"]
    params: List[Any] = [str(operator_id or "")]
    if contract_name:
        where.append("LOWER(contract_name) LIKE ?")
        params.append(f"%{str(contract_name).strip().lower()}%")
    if audit_id:
        where.append("audit_id = ?")
        params.append(str(audit_id))
    if document_id:
        where.append("document_id = ?")
        params.append(str(document_id))
    if started_at_utc:
        where.append("occurred_at_utc >= ?")
        params.append(_normalize_utc_iso_ms(started_at_utc))
    if ended_at_utc:
        where.append("occurred_at_utc <= ?")
        params.append(_normalize_utc_iso_ms(ended_at_utc))
    stages = _normalize_stage_filters(stage_filters)
    if stages:
        placeholders = ", ".join(["?"] * len(stages))
        where.append(f"stage_code IN ({placeholders})")
        params.extend(stages)
    where_sql = " AND ".join(where) if where else "1=1"
    safe_limit = max(1, min(int(limit or 100), 500))
    safe_offset = max(0, int(offset or 0))
    with _get_conn(cfg) as conn:
        total = conn.execute(
            f"SELECT COUNT(1) FROM contract_audit_stage_log WHERE {where_sql}",
            params,
        ).fetchone()[0]
        rows = conn.execute(
            f"""
            SELECT id, event_key, audit_id, document_id, contract_name, occurred_at_utc,
                   stage_code, stage_status, stage_result, stage_message, audit_opinion,
                   operator_id, ip_address, source_endpoint, payload_json, created_at, updated_at
            FROM contract_audit_stage_log
            WHERE {where_sql}
            ORDER BY occurred_at_utc DESC, created_at DESC
            LIMIT ? OFFSET ?
            """,
            [*params, safe_limit, safe_offset],
        ).fetchall()
    return {
        "items": [_serialize_record(row) for row in rows],
        "total": int(total or 0),
        "limit": safe_limit,
        "offset": safe_offset,
    }


def export_contract_audit_stage_logs(
    cfg: Dict[str, Any],
    *,
    records: Iterable[Dict[str, Any]],
    export_format: str = "jsonl",
) -> Dict[str, str]:
    fmt = str(export_format or "jsonl").strip().lower()
    if fmt not in {"jsonl", "csv"}:
        raise ValueError("unsupported export format")
    export_dir = _export_dir(cfg)
    os.makedirs(export_dir, exist_ok=True)
    stem = f"contract-audit-stage-log-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')[:-3]}Z"
    path = os.path.join(export_dir, f"{stem}.{fmt}")
    rows = list(records)
    if fmt == "jsonl":
        with open(path, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(_export_row(row), ensure_ascii=False))
                fh.write("\n")
    else:
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=_EXPORT_FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow(_export_row(row))
    return {"path": path, "format": fmt, "filename": os.path.basename(path)}


def _export_row(row: Dict[str, Any]) -> Dict[str, Any]:
    payload = row.get("payload")
    return {
        "id": row.get("id", ""),
        "event_key": row.get("event_key", ""),
        "audit_id": row.get("audit_id", ""),
        "document_id": row.get("document_id", ""),
        "contract_name": row.get("contract_name", ""),
        "occurred_at_utc": row.get("occurred_at_utc", ""),
        "stage_code": row.get("stage_code", ""),
        "stage_status": row.get("stage_status", ""),
        "stage_result": row.get("stage_result", ""),
        "stage_message": row.get("stage_message", ""),
        "audit_opinion": row.get("audit_opinion", ""),
        "operator_id": row.get("operator_id", ""),
        "ip_address": row.get("ip_address", ""),
        "source_endpoint": row.get("source_endpoint", ""),
        "payload": json.dumps(payload, ensure_ascii=False, sort_keys=True) if isinstance(payload, dict) else str(payload or ""),
    }
