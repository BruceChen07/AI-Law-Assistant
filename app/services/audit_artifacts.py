"""Audit artifact archival.

Every finished audit is mirrored to a plain-file directory so results stay
readable even if the web UI or the database is unavailable:

    <data_dir>/audits/<audit_id>/report.json   full result (audit or error)
    <data_dir>/audits/<audit_id>/meta.json     run meta (model, coverage, timings)
    <data_dir>/audits/<audit_id>/chunks.jsonl  per-chunk audit manifest
    <data_dir>/audits/<audit_id>/report.md     human readable report
    <data_dir>/audits/index.jsonl              append-only audit index

All functions are best effort: archival failures are logged, never raised.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import structlog

from app.core.database import get_conn
from app.services.crud import (
    get_contract_audit_by_id,
    get_contract_audit_progress,
    list_contract_audit_chunks,
)

logger = structlog.get_logger(__name__)

ARTIFACT_NAMES = ("report.json", "meta.json", "chunks.jsonl", "report.md")
_LEVEL_ORDER = ("high", "medium", "low")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def artifact_root(cfg: Dict[str, Any]) -> str:
    configured = str(cfg.get("audit_artifact_dir") or "").strip()
    if configured:
        return os.path.abspath(configured)
    data_dir = str(cfg.get("data_dir") or "./data")
    return os.path.abspath(os.path.join(data_dir, "audits"))


def audit_dir(cfg: Dict[str, Any], audit_id: str) -> str:
    safe_id = "".join(
        ch for ch in str(audit_id or "") if ch.isalnum() or ch in "-_") or "unknown"
    return os.path.join(artifact_root(cfg), safe_id)


def _write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def _document_filename(cfg: Dict[str, Any], document_id: str) -> str:
    if not document_id:
        return ""
    try:
        conn = get_conn(cfg)
        row = conn.execute(
            "SELECT original_filename FROM documents WHERE id=?",
            (str(document_id),),
        ).fetchone()
        conn.close()
        return str(row["original_filename"] or "") if row else ""
    except Exception:
        return ""


def _risk_counts(audit: Any) -> Dict[str, int]:
    counts = {"high": 0, "medium": 0, "low": 0}
    if not isinstance(audit, dict):
        return counts
    summary = audit.get("risk_summary")
    if isinstance(summary, dict):
        for level in _LEVEL_ORDER:
            try:
                counts[level] = int(summary.get(level) or 0)
            except Exception:
                counts[level] = 0
        return counts
    for risk in list(audit.get("risks") or []):
        if not isinstance(risk, dict):
            continue
        level = str(risk.get("level") or "low").lower()
        if level in counts:
            counts[level] += 1
    return counts


def _render_markdown(
    *,
    audit_id: str,
    document_id: str,
    filename: str,
    status: str,
    created_at: str,
    audit: Any,
    meta: Any,
    error: Any,
) -> str:
    lines: List[str] = []
    title = filename or document_id or audit_id
    lines.append(f"# 合同审计报告：{title}")
    lines.append("")
    lines.append("| 项 | 值 |")
    lines.append("| --- | --- |")
    lines.append(f"| 审计 ID | `{audit_id}` |")
    lines.append(f"| 文档 ID | `{document_id}` |")
    lines.append(f"| 状态 | {status} |")
    lines.append(f"| 生成时间 | {created_at} |")
    if isinstance(meta, dict):
        for key in (
            "execution_path",
            "plan_version",
            "chunks_total",
            "chunks_completed",
            "coverage_audited",
            "chunk_reused_count",
            "review_retry_applied_count",
            "text_length",
            "ocr_used",
            "page_count",
        ):
            if key in meta and meta.get(key) not in (None, ""):
                lines.append(f"| {key} | {meta.get(key)} |")
    lines.append("")

    if isinstance(error, dict) and error:
        lines.append("## 失败信息")
        lines.append("")
        lines.append(f"- error: {error.get('error', '')}")
        lines.append(f"- detail: {error.get('detail', '')}")
        lines.append("")
        return "\n".join(lines)

    counts = _risk_counts(audit)
    lines.append("## 风险概览")
    lines.append("")
    lines.append(f"- 高风险：{counts['high']}")
    lines.append(f"- 中风险：{counts['medium']}")
    lines.append(f"- 低风险：{counts['low']}")
    summary = str((audit or {}).get("summary") or "").strip() if isinstance(audit, dict) else ""
    if summary:
        lines.append("")
        lines.append(summary)
    lines.append("")

    risks = list((audit or {}).get("risks") or []) if isinstance(audit, dict) else []
    lines.append(f"## 风险明细（{len(risks)}）")
    lines.append("")
    for index, risk in enumerate(risks, start=1):
        if not isinstance(risk, dict):
            continue
        location = risk.get("location") if isinstance(
            risk.get("location"), dict) else {}
        lines.append(
            f"### {index}. [{str(risk.get('level') or '').upper()}] {risk.get('issue') or ''}"
        )
        lines.append("")
        if risk.get("suggestion"):
            lines.append(f"- 建议：{risk.get('suggestion')}")
        basis = risk.get("basis") or risk.get("law_reference") or ""
        if basis:
            lines.append(f"- 依据：{basis}")
        if risk.get("citation_id"):
            lines.append(f"- 引用 ID：{risk.get('citation_id')}")
        if location.get("clause_id"):
            lines.append(f"- 条款：{location.get('clause_path') or location.get('clause_id')}")
        if risk.get("evidence"):
            lines.append(f"- 证据：{risk.get('evidence')}")
        lines.append("")
    return "\n".join(lines)


def _chunk_rows(chunks: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for chunk in list(chunks or []):
        if not isinstance(chunk, dict):
            continue
        def _load(value: Any) -> Any:
            if isinstance(value, str) and value.strip():
                try:
                    return json.loads(value)
                except Exception:
                    return value
            return value if value is not None else []

        rows.append(
            {
                "chunk_id": chunk.get("chunk_id"),
                "ordinal": chunk.get("ordinal"),
                "status": chunk.get("status"),
                "attempts": chunk.get("attempts"),
                "reviewed": chunk.get("reviewed"),
                "clause_ids": _load(chunk.get("clause_ids")),
                "source_spans": _load(chunk.get("source_spans")),
                "input_tokens": chunk.get("input_tokens"),
                "output_tokens": chunk.get("output_tokens"),
                "prompt_hash": chunk.get("prompt_hash"),
                "updated_at": chunk.get("updated_at"),
            }
        )
    return rows


def write_audit_artifacts(
    cfg: Dict[str, Any],
    *,
    audit_id: str,
    document_id: str = "",
    filename: str = "",
    status: str = "done",
    audit: Any = None,
    meta: Any = None,
    error: Any = None,
    chunks: Optional[List[Dict[str, Any]]] = None,
    created_at: str = "",
) -> Dict[str, Any]:
    created_at = created_at or _now()
    try:
        target_dir = audit_dir(cfg, audit_id)
        os.makedirs(target_dir, exist_ok=True)
        filename = filename or _document_filename(cfg, document_id)
        report = {
            "audit_id": audit_id,
            "document_id": document_id,
            "filename": filename,
            "status": status,
            "created_at": created_at,
            "audit": audit if status == "done" else None,
            "error": error if status != "done" else None,
        }
        _write_text(
            os.path.join(target_dir, "report.json"),
            json.dumps(report, ensure_ascii=False, indent=2),
        )
        _write_text(
            os.path.join(target_dir, "meta.json"),
            json.dumps(
                {
                    "audit_id": audit_id,
                    "document_id": document_id,
                    "filename": filename,
                    "status": status,
                    "created_at": created_at,
                    "meta": meta or {},
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        chunk_lines = [
            json.dumps(row, ensure_ascii=False) for row in _chunk_rows(chunks)
        ]
        _write_text(
            os.path.join(target_dir, "chunks.jsonl"),
            "\n".join(chunk_lines) + ("\n" if chunk_lines else ""),
        )
        _write_text(
            os.path.join(target_dir, "report.md"),
            _render_markdown(
                audit_id=audit_id,
                document_id=document_id,
                filename=filename,
                status=status,
                created_at=created_at,
                audit=audit,
                meta=meta,
                error=error,
            ),
        )
        counts = _risk_counts(audit)
        index_row = {
            "audit_id": audit_id,
            "document_id": document_id,
            "filename": filename,
            "status": status,
            "created_at": created_at,
            "risk_summary": counts,
            "risk_count": sum(counts.values()),
            "dir": target_dir,
        }
        index_path = os.path.join(artifact_root(cfg), "index.jsonl")
        os.makedirs(os.path.dirname(index_path), exist_ok=True)
        with open(index_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(index_row, ensure_ascii=False) + "\n")
        return {"dir": target_dir, "files": list(ARTIFACT_NAMES), "index": index_path}
    except Exception as exc:  # pragma: no cover - archival is best effort
        logger.warning("audit_artifact_write_failed",
                       audit_id=audit_id, error=str(exc))
        return {}


def rebuild_artifacts_from_db(cfg: Dict[str, Any], audit_id: str) -> Dict[str, Any]:
    """Re-create artifact files for an audit that only exists in SQLite."""
    record = get_contract_audit_by_id(cfg, audit_id)
    progress = get_contract_audit_progress(cfg, audit_id)
    if not record and not progress:
        return {}
    status = str((record or {}).get("status") or (progress or {}).get("status") or "")
    document_id = str(
        (record or {}).get("document_id") or (progress or {}).get("document_id") or "")
    detail: Dict[str, Any] = {}
    raw_detail = (progress or {}).get("detail_json") or ""
    if raw_detail:
        try:
            parsed = json.loads(raw_detail)
            if isinstance(parsed, dict):
                detail = parsed
        except Exception:
            detail = {}
    audit: Any = None
    error: Any = None
    if status == "done" and (record or {}).get("result_json"):
        try:
            audit = json.loads(record["result_json"])
        except Exception:
            audit = None
    elif (record or {}).get("result_json"):
        try:
            error = json.loads(record["result_json"])
        except Exception:
            error = {"error": str(record.get("result_json") or "")}
    meta = detail.get("result_meta") if isinstance(
        detail.get("result_meta"), dict) else detail
    return write_audit_artifacts(
        cfg,
        audit_id=audit_id,
        document_id=document_id,
        status=status or "unknown",
        audit=audit,
        meta=meta,
        error=error,
        chunks=list_contract_audit_chunks(cfg, audit_id),
        created_at=str((record or {}).get("created_at") or (progress or {}).get("created_at") or ""),
    )


def list_artifacts(cfg: Dict[str, Any], audit_id: str) -> List[Dict[str, Any]]:
    target_dir = audit_dir(cfg, audit_id)
    if not os.path.isdir(target_dir):
        return []
    items: List[Dict[str, Any]] = []
    for name in ARTIFACT_NAMES:
        path = os.path.join(target_dir, name)
        if not os.path.isfile(path):
            continue
        stat = os.stat(path)
        items.append(
            {
                "name": name,
                "size": int(stat.st_size),
                "modified_at": datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc).isoformat(),
            }
        )
    return items


def resolve_artifact_path(cfg: Dict[str, Any], audit_id: str, name: str) -> Optional[str]:
    if name not in ARTIFACT_NAMES:
        return None
    path = os.path.join(audit_dir(cfg, audit_id), name)
    return path if os.path.isfile(path) else None
