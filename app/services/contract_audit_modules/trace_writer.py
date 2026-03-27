"""
Trace Writer.
职责: 负责记录合同审计过程中的详细调试轨迹 (Trace)，以 JSONL 格式落盘。
输入输出: 接收配置、事件名称和载荷字典，无返回值。
异常场景: 文件写入失败时记录错误日志，不阻断主流程。
"""
import os
import json
import structlog
from datetime import datetime
from typing import Dict, Any, Tuple

logger = structlog.get_logger(__name__)

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
            base = os.path.join(data_dir, "memory") if data_dir else os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../data/memory"))
        trace_dir = os.path.join(base, "debug", "contract_audit_trace")
    max_chars = int(cfg.get("contract_audit_trace_max_chars", 600) or 600)
    return {"enabled": enabled, "dir": os.path.abspath(trace_dir), "max_chars": max(120, max_chars)}

def write_audit_trace(cfg: Dict[str, Any], event: str, payload: Dict[str, Any], memory_dir: str = "") -> None:
    """写入单条审计追踪日志到独立文件。"""
    opts = audit_trace_options(cfg, memory_dir)
    if not opts["enabled"]:
        return
    day = datetime.utcnow().strftime("%Y-%m-%d")
    target_dir = os.path.join(opts["dir"], day)
    os.makedirs(target_dir, exist_ok=True)
    row = {"ts": datetime.utcnow().isoformat(), "event": str(event or ""), **(payload or {})}
    file_path = os.path.join(target_dir, "contract_audit_trace.jsonl")
    try:
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.error("write_audit_trace_failed", error=str(e), event=event)
