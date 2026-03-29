"""
Trace Writer.
职责: 负责记录合同审计过程中的详细调试轨迹 (Trace)，以 JSONL 格式落盘。
输入输出: 接收配置、事件名称和载荷字典，无返回值。
异常场景: 文件写入失败时记录错误日志，不阻断主流程。
"""
import os
import json
import shutil
import structlog
import time
from datetime import datetime
from typing import Dict, Any, Tuple

logger = structlog.get_logger(__name__)
_LAST_DEBUG_CLEANUP_AT: Dict[str, float] = {}


def memory_paths(cfg: Dict[str, Any]) -> Tuple[str, str]:
    """获取记忆存储目录和数据库路径。"""
    memory_dir = str(cfg.get("memory_dir") or "").strip()
    if not memory_dir:
        data_dir = str(cfg.get("data_dir") or "").strip()
        if data_dir:
            memory_dir = os.path.join(data_dir, "memory")
        else:
            memory_dir = os.path.abspath(os.path.join(
                os.path.dirname(__file__), "../../../memory"))
    memory_db = str(cfg.get("memory_db_path") or "").strip()
    if not memory_db:
        memory_db = os.path.join(memory_dir, "memory.db")
    os.makedirs(memory_dir, exist_ok=True)
    return os.path.abspath(memory_dir), os.path.abspath(memory_db)


def trace_clip(v: Any, max_chars: int = 600) -> str:
    """截断超长字符串，用于 trace 日志精简。"""
    s = str(v or "")
    if len(s) <= max_chars:
        return s
    return s[:max_chars] + f"...<truncated:{len(s)-max_chars}>"


def audit_trace_options(cfg: Dict[str, Any], memory_dir: str = "") -> Dict[str, Any]:
    """获取 trace 配置选项。"""
    enabled = bool(cfg.get("contract_audit_trace_enabled", True))
    trace_dir = str(cfg.get("contract_audit_trace_dir") or "").strip()
    if not trace_dir:
        base = memory_dir
        if not base:
            data_dir = str(cfg.get("data_dir") or "").strip()
            base = os.path.join(data_dir, "memory") if data_dir else os.path.abspath(
                os.path.join(os.path.dirname(__file__), "../../../data/memory"))
        trace_dir = os.path.join(base, "debug", "contract_audit_trace")
    max_chars = int(cfg.get("contract_audit_trace_max_chars", 600) or 600)
    return {"enabled": enabled, "dir": os.path.abspath(trace_dir), "max_chars": max(120, max_chars)}


def round_trace_options(cfg: Dict[str, Any], memory_dir: str = "") -> Dict[str, Any]:
    enabled = bool(cfg.get("contract_audit_round_trace_enabled", True))
    trace_dir = str(cfg.get("contract_audit_round_trace_dir") or "").strip()
    if not trace_dir:
        base = memory_dir
        if not base:
            data_dir = str(cfg.get("data_dir") or "").strip()
            base = os.path.join(data_dir, "memory") if data_dir else os.path.abspath(
                os.path.join(os.path.dirname(__file__), "../../../data/memory"))
        trace_dir = os.path.join(base, "debug", "contract_audit_rounds")
    max_chars = int(
        cfg.get("contract_audit_round_trace_max_chars", 4000) or 4000)
    return {"enabled": enabled, "dir": os.path.abspath(trace_dir), "max_chars": max(400, max_chars)}


def _clip_payload(value: Any, max_chars: int) -> Any:
    if isinstance(value, str):
        return trace_clip(value, max_chars)
    if isinstance(value, dict):
        return {k: _clip_payload(v, max_chars) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip_payload(v, max_chars) for v in value]
    return value


def _audit_file_tag(v: Any) -> str:
    s = str(v or "").strip()
    if not s:
        return ""
    out = []
    for ch in s:
        if ch.isalnum() or ch in ("-", "_"):
            out.append(ch)
        else:
            out.append("_")
    return "".join(out).strip("_")


def _cleanup_debug_tree(cfg: Dict[str, Any], base_dir: str) -> None:
    root = os.path.abspath(str(base_dir or "").strip())
    if not root:
        return
    interval = max(
        60, int(cfg.get("contract_audit_debug_cleanup_interval_sec") or 1800))
    now_ts = time.time()
    last_ts = float(_LAST_DEBUG_CLEANUP_AT.get(root) or 0.0)
    if now_ts - last_ts < interval:
        return
    _LAST_DEBUG_CLEANUP_AT[root] = now_ts
    retention_days = max(
        1, int(cfg.get("contract_audit_debug_retention_days") or 7))
    archive_enabled = bool(
        cfg.get("contract_audit_debug_archive_before_delete", False))
    archive_dir = str(
        cfg.get("contract_audit_debug_archive_dir") or "").strip()
    archive_root = archive_dir if archive_dir else os.path.join(
        root, "_archive")
    if archive_enabled:
        os.makedirs(archive_root, exist_ok=True)
    now = datetime.utcnow()
    cutoff = now.toordinal() - retention_days
    if not os.path.isdir(root):
        return
    try:
        names = os.listdir(root)
    except Exception:
        return
    for name in names:
        path = os.path.join(root, name)
        if not os.path.isdir(path):
            continue
        try:
            day = datetime.strptime(name, "%Y-%m-%d")
        except Exception:
            continue
        if day.toordinal() <= cutoff:
            if archive_enabled:
                archive_base = os.path.join(
                    archive_root, f"{os.path.basename(root)}_{name}")
                try:
                    shutil.make_archive(
                        archive_base, "zip", root_dir=root, base_dir=name)
                except Exception:
                    pass
            shutil.rmtree(path, ignore_errors=True)


def write_round_trace(cfg: Dict[str, Any], round_no: int, action: str, payload: Dict[str, Any], memory_dir: str = "") -> None:
    opts = round_trace_options(cfg, memory_dir)
    if not opts["enabled"]:
        return
    _cleanup_debug_tree(cfg, opts["dir"])
    rn = int(round_no or 0)
    if rn <= 0:
        return
    day = datetime.utcnow().strftime("%Y-%m-%d")
    target_dir = os.path.join(opts["dir"], day)
    os.makedirs(target_dir, exist_ok=True)
    row = _clip_payload({"ts": datetime.utcnow().isoformat(), "round": rn, "action": str(
        action or ""), **(payload or {})}, opts["max_chars"])
    audit_tag = _audit_file_tag(row.get("audit_id"))
    if audit_tag:
        file_path = os.path.join(
            target_dir, f"round_{rn:03d}_{audit_tag}.jsonl")
    else:
        file_path = os.path.join(target_dir, f"round_{rn:03d}.jsonl")
    try:
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.error("write_round_trace_failed",
                     error=str(e), round=rn, action=action)


def write_audit_trace(cfg: Dict[str, Any], event: str, payload: Dict[str, Any], memory_dir: str = "") -> None:
    """写入单条审计追踪日志到独立文件。"""
    opts = audit_trace_options(cfg, memory_dir)
    if not opts["enabled"]:
        return
    _cleanup_debug_tree(cfg, opts["dir"])
    day = datetime.utcnow().strftime("%Y-%m-%d")
    target_dir = os.path.join(opts["dir"], day)
    os.makedirs(target_dir, exist_ok=True)
    row = {"ts": datetime.utcnow().isoformat(), "event": str(
        event or ""), **(payload or {})}
    file_path = os.path.join(target_dir, "contract_audit_trace.jsonl")
    audit_tag = _audit_file_tag(row.get("audit_id"))
    try:
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        if audit_tag:
            audit_file_path = os.path.join(target_dir, f"{audit_tag}.jsonl")
            with open(audit_file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.error("write_audit_trace_failed", error=str(e), event=event)
