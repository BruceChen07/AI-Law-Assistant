"""
LLM 全链路追踪模块 (LLM Trace Collector).
职责: 记录每一次 LLM 请求的全生命周期日志，包括请求接收、推理过程、响应生成、
      重试链路、Token 消耗等完整信息。

存储策略:
  - SQLite: 结构化索引字段，用于快速检索、筛选、聚合统计
  - JSONL: 完整消息体和响应内容归档，避免 SQLite 膨胀

数据模型:
  - Trace: 一次完整的审计任务 = 1 个 trace_id
  - Span: trace 下的单次 LLM 调用，包含请求/响应/重试子 span
"""

import json
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)

# ---- 线程安全锁 ----
_DB_LOCK = threading.Lock()


# ==================== 数据模型 ====================

class LlmSpan:
    """单次 LLM 调用的全量信息容器。"""

    __slots__ = (
        "trace_id", "span_id", "parent_span_id", "audit_id",
        "stage", "task_profile", "model_role",
        "provider", "api_base", "model_name",
        "request_received_at", "request_msgs_hash", "request_input_tokens_est",
        "request_temperature", "request_max_tokens", "request_timeout",
        "thinking_started_at", "thinking_content", "thinking_duration_ms",
        "response_generated_at", "response_content", "response_content_length",
        "prompt_tokens", "completion_tokens", "total_tokens",
        "total_latency_ms", "retry_index", "is_retry", "retry_strategy",
        "status", "error_type", "error_message",
        "ollama_load_duration_ms", "ollama_prompt_eval_ms",
        "ollama_eval_duration_ms", "ollama_total_duration_ms",
        "span_metadata",
    )

    def __init__(self) -> None:
        self.trace_id: str = ""
        self.span_id: str = ""
        self.parent_span_id: str = ""
        self.audit_id: str = ""
        self.stage: str = "llm_call"
        self.task_profile: str = "default"
        self.model_role: str = "main"
        self.provider: str = "ollama"
        self.api_base: str = ""
        self.model_name: str = ""
        self.request_received_at: str = ""
        self.request_msgs_hash: str = ""
        self.request_input_tokens_est: int = 0
        self.request_temperature: float = 0.0
        self.request_max_tokens: int = 0
        self.request_timeout: int = 0
        self.thinking_started_at: str = ""
        self.thinking_content: str = ""
        self.thinking_duration_ms: int = 0
        self.response_generated_at: str = ""
        self.response_content: str = ""
        self.response_content_length: int = 0
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0
        self.total_tokens: int = 0
        self.total_latency_ms: int = 0
        self.retry_index: int = 0
        self.is_retry: bool = False
        self.retry_strategy: str = ""
        self.status: str = "pending"  # pending | thinking | success | failed
        self.error_type: str = ""
        self.error_message: str = ""
        self.ollama_load_duration_ms: int = 0
        self.ollama_prompt_eval_ms: int = 0
        self.ollama_eval_duration_ms: int = 0
        self.ollama_total_duration_ms: int = 0
        self.span_metadata: str = ""

    def to_sqlite_row(self) -> Dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "audit_id": self.audit_id,
            "stage": self.stage,
            "task_profile": self.task_profile,
            "model_role": self.model_role,
            "provider": self.provider,
            "api_base": self.api_base,
            "model_name": self.model_name,
            "request_received_at": self.request_received_at,
            "request_msgs_hash": self.request_msgs_hash,
            "request_input_tokens_est": self.request_input_tokens_est,
            "request_temperature": self.request_temperature,
            "request_max_tokens": self.request_max_tokens,
            "request_timeout": self.request_timeout,
            "thinking_started_at": self.thinking_started_at or "",
            "thinking_content": "",  # 完整思考内容仅存 JSONL
            "thinking_duration_ms": self.thinking_duration_ms,
            "response_generated_at": self.response_generated_at or "",
            "response_content": "",  # 完整响应仅存 JSONL
            "response_content_length": self.response_content_length,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "total_latency_ms": self.total_latency_ms,
            "retry_index": self.retry_index,
            "is_retry": 1 if self.is_retry else 0,
            "retry_strategy": self.retry_strategy,
            "status": self.status,
            "error_type": self.error_type,
            "error_message": self.error_message[:2000] if self.error_message else "",
            "ollama_load_duration_ms": self.ollama_load_duration_ms,
            "ollama_prompt_eval_ms": self.ollama_prompt_eval_ms,
            "ollama_eval_duration_ms": self.ollama_eval_duration_ms,
            "ollama_total_duration_ms": self.ollama_total_duration_ms,
            "span_metadata": self.span_metadata[:4000] if self.span_metadata else "",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    def to_jsonl_dict(self) -> Dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "audit_id": self.audit_id,
            "stage": self.stage,
            "task_profile": self.task_profile,
            "model_role": self.model_role,
            "provider": self.provider,
            "api_base": self.api_base,
            "model_name": self.model_name,
            "request_received_at": self.request_received_at,
            "request_temperature": self.request_temperature,
            "request_max_tokens": self.request_max_tokens,
            "request_timeout": self.request_timeout,
            "request_input_tokens_est": self.request_input_tokens_est,
            "thinking_started_at": self.thinking_started_at or "",
            "thinking_content": self.thinking_content,
            "thinking_duration_ms": self.thinking_duration_ms,
            "response_generated_at": self.response_generated_at or "",
            "response_content": self.response_content,
            "response_content_length": self.response_content_length,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "total_latency_ms": self.total_latency_ms,
            "retry_index": self.retry_index,
            "is_retry": self.is_retry,
            "retry_strategy": self.retry_strategy,
            "status": self.status,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "ollama_load_duration_ms": self.ollama_load_duration_ms,
            "ollama_prompt_eval_ms": self.ollama_prompt_eval_ms,
            "ollama_eval_duration_ms": self.ollama_eval_duration_ms,
            "ollama_total_duration_ms": self.ollama_total_duration_ms,
            "span_metadata": self.span_metadata,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }


# ==================== Trace 收集器 ====================

class LlmTraceCollector:
    """
    全链路 LLM 调用追踪器（单例）。

    使用方式:
        collector = LlmTraceCollector(cfg)
        span = collector.start_span(audit_id="audit_xxx", stage="clause_audit")
        collector.record_request(span, messages=[...], ...)
        # ... LLM 调用 ...
        collector.record_response(span, content="...", usage={...})
        collector.finish_span(span, status="success")
    """

    _instances: Dict[str, "LlmTraceCollector"] = {}

    def __new__(cls, cfg: Dict[str, Any], trace_key: str = "default") -> "LlmTraceCollector":
        cache_key = f"{id(cfg)}_{trace_key}"
        if cache_key not in cls._instances:
            instance = super().__new__(cls)
            instance._initialized = False  # type: ignore
            cls._instances[cache_key] = instance
        return cls._instances[cache_key]

    def __init__(self, cfg: Dict[str, Any], trace_key: str = "default") -> None:
        if self._initialized:  # type: ignore
            return
        self._initialized = True  # type: ignore
        self._cfg = cfg
        self._trace_key = trace_key
        self._enabled = bool(cfg.get("llm_trace_full_enabled", True))
        self._db_path = self._resolve_db_path()
        self._jsonl_dir = self._resolve_jsonl_dir()
        self._max_jsonl_chars = int(
            cfg.get("llm_trace_full_max_chars", 16000) or 16000)
        self._ensure_storage()
        logger.info(
            "llm_trace_collector_ready",
            enabled=self._enabled,
            db_path=self._db_path,
            jsonl_dir=self._jsonl_dir,
            max_chars=self._max_jsonl_chars,
        )

    def _resolve_db_path(self) -> str:
        db_path = str(self._cfg.get("llm_trace_full_db_path") or "").strip()
        if db_path:
            return os.path.abspath(db_path)
        data_dir = str(self._cfg.get("data_dir") or "").strip() or os.path.join(
            os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "..", "data"
        )
        return os.path.abspath(os.path.join(data_dir, "llm_trace.db"))

    def _resolve_jsonl_dir(self) -> str:
        d = str(self._cfg.get("llm_trace_full_dir") or "").strip()
        if d:
            return os.path.abspath(d)
        data_dir = str(self._cfg.get("data_dir") or "").strip() or os.path.join(
            os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "..", "data"
        )
        return os.path.abspath(os.path.join(data_dir, "llm_traces_full"))

    def _ensure_storage(self) -> None:
        with _DB_LOCK:
            os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
            conn = sqlite3.connect(self._db_path, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS llm_trace_spans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id TEXT NOT NULL,
                    span_id TEXT NOT NULL UNIQUE,
                    parent_span_id TEXT,
                    audit_id TEXT,
                    stage TEXT NOT NULL DEFAULT 'llm_call',
                    task_profile TEXT DEFAULT 'default',
                    model_role TEXT DEFAULT 'main',
                    provider TEXT,
                    api_base TEXT,
                    model_name TEXT NOT NULL,
                    request_received_at TEXT,
                    request_msgs_hash TEXT,
                    request_input_tokens_est INTEGER DEFAULT 0,
                    request_temperature REAL DEFAULT 0,
                    request_max_tokens INTEGER DEFAULT 0,
                    request_timeout INTEGER DEFAULT 0,
                    thinking_started_at TEXT,
                    thinking_content TEXT DEFAULT '',
                    thinking_duration_ms INTEGER DEFAULT 0,
                    response_generated_at TEXT,
                    response_content TEXT DEFAULT '',
                    response_content_length INTEGER DEFAULT 0,
                    prompt_tokens INTEGER DEFAULT 0,
                    completion_tokens INTEGER DEFAULT 0,
                    total_tokens INTEGER DEFAULT 0,
                    total_latency_ms INTEGER DEFAULT 0,
                    retry_index INTEGER DEFAULT 0,
                    is_retry INTEGER DEFAULT 0,
                    retry_strategy TEXT DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    error_type TEXT DEFAULT '',
                    error_message TEXT DEFAULT '',
                    ollama_load_duration_ms INTEGER DEFAULT 0,
                    ollama_prompt_eval_ms INTEGER DEFAULT 0,
                    ollama_eval_duration_ms INTEGER DEFAULT 0,
                    ollama_total_duration_ms INTEGER DEFAULT 0,
                    span_metadata TEXT DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_llm_trace_trace_id ON llm_trace_spans(trace_id)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_llm_trace_model ON llm_trace_spans(model_name)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_llm_trace_created ON llm_trace_spans(created_at)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_llm_trace_status ON llm_trace_spans(status)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_llm_trace_audit ON llm_trace_spans(audit_id)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_llm_trace_stage ON llm_trace_spans(stage)")
            conn.commit()
            conn.close()

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ---- Span 生命周期 ----

    def new_span(
        self,
        trace_id: str = "",
        audit_id: str = "",
        stage: str = "llm_call",
        parent_span_id: str = "",
        task_profile: str = "default",
        model_role: str = "main",
        span_metadata: Optional[Dict[str, Any]] = None,
    ) -> LlmSpan:
        span = LlmSpan()
        span.trace_id = trace_id or f"trace_{uuid.uuid4().hex[:16]}"
        span.span_id = f"span_{uuid.uuid4().hex[:12]}"
        span.parent_span_id = parent_span_id
        span.audit_id = audit_id
        span.stage = stage
        span.task_profile = task_profile
        span.model_role = model_role
        span.status = "pending"
        span.span_metadata = json.dumps(
            span_metadata, ensure_ascii=False) if span_metadata else ""
        return span

    def record_request(
        self,
        span: LlmSpan,
        *,
        provider: str = "ollama",
        api_base: str = "",
        model_name: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        timeout: int = 60,
        input_tokens_est: int = 0,
        retry_index: int = 0,
        is_retry: bool = False,
        retry_strategy: str = "",
    ) -> None:
        if not self._enabled:
            return
        span.provider = provider
        span.api_base = api_base
        span.model_name = model_name
        span.request_received_at = datetime.now(timezone.utc).isoformat()
        span.request_msgs_hash = _hash_messages(messages)
        span.request_input_tokens_est = input_tokens_est
        span.request_temperature = temperature
        span.request_max_tokens = max_tokens
        span.request_timeout = timeout
        span.retry_index = retry_index
        span.is_retry = is_retry
        span.retry_strategy = retry_strategy
        span.status = "pending"
        # 首次请求时写入 SQLite 占位行
        self._upsert_span(span)
        # 写入 JSONL 归档（保存消息体）
        self._write_jsonl(span, "request", {
            "messages": _sanitize_for_jsonl(messages, self._max_jsonl_chars),
            "request_received_at": span.request_received_at,
        })

    def record_thinking(
        self,
        span: LlmSpan,
        *,
        thinking_content: str = "",
    ) -> None:
        if not self._enabled:
            return
        span.thinking_started_at = datetime.now(timezone.utc).isoformat()
        span.thinking_content = _clip(thinking_content, self._max_jsonl_chars)
        span.status = "thinking"
        self._write_jsonl(span, "thinking", {
            "thinking_started_at": span.thinking_started_at,
            "thinking_content": span.thinking_content,
        })

    def record_response(
        self,
        span: LlmSpan,
        *,
        content: str = "",
        usage: Optional[Dict[str, Any]] = None,
        ollama_raw: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self._enabled:
            return
        now = datetime.now(timezone.utc).isoformat()
        span.response_generated_at = now
        span.response_content = _clip(content, self._max_jsonl_chars)
        span.response_content_length = len(content)
        usage = usage or {}
        span.prompt_tokens = int(usage.get("prompt_tokens") or 0)
        span.completion_tokens = int(usage.get("completion_tokens") or 0)
        span.total_tokens = int(usage.get("total_tokens") or 0)
        if span.total_tokens == 0:
            span.total_tokens = span.prompt_tokens + span.completion_tokens
        # 提取 Ollama 性能指标
        if ollama_raw:
            span.ollama_load_duration_ms = _ms_or_0(
                ollama_raw.get("load_duration"))
            span.ollama_prompt_eval_ms = _ms_or_0(
                ollama_raw.get("prompt_eval_duration"))
            span.ollama_eval_duration_ms = _ms_or_0(
                ollama_raw.get("eval_duration"))
            span.ollama_total_duration_ms = _ms_or_0(
                ollama_raw.get("total_duration"))
        # 计算思考耗时
        if span.thinking_started_at and span.response_generated_at:
            try:
                t_start = datetime.fromisoformat(span.thinking_started_at)
                t_end = datetime.fromisoformat(span.response_generated_at)
                span.thinking_duration_ms = int(
                    (t_end - t_start).total_seconds() * 1000)
            except Exception:
                span.thinking_duration_ms = 0
        self._upsert_span(span)
        self._write_jsonl(span, "response", {
            "response_generated_at": now,
            "response_content": span.response_content,
            "response_content_length": span.response_content_length,
            "usage": usage,
        })

    def record_error(
        self,
        span: LlmSpan,
        *,
        error_type: str = "",
        error_message: str = "",
    ) -> None:
        if not self._enabled:
            return
        span.status = "failed"
        span.error_type = error_type
        span.error_message = _clip(error_message, 4000)
        self._upsert_span(span)
        self._write_jsonl(span, "error", {
            "error_type": error_type,
            "error_message": span.error_message,
        })

    def finish_span(
        self,
        span: LlmSpan,
        *,
        status: str = "success",
        duration_ms: int = 0,
    ) -> None:
        if not self._enabled:
            return
        span.status = status
        span.total_latency_ms = duration_ms
        self._upsert_span(span)

    # ---- 存储方法 ----

    def _upsert_span(self, span: LlmSpan) -> None:
        row = span.to_sqlite_row()
        try:
            with _DB_LOCK:
                conn = sqlite3.connect(self._db_path, timeout=10)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute(
                    """
                    INSERT INTO llm_trace_spans (
                        trace_id, span_id, parent_span_id, audit_id, stage,
                        task_profile, model_role, provider, api_base, model_name,
                        request_received_at, request_msgs_hash, request_input_tokens_est,
                        request_temperature, request_max_tokens, request_timeout,
                        thinking_started_at, thinking_duration_ms,
                        response_generated_at, response_content_length,
                        prompt_tokens, completion_tokens, total_tokens, total_latency_ms,
                        retry_index, is_retry, retry_strategy, status,
                        error_type, error_message,
                        ollama_load_duration_ms, ollama_prompt_eval_ms,
                        ollama_eval_duration_ms, ollama_total_duration_ms,
                        span_metadata, created_at
                    ) VALUES (
                        :trace_id, :span_id, :parent_span_id, :audit_id, :stage,
                        :task_profile, :model_role, :provider, :api_base, :model_name,
                        :request_received_at, :request_msgs_hash, :request_input_tokens_est,
                        :request_temperature, :request_max_tokens, :request_timeout,
                        :thinking_started_at, :thinking_duration_ms,
                        :response_generated_at, :response_content_length,
                        :prompt_tokens, :completion_tokens, :total_tokens, :total_latency_ms,
                        :retry_index, :is_retry, :retry_strategy, :status,
                        :error_type, :error_message,
                        :ollama_load_duration_ms, :ollama_prompt_eval_ms,
                        :ollama_eval_duration_ms, :ollama_total_duration_ms,
                        :span_metadata,
                        COALESCE(:created_at, (SELECT created_at FROM llm_trace_spans WHERE span_id = :span_id2))
                    )
                    ON CONFLICT(span_id) DO UPDATE SET
                        status = excluded.status,
                        request_received_at = COALESCE(excluded.request_received_at, llm_trace_spans.request_received_at),
                        thinking_started_at = COALESCE(excluded.thinking_started_at, llm_trace_spans.thinking_started_at),
                        thinking_duration_ms = COALESCE(excluded.thinking_duration_ms, llm_trace_spans.thinking_duration_ms),
                        response_generated_at = COALESCE(excluded.response_generated_at, llm_trace_spans.response_generated_at),
                        response_content_length = COALESCE(excluded.response_content_length, llm_trace_spans.response_content_length),
                        prompt_tokens = COALESCE(excluded.prompt_tokens, llm_trace_spans.prompt_tokens),
                        completion_tokens = COALESCE(excluded.completion_tokens, llm_trace_spans.completion_tokens),
                        total_tokens = COALESCE(excluded.total_tokens, llm_trace_spans.total_tokens),
                        total_latency_ms = COALESCE(excluded.total_latency_ms, llm_trace_spans.total_latency_ms),
                        retry_index = excluded.retry_index,
                        error_type = COALESCE(excluded.error_type, llm_trace_spans.error_type),
                        error_message = COALESCE(excluded.error_message, llm_trace_spans.error_message),
                        ollama_load_duration_ms = COALESCE(excluded.ollama_load_duration_ms, llm_trace_spans.ollama_load_duration_ms),
                        ollama_prompt_eval_ms = COALESCE(excluded.ollama_prompt_eval_ms, llm_trace_spans.ollama_prompt_eval_ms),
                        ollama_eval_duration_ms = COALESCE(excluded.ollama_eval_duration_ms, llm_trace_spans.ollama_eval_duration_ms),
                        ollama_total_duration_ms = COALESCE(excluded.ollama_total_duration_ms, llm_trace_spans.ollama_total_duration_ms)
                    """,
                    {**row, "span_id2": span.span_id},
                )
                conn.commit()
                conn.close()
        except Exception as e:
            logger.warning("llm_trace_upsert_failed",
                           span_id=span.span_id, error=str(e))

    def _write_jsonl(self, span: LlmSpan, event: str, payload: Dict[str, Any]) -> None:
        try:
            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            target_dir = os.path.join(self._jsonl_dir, day)
            os.makedirs(target_dir, exist_ok=True)

            # 公共名文件（所有事件合并）
            common_path = os.path.join(target_dir, "llm_trace_full.jsonl")
            full = span.to_jsonl_dict()
            full["event"] = event
            full.update(payload)
            with open(common_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(full, ensure_ascii=False) + "\n")

            # 按 trace_id 分文件（方便按审计会话回溯）
            if span.trace_id:
                tagged = _sanitize_filename(span.trace_id)
                trace_file = os.path.join(
                    target_dir, f"trace_{tagged}.jsonl")
                with open(trace_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(full, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning("llm_trace_jsonl_failed",
                           span_id=span.span_id, error=str(e))

    # ---- 查询接口 ----

    def query_traces(
        self,
        *,
        model_name: str = "",
        status: str = "",
        audit_id: str = "",
        date_from: str = "",
        date_to: str = "",
        stage: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """分页查询 trace 列表（从 SQLite）。"""
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conditions: List[str] = ["1=1"]
        params: List[Any] = []
        if model_name:
            conditions.append("model_name LIKE ?")
            params.append(f"%{model_name}%")
        if status:
            conditions.append("status = ?")
            params.append(status)
        if audit_id:
            conditions.append("audit_id = ?")
            params.append(audit_id)
        if date_from:
            conditions.append("created_at >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("created_at <= ?")
            params.append(date_to + "T23:59:59")
        if stage:
            conditions.append("stage = ?")
            params.append(stage)
        where = " AND ".join(conditions)
        count_row = conn.execute(
            f"SELECT COUNT(*) as cnt FROM llm_trace_spans WHERE {where}", params
        ).fetchone()
        total = count_row["cnt"] if count_row else 0
        offset = (page - 1) * page_size
        rows = conn.execute(
            f"SELECT * FROM llm_trace_spans WHERE {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [page_size, offset],
        ).fetchall()
        conn.close()
        return {"items": [dict(r) for r in rows], "total": total, "page": page, "page_size": page_size}

    def get_trace_detail(self, span_id: str) -> Optional[Dict[str, Any]]:
        """获取单个 span 详情（含 JSONL 中的完整内容）。"""
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM llm_trace_spans WHERE span_id = ?", [span_id]
        ).fetchone()
        conn.close()
        if not row:
            return None
        result = dict(row)
        # 尝试从 JSONL 中获取完整内容
        trace_id = result.get("trace_id", "")
        created_at = result.get("created_at", "")
        if trace_id and created_at:
            try:
                day = created_at[:10]  # "2026-07-03"
                tagged = _sanitize_filename(trace_id)
                trace_file = os.path.join(
                    self._jsonl_dir, day, f"trace_{tagged}.jsonl")
                if os.path.exists(trace_file):
                    events = []
                    with open(trace_file, "r", encoding="utf-8") as f:
                        for line in f:
                            try:
                                evt = json.loads(line.strip())
                                if evt.get("span_id") == span_id:
                                    events.append(evt)
                            except Exception:
                                continue
                    if events:
                        latest = events[-1]
                        result["full_request_messages"] = latest.get(
                            "messages", [])
                        result["full_response_content"] = latest.get(
                            "response_content", "")
                        result["full_thinking_content"] = latest.get(
                            "thinking_content", "")
                        result["jsonl_events"] = events
            except Exception as e:
                logger.warning("llm_trace_detail_jsonl_failed",
                               span_id=span_id, error=str(e))
        return result

    def get_stats(
        self,
        *,
        date_from: str = "",
        date_to: str = "",
    ) -> Dict[str, Any]:
        """聚合统计：按模型维度的成功/失败/平均耗时/Token 消耗。"""
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conditions = ["1=1"]
        params: List[Any] = []
        if date_from:
            conditions.append("created_at >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("created_at <= ?")
            params.append(date_to + "T23:59:59")
        where = " AND ".join(conditions)
        model_stats = conn.execute(
            f"""
            SELECT
                model_name,
                COUNT(*) as total_calls,
                SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) as success_count,
                SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as failed_count,
                AVG(CASE WHEN status='success' THEN total_latency_ms END) as avg_latency_ms,
                AVG(CASE WHEN status='success' THEN total_tokens END) as avg_total_tokens,
                SUM(prompt_tokens) as sum_prompt_tokens,
                SUM(completion_tokens) as sum_completion_tokens,
                AVG(ollama_load_duration_ms) as avg_ollama_load_ms,
                AVG(ollama_eval_duration_ms) as avg_ollama_eval_ms,
                COUNT(DISTINCT trace_id) as distinct_traces,
                COUNT(DISTINCT audit_id) as distinct_audits
            FROM llm_trace_spans
            WHERE {where}
            GROUP BY model_name
            ORDER BY total_calls DESC
            """,
            params,
        ).fetchall()
        stage_stats = conn.execute(
            f"""
            SELECT stage, COUNT(*) as cnt, AVG(total_latency_ms) as avg_latency_ms
            FROM llm_trace_spans WHERE {where}
            GROUP BY stage ORDER BY cnt DESC
            """,
            params,
        ).fetchall()
        daily_stats = conn.execute(
            f"""
            SELECT date(created_at) as day, COUNT(*) as cnt,
                   SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) as ok_count,
                   SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as fail_count
            FROM llm_trace_spans WHERE {where}
            GROUP BY day ORDER BY day DESC LIMIT 30
            """,
            params,
        ).fetchall()
        conn.close()
        return {
            "by_model": [dict(r) for r in model_stats],
            "by_stage": [dict(r) for r in stage_stats],
            "daily": [dict(r) for r in daily_stats],
        }

    def delete_old_traces(self, before_date: str) -> int:
        """清理指定日期之前的旧日志。"""
        conn = sqlite3.connect(self._db_path, timeout=10)
        cur = conn.execute(
            "DELETE FROM llm_trace_spans WHERE created_at < ?", [before_date]
        )
        deleted = cur.rowcount
        conn.commit()
        conn.close()
        logger.info("llm_trace_cleanup_done",
                    before=before_date, deleted=deleted)
        return deleted


# ==================== 工具函数 ====================

def _hash_messages(messages: Optional[List[Dict[str, Any]]]) -> str:
    if not messages:
        return ""
    import hashlib
    raw = json.dumps(messages, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _clip(text: str, max_chars: int) -> str:
    s = str(text or "")
    if len(s) <= max_chars:
        return s
    return s[:max_chars] + f"...<truncated:{len(s)-max_chars}>"


def _ms_or_0(value: Any) -> int:
    try:
        ns = int(value) if value is not None else 0
        return max(0, ns // 1_000_000)  # Ollama 返回纳秒
    except (ValueError, TypeError):
        return 0


def _sanitize_for_jsonl(messages: Optional[List[Dict[str, Any]]], max_chars: int) -> List[Dict[str, Any]]:
    if not messages:
        return []
    out = []
    for m in messages:
        if isinstance(m, dict):
            out.append({
                "role": str(m.get("role") or ""),
                "content": _clip(str(m.get("content") or ""), max_chars),
            })
        else:
            out.append(
                {"role": "unknown", "content": _clip(str(m), max_chars)})
    return out


def _sanitize_filename(s: str) -> str:
    out = []
    for ch in str(s or ""):
        if ch.isalnum() or ch in ("-", "_"):
            out.append(ch)
        else:
            out.append("_")
    result = "".join(out).strip("_")
    return result[:120] if result else "unknown"


# ==================== 全局便捷函数 ====================

_global_collector: Optional[LlmTraceCollector] = None
_global_lock = threading.Lock()


def get_trace_collector(cfg: Optional[Dict[str, Any]] = None) -> LlmTraceCollector:
    """获取全局单例 Trace 收集器。"""
    global _global_collector
    with _global_lock:
        if _global_collector is None and cfg is not None:
            _global_collector = LlmTraceCollector(cfg)
        if _global_collector is None:
            _global_collector = LlmTraceCollector(
                {"llm_trace_full_enabled": True})
    return _global_collector


def new_trace_id() -> str:
    return f"trace_{uuid.uuid4().hex[:16]}"
