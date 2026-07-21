import os
import uuid
import json
import csv
import io
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Literal, Dict, Any, Tuple
from fastapi import APIRouter, HTTPException, Depends, Query, Request, Response
from pydantic import BaseModel, Field
import httpx
from app.core.auth import get_all_users, update_user_role, log_audit
from app.api.dependencies import require_admin, get_app_llm
from app.core.database import get_conn
from app.core.config import get_config, update_config_patch
from app.core.llm_trace import get_trace_collector
from app.core.secure_store import has_llm_api_key, set_llm_api_key, delete_llm_api_key
from app.vector_store.factory import VectorStoreFactory
from app.services.memory_promotion import (
    list_pending_rule_memories,
    promote_episode_to_rule,
    review_rule_memory,
)
from app.services.audit_capabilities import (
    create_skill,
    delete_skill,
    get_admin_skill_detail,
    list_admin_skills,
    set_skill_status,
    update_skill,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])
logger = logging.getLogger("law_assistant")
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_LLAMACPP_HOST = "http://127.0.0.1:18080"
OLLAMA_CACHE_TTL_SEC = 300
_OLLAMA_MODELS_CACHE: Dict[str, Any] = {
    "host": "",
    "ts": 0.0,
    "models": [],
    "error": "",
}
_OLLAMA_MODELS_CACHE_LOCK = threading.Lock()
LLAMACPP_CACHE_TTL_SEC = 300
_LLAMACPP_MODELS_CACHE: Dict[str, Any] = {
    "host": "",
    "ts": 0.0,
    "models": [],
    "error": "",
}
_LLAMACPP_MODELS_CACHE_LOCK = threading.Lock()


# ============ Document Models ============
class DocumentResponse(BaseModel):
    id: str
    filename: str
    original_filename: str
    file_size: int
    mime_type: Optional[str]
    user_id: str
    username: Optional[str]
    title: Optional[str]
    category: Optional[str]
    status: str
    created_at: str


class DocumentListResponse(BaseModel):
    items: List[DocumentResponse]
    total: int
    page: int
    page_size: int


class DeleteResponse(BaseModel):
    message: str
    deleted_id: str


# ============ Skill Management Models ============
class SkillResponse(BaseModel):
    id: str
    display_name: str
    category: str
    scene: str
    description: str = ""
    owner_type: str
    owner_id: Optional[str] = None
    visibility: str
    status: str
    source_url: str = ""
    reference_summary: str = ""
    input_schema: Dict[str, Any] = {}
    output_schema: Dict[str, Any] = {}
    config_schema: Dict[str, Any] = {}
    tags: List[str] = []
    sort_order: int
    template_ref_count: int = 0
    agent_ref_count: int = 0
    in_use: bool = False
    created_at: str
    updated_at: Optional[str] = None


class SkillListResponse(BaseModel):
    items: List[SkillResponse]
    total: int
    page: int
    page_size: int


class SkillCreateRequest(BaseModel):
    id: Optional[str] = Field(default=None, max_length=64)
    display_name: str = Field(..., min_length=1, max_length=120)
    category: str = Field(..., min_length=1, max_length=60)
    scene: str = Field(..., min_length=1, max_length=80)
    description: str = ""
    visibility: str = "public"
    status: str = "active"
    source_url: str = ""
    reference_summary: str = ""
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    output_schema: Dict[str, Any] = Field(default_factory=dict)
    config_schema: Dict[str, Any] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)
    sort_order: int = 100


class SkillUpdateRequest(BaseModel):
    display_name: Optional[str] = Field(
        default=None, min_length=1, max_length=120)
    category: Optional[str] = Field(default=None, min_length=1, max_length=60)
    scene: Optional[str] = Field(default=None, min_length=1, max_length=80)
    description: Optional[str] = None
    visibility: Optional[str] = None
    status: Optional[str] = None
    source_url: Optional[str] = None
    reference_summary: Optional[str] = None
    input_schema: Optional[Dict[str, Any]] = None
    output_schema: Optional[Dict[str, Any]] = None
    config_schema: Optional[Dict[str, Any]] = None
    tags: Optional[List[str]] = None
    sort_order: Optional[int] = None


class SkillStatusUpdateRequest(BaseModel):
    status: Literal["active", "disabled"]


# ============ Stats Models ============
class StatsResponse(BaseModel):
    total_documents: int
    total_users: int
    total_size: int
    documents_by_category: dict
    documents_by_user: dict


class LLMConfigResponse(BaseModel):
    provider: str
    api_base: str
    model: str
    temperature: float
    max_tokens: int
    timeout: int
    headers: dict
    has_api_key: bool
    config_source: str = "llm_config"
    local_llm_enabled: bool = False


class OllamaModelItem(BaseModel):
    name: str
    model: str
    parameter_size: str = ""
    updated_at: str = ""
    size_bytes: int = 0
    family: str = ""
    quantization_level: str = ""
    capabilities: List[str] = []


class OllamaModelListResponse(BaseModel):
    ok: bool
    host: str
    reachable: bool
    cached: bool
    stale: bool = False
    cache_ttl_sec: int
    cached_at: str = ""
    expires_at: str = ""
    current_model: str = ""
    recommended_model: str = ""
    models: List[OllamaModelItem]
    error: str = ""


class LlamaCppModelItem(BaseModel):
    name: str
    model: str
    owner: str = ""
    context_length: int = 0
    size_bytes: int = 0
    family: str = ""
    quantization_level: str = ""


class LlamaCppModelListResponse(BaseModel):
    ok: bool
    host: str
    reachable: bool
    cached: bool
    stale: bool = False
    cache_ttl_sec: int
    cached_at: str = ""
    expires_at: str = ""
    current_model: str = ""
    recommended_model: str = ""
    models: List[LlamaCppModelItem]
    error: str = ""


class LLMConfigUpdate(BaseModel):
    provider: str
    api_base: str
    api_key: str
    model: str
    temperature: float
    max_tokens: int
    timeout: int
    headers: dict


class UIConfigResponse(BaseModel):
    show_citation_source: bool
    default_theme: Literal["dark", "light"]


class UIConfigUpdate(BaseModel):
    show_citation_source: bool
    default_theme: Optional[str] = None


class MemoryRuntimeConfigResponse(BaseModel):
    memory_module_enabled: bool
    memory_mode_when_disabled: str
    memory_disable_fallback_on_error: bool
    memory_token_guard_enabled: bool
    memory_max_llm_calls_per_audit: int
    memory_max_prompt_chars_per_clause: int
    memory_temporary_disable_enabled: bool
    memory_temporary_disable_fallback_mode: str
    memory_temporary_disable_reason: str
    memory_temporary_disable_trigger_source: str
    risk_notice: str


class MemoryRuntimeConfigUpdate(BaseModel):
    memory_module_enabled: Optional[bool] = None
    memory_mode_when_disabled: Optional[str] = None
    memory_disable_fallback_on_error: Optional[bool] = None
    memory_token_guard_enabled: Optional[bool] = None
    memory_max_llm_calls_per_audit: Optional[int] = None
    memory_max_prompt_chars_per_clause: Optional[int] = None
    memory_temporary_disable_enabled: Optional[bool] = None
    memory_temporary_disable_fallback_mode: Optional[str] = None
    memory_temporary_disable_reason: Optional[str] = None
    memory_temporary_disable_trigger_source: Optional[str] = None


class LLMTestRequest(BaseModel):
    prompt: Optional[str] = None


class LLMTestResponse(BaseModel):
    ok: bool
    prompt: str
    answer: str


class TokenUsageTotals(BaseModel):
    input_tokens: int
    output_tokens: int
    total_tokens: int
    request_count: int


class TokenUsageSeriesItem(BaseModel):
    bucket: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    request_count: int


class TokenUsageRankingItem(BaseModel):
    key: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    request_count: int


class TokenUsageAlert(BaseModel):
    level: str
    reason: str
    value: int
    threshold: int
    bucket: Optional[str] = None
    meta: Optional[dict] = None


class TokenUsageResponse(BaseModel):
    range_start: str
    range_end: str
    granularity: str
    rank_by: str
    totals: TokenUsageTotals
    series: List[TokenUsageSeriesItem]
    rankings: List[TokenUsageRankingItem]
    alerts: List[TokenUsageAlert]
    last_updated: str


class MemoryPromotionRunRequest(BaseModel):
    episode_id: Optional[str] = None
    min_support_count: Optional[int] = None
    min_quality_score: Optional[float] = None
    high_impact_threshold: Optional[float] = None


class MemoryRuleReviewRequest(BaseModel):
    action: Literal["approve", "reject"]
    note: Optional[str] = None


def _clean_text(v: str) -> str:
    s = str(v or "").strip()
    s = s.strip("`").strip('"').strip("'").strip()
    return s


def _clean_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _normalize_ollama_host(value: str) -> str:
    host = _clean_text(value) or DEFAULT_OLLAMA_HOST
    for suffix in ("/chat/completions", "/api/chat", "/api/generate", "/v1"):
        if host.endswith(suffix):
            host = host[: -len(suffix)]
    return host.rstrip("/")


def _normalize_openai_host(value: str, default_host: str = DEFAULT_LLAMACPP_HOST) -> str:
    host = _clean_text(value) or default_host
    for suffix in ("/chat/completions", "/v1/chat/completions", "/v1/completions", "/v1"):
        if host.endswith(suffix):
            host = host[: -len(suffix)]
    return host.rstrip("/")


def _iso_from_ts(ts: float) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _effective_llm_config(cfg: dict) -> Tuple[dict, str, bool]:
    local_cfg = _clean_dict(cfg.get("local_llm"))
    local_enabled = bool(local_cfg.get("enabled", False))
    main_cfg = _clean_dict(local_cfg.get("main_model"))
    if local_enabled and _clean_text(main_cfg.get("api_base")) and _clean_text(main_cfg.get("model")):
        return main_cfg, "local_main_model", True
    llm_cfg = _clean_dict(cfg.get("llm_config"))
    return llm_cfg, "llm_config", local_enabled


def _choose_recommended_ollama_model(models: List[Dict[str, Any]]) -> str:
    names = {
        _clean_text(item.get("name") or item.get("model"))
        for item in models
        if isinstance(item, dict)
    }
    preferred = [
        "qwen3.6:27b",
        "qwen3-coder-next:latest",
        "qwen3:8b",
        "deepseek-r1:14b",
        "qwen3:4b",
        "glm4:latest",
        "llama3:latest",
        "llama3.2:latest",
    ]
    for item in preferred:
        if item in names:
            return item
    return sorted(names)[0] if names else ""


def _extract_ollama_models(payload: dict) -> List[Dict[str, Any]]:
    rows = payload.get("models") if isinstance(
        payload.get("models"), list) else []
    out: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        details = _clean_dict(row.get("details"))
        out.append({
            "name": _clean_text(row.get("name") or row.get("model")),
            "model": _clean_text(row.get("model") or row.get("name")),
            "parameter_size": _clean_text(details.get("parameter_size")),
            "updated_at": _clean_text(row.get("modified_at")),
            "size_bytes": int(row.get("size") or 0),
            "family": _clean_text(details.get("family")),
            "quantization_level": _clean_text(details.get("quantization_level")),
            "capabilities": [
                _clean_text(item)
                for item in (row.get("capabilities") or [])
                if _clean_text(item)
            ],
        })
    out.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return out


def _get_cached_ollama_models(host: str, force_refresh: bool = False) -> Tuple[List[Dict[str, Any]], bool, bool, str, float]:
    now = time.time()
    with _OLLAMA_MODELS_CACHE_LOCK:
        cache_host = _clean_text(_OLLAMA_MODELS_CACHE.get("host"))
        cache_ts = float(_OLLAMA_MODELS_CACHE.get("ts") or 0.0)
        cache_models = list(_OLLAMA_MODELS_CACHE.get("models") or [])
        cache_error = _clean_text(_OLLAMA_MODELS_CACHE.get("error"))
        cache_ok = bool(cache_models) and cache_host == host and (
            now - cache_ts) < OLLAMA_CACHE_TTL_SEC
        if cache_ok and not force_refresh:
            return cache_models, True, False, cache_error, cache_ts
    try:
        response = httpx.get(f"{host}/api/tags", timeout=10)
        response.raise_for_status()
        models = _extract_ollama_models(response.json())
        with _OLLAMA_MODELS_CACHE_LOCK:
            _OLLAMA_MODELS_CACHE["host"] = host
            _OLLAMA_MODELS_CACHE["ts"] = now
            _OLLAMA_MODELS_CACHE["models"] = models
            _OLLAMA_MODELS_CACHE["error"] = ""
        return models, False, False, "", now
    except Exception as e:
        err = f"ollama models fetch failed: {str(e)}"
        with _OLLAMA_MODELS_CACHE_LOCK:
            cache_host = _clean_text(_OLLAMA_MODELS_CACHE.get("host"))
            cache_ts = float(_OLLAMA_MODELS_CACHE.get("ts") or 0.0)
            cache_models = list(_OLLAMA_MODELS_CACHE.get("models") or [])
        if cache_models and cache_host == host:
            return cache_models, False, True, err, cache_ts
        return [], False, False, err, 0.0


def _extract_llamacpp_models(payload: dict) -> List[Dict[str, Any]]:
    rows = payload.get("data") if isinstance(payload.get("data"), list) else []
    out: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        meta = _clean_dict(row.get("metadata"))
        out.append({
            "name": _clean_text(row.get("id") or row.get("model")),
            "model": _clean_text(row.get("id") or row.get("model")),
            "owner": _clean_text(row.get("owned_by")),
            "context_length": int(meta.get("context_length") or meta.get("n_ctx_train") or 0),
            "size_bytes": int(meta.get("size_bytes") or meta.get("model_size") or 0),
            "family": _clean_text(meta.get("family") or meta.get("general.architecture")),
            "quantization_level": _clean_text(meta.get("quantization") or meta.get("general.file_type")),
        })
    return sorted(out, key=lambda item: item.get("name", ""))


def _get_cached_llamacpp_models(host: str, force_refresh: bool = False) -> Tuple[List[Dict[str, Any]], bool, bool, str, float]:
    now = time.time()
    with _LLAMACPP_MODELS_CACHE_LOCK:
        cache_host = _clean_text(_LLAMACPP_MODELS_CACHE.get("host"))
        cache_ts = float(_LLAMACPP_MODELS_CACHE.get("ts") or 0.0)
        cache_models = list(_LLAMACPP_MODELS_CACHE.get("models") or [])
        cache_error = _clean_text(_LLAMACPP_MODELS_CACHE.get("error"))
        cache_ok = bool(cache_models) and cache_host == host and (
            now - cache_ts) < LLAMACPP_CACHE_TTL_SEC
        if cache_ok and not force_refresh:
            return cache_models, True, False, cache_error, cache_ts
    try:
        response = httpx.get(f"{host}/v1/models", timeout=10)
        response.raise_for_status()
        models = _extract_llamacpp_models(response.json())
        with _LLAMACPP_MODELS_CACHE_LOCK:
            _LLAMACPP_MODELS_CACHE["host"] = host
            _LLAMACPP_MODELS_CACHE["ts"] = now
            _LLAMACPP_MODELS_CACHE["models"] = models
            _LLAMACPP_MODELS_CACHE["error"] = ""
        return models, False, False, "", now
    except Exception as e:
        err = f"llama.cpp models fetch failed: {str(e)}"
        with _LLAMACPP_MODELS_CACHE_LOCK:
            cache_host = _clean_text(_LLAMACPP_MODELS_CACHE.get("host"))
            cache_ts = float(_LLAMACPP_MODELS_CACHE.get("ts") or 0.0)
            cache_models = list(_LLAMACPP_MODELS_CACHE.get("models") or [])
        if cache_models and cache_host == host:
            return cache_models, False, True, err, cache_ts
        return [], False, False, err, 0.0


def _ollama_unreachable_message(llm_cfg: dict) -> str:
    configured = _normalize_ollama_host(
        _clean_text(llm_cfg.get("api_base")) or DEFAULT_OLLAMA_HOST
    )
    return f"Ollama 服务未启动或 {configured} 不可达，请先启动 Ollama 后重试。"


def _llamacpp_unreachable_message(llm_cfg: dict) -> str:
    configured = _normalize_openai_host(
        _clean_text(llm_cfg.get("api_base")) or DEFAULT_LLAMACPP_HOST
    )
    return f"llama.cpp 服务未启动或 {configured} 不可达，请先启动 llama-server 后重试。"


def _friendly_llm_test_error(llm_cfg: dict, raw_err: str) -> str:
    low_err = raw_err.lower()
    provider = _clean_text(llm_cfg.get("provider")).lower()
    model = _clean_text(llm_cfg.get("model"))
    configured_host = _clean_text(llm_cfg.get("api_base"))

    if provider == "ollama":
        ollama_host = _normalize_ollama_host(
            configured_host or DEFAULT_OLLAMA_HOST)
        if "10061" in low_err or "connection refused" in low_err:
            return f"Ollama 服务未启动，或 {ollama_host} 不可达。请先启动本地 Ollama 后重试。"
        if "not found" in low_err and "model" in low_err:
            return f"Ollama 模型不存在：{model}。请先执行 `ollama pull`，或切换到已安装模型。"
        if "timed out" in low_err or "timeout" in low_err:
            return f"Ollama 模型响应超时：{model}。请增大超时时间，或切换到更小模型。"

    if provider == "llama_cpp":
        llamacpp_host = _normalize_openai_host(
            configured_host or DEFAULT_LLAMACPP_HOST)
        if "10061" in low_err or "connection refused" in low_err:
            return f"llama.cpp 服务未启动，或 {llamacpp_host} 不可达。请先启动本地 llama-server 后重试。"
        if "not found" in low_err and "model" in low_err:
            return f"llama.cpp 未加载目标模型：{model}。请确认 llama-server 的 `--alias` 与管理端配置一致。"
        if "timed out" in low_err or "timeout" in low_err:
            return f"llama.cpp 模型响应超时：{model}。请检查 GGUF 量化规格、上下文窗口或线程参数。"

    if "10061" in low_err or "connection refused" in low_err:
        return f"LLM 接口不可达：{configured_host}。请确认当前配置指向本地端侧模型服务。"
    return raw_err


def _sync_llm_patch(cfg_current: dict, data: dict) -> dict:
    patch = {"llm_config": dict(data)}
    local_cfg = _clean_dict(cfg_current.get("local_llm"))
    if bool(local_cfg.get("enabled", False)):
        next_local = dict(local_cfg)
        main_cfg = _clean_dict(local_cfg.get("main_model"))
        next_main = dict(main_cfg)
        for key in ("provider", "api_base", "model", "temperature", "max_tokens", "timeout", "headers"):
            next_main[key] = data.get(key)
        next_main["api_key"] = ""
        next_local["main_model"] = next_main
        patch["local_llm"] = next_local
    return patch


def _normalize_llm_payload(payload: dict) -> dict:
    data = dict(payload or {})
    data["api_base"] = _clean_text(data.get("api_base", ""))
    data["model"] = _clean_text(data.get("model", ""))
    data["api_key"] = _clean_text(data.get("api_key", ""))
    if not isinstance(data.get("headers"), dict):
        data["headers"] = {}
    return data


def _validate_llm_config(payload: dict):
    api_base = str(payload.get("api_base", "")).strip()
    model = str(payload.get("model", "")).strip()
    if not api_base.startswith("http"):
        raise HTTPException(
            status_code=400, detail="api_base must be a valid http(s) url")
    if not model:
        raise HTTPException(status_code=400, detail="model is required")
    temperature = float(payload.get("temperature", 0.2))
    if temperature < 0 or temperature > 2:
        raise HTTPException(status_code=400, detail="temperature out of range")
    max_tokens = int(payload.get("max_tokens", 2048))
    if max_tokens <= 0:
        raise HTTPException(
            status_code=400, detail="max_tokens must be positive")
    timeout = int(payload.get("timeout", 60))
    if timeout <= 0:
        raise HTTPException(status_code=400, detail="timeout must be positive")


def _ensure_llm_secret_migrated(cfg: dict) -> dict:
    llm_cfg = cfg.get("llm_config") if isinstance(
        cfg.get("llm_config"), dict) else {}
    plain = _clean_text(llm_cfg.get("api_key", ""))
    if not plain:
        return cfg
    if not set_llm_api_key(cfg, plain):
        return cfg
    next_llm = dict(llm_cfg)
    next_llm["api_key"] = ""
    return update_config_patch({"llm_config": next_llm})


def _normalize_theme(v: Optional[str]) -> str:
    s = str(v or "").strip().lower()
    return "light" if s == "light" else "dark"


def _get_ui_config(cfg: dict) -> dict:
    ui_cfg = cfg.get("ui_config") if isinstance(
        cfg.get("ui_config"), dict) else {}
    return {
        "show_citation_source": bool(ui_cfg.get("show_citation_source", False)),
        "default_theme": _normalize_theme(ui_cfg.get("default_theme")),
    }


def _get_memory_runtime_config(cfg: dict) -> dict:
    raw = cfg.get("memory_runtime_config") if isinstance(
        cfg.get("memory_runtime_config"), dict) else {}
    temp_disable = cfg.get("memory_temporary_disable") if isinstance(
        cfg.get("memory_temporary_disable"), dict) else {}
    mode = str(raw.get("memory_mode_when_disabled")
               or "classic").strip().lower()
    if mode not in {"classic"}:
        mode = "classic"
    temp_mode = str(temp_disable.get("fallback_mode")
                    or "classic").strip().lower()
    if temp_mode not in {"classic"}:
        temp_mode = "classic"
    max_calls = int(raw.get("memory_max_llm_calls_per_audit") or 12)
    max_prompt_chars = int(
        raw.get("memory_max_prompt_chars_per_clause") or 2400)
    max_calls = max(1, min(max_calls, 200))
    max_prompt_chars = max(300, min(max_prompt_chars, 12000))
    return {
        "memory_module_enabled": bool(raw.get("memory_module_enabled", True)),
        "memory_mode_when_disabled": mode,
        "memory_disable_fallback_on_error": bool(raw.get("memory_disable_fallback_on_error", True)),
        "memory_token_guard_enabled": bool(raw.get("memory_token_guard_enabled", True)),
        "memory_max_llm_calls_per_audit": max_calls,
        "memory_max_prompt_chars_per_clause": max_prompt_chars,
        "memory_temporary_disable_enabled": bool(temp_disable.get("enabled", False)),
        "memory_temporary_disable_fallback_mode": temp_mode,
        "memory_temporary_disable_reason": str(temp_disable.get("reason") or "edge_llm_context_limit").strip() or "edge_llm_context_limit",
        "memory_temporary_disable_trigger_source": str(temp_disable.get("trigger_source") or "config.memory_temporary_disable").strip() or "config.memory_temporary_disable",
        "risk_notice": "Enabling memory mode may increase LLM calls and token cost; temporary-disable can force classic fallback for edge-device stability.",
    }


def _llm_trace_dir(cfg: dict) -> str:
    trace_dir = str(cfg.get("llm_trace_dir") or "").strip()
    if not trace_dir:
        base = str(cfg.get("data_dir") or "").strip()
        trace_dir = os.path.join(
            base, "llm_interactions") if base else os.path.abspath("llm_interactions")
    return os.path.abspath(trace_dir)


def _parse_dt(v: Optional[str], fallback: Optional[datetime] = None) -> datetime:
    if not v:
        return fallback or datetime.utcnow()
    s = str(v).strip()
    if not s:
        return fallback or datetime.utcnow()
    if len(s) == 10 and s.count("-") == 2:
        try:
            return datetime.fromisoformat(s)
        except Exception:
            return fallback or datetime.utcnow()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return fallback or datetime.utcnow()


def _bucket_key(dt: datetime, granularity: str) -> str:
    if granularity == "hour":
        return dt.strftime("%Y-%m-%d %H:00")
    if granularity == "week":
        iso_year, iso_week, _ = dt.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
    return dt.strftime("%Y-%m-%d")


def _iter_trace_rows(trace_dir: str, start_dt: datetime, end_dt: datetime, max_rows: int):
    day = datetime(start_dt.year, start_dt.month, start_dt.day)
    end_day = datetime(end_dt.year, end_dt.month, end_dt.day)
    count = 0
    while day <= end_day:
        day_dir = os.path.join(trace_dir, day.strftime("%Y-%m-%d"))
        file_path = os.path.join(day_dir, "llm_trace.jsonl")
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    for line in f:
                        if max_rows and count >= max_rows:
                            return
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            row = json.loads(line)
                        except Exception:
                            continue
                        ts = _parse_dt(row.get("ts"))
                        if ts < start_dt or ts > end_dt:
                            continue
                        count += 1
                        yield row
            except OSError as e:
                logger.warning(
                    "llm_trace_read_failed file=%s err=%s",
                    file_path,
                    str(e),
                )
        day += timedelta(days=1)


# ============ Document CRUD ============
@router.get("/documents", response_model=DocumentListResponse)
def list_documents(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    search: Optional[str] = None,
    category: Optional[str] = None,
    user_id: Optional[str] = None,
    current_user: dict = Depends(require_admin)
):
    conn = get_conn(get_config())
    cur = conn.cursor()

    where_clauses = ["d.status = 'active'"]
    params = []

    if search:
        where_clauses.append("(d.original_filename LIKE ? OR d.title LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])

    if category:
        if category == "legal":
            where_clauses.append(
                "(d.category = ? OR ((d.category IS NULL OR TRIM(d.category) = '') AND EXISTS (SELECT 1 FROM regulation_version v WHERE v.source_file = d.file_path)))"
            )
            params.append(category)
        else:
            where_clauses.append("d.category = ?")
            params.append(category)

    if user_id:
        where_clauses.append("d.user_id = ?")
        params.append(user_id)

    where_sql = " AND ".join(where_clauses)

    cur.execute(f"""
        SELECT COUNT(*) as total FROM documents d WHERE {where_sql}
    """, params)
    total = cur.fetchone()[0]

    offset = (page - 1) * page_size
    cur.execute(f"""
        SELECT d.*, u.username 
        FROM documents d 
        LEFT JOIN users u ON d.user_id = u.id
        WHERE {where_sql}
        ORDER BY d.created_at DESC
        LIMIT ? OFFSET ?
    """, params + [page_size, offset])

    rows = cur.fetchall()
    conn.close()

    items = []
    for row in rows:
        items.append(DocumentResponse(
            id=row["id"],
            filename=row["filename"],
            original_filename=row["original_filename"],
            file_size=row["file_size"],
            mime_type=row["mime_type"],
            user_id=row["user_id"],
            username=row["username"],
            title=row["title"],
            category=row["category"],
            status=row["status"],
            created_at=row["created_at"]
        ))

    return DocumentListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size
    )


@router.delete("/documents/{doc_id}", response_model=DeleteResponse)
def delete_document(
    doc_id: str,
    request: Request,
    current_user: dict = Depends(require_admin)
):
    conn = get_conn(get_config())
    cur = conn.cursor()

    cur.execute("SELECT * FROM documents WHERE id = ?", (doc_id,))
    doc = cur.fetchone()

    if not doc:
        conn.close()
        raise HTTPException(status_code=404, detail="Document not found")

    cur.execute("UPDATE documents SET status = 'deleted', deleted_at = ? WHERE id = ?",
                (datetime.utcnow().isoformat(), doc_id))
    conn.commit()
    conn.close()

    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    log_audit(
        current_user["id"], "delete", "document", doc_id,
        ip_address, user_agent, f"Deleted document: {doc['original_filename']}"
    )

    return DeleteResponse(message="Document deleted successfully", deleted_id=doc_id)


# ============ User Management ============
@router.get("/users")
def list_users(current_user: dict = Depends(require_admin)):
    users = get_all_users()
    return users


@router.put("/users/{user_id}/role")
def update_role(
    user_id: str,
    role: str,
    request: Request,
    current_user: dict = Depends(require_admin)
):
    if role not in ["user", "admin"]:
        raise HTTPException(status_code=400, detail="Invalid role")

    success = update_user_role(user_id, role)
    if not success:
        raise HTTPException(status_code=404, detail="User not found")

    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    log_audit(
        current_user["id"], "update_role", "user", user_id,
        ip_address, user_agent, f"Changed role to {role}"
    )

    return {"message": f"Role updated to {role}"}


@router.delete("/users/{user_id}", response_model=DeleteResponse)
def delete_user(
    user_id: str,
    request: Request,
    current_user: dict = Depends(require_admin)
):
    if str(current_user.get("id")) == str(user_id):
        raise HTTPException(
            status_code=400, detail="Cannot delete current user")

    conn = get_conn(get_config())
    cur = conn.cursor()

    cur.execute("SELECT id, username, role FROM users WHERE id = ?", (user_id,))
    target = cur.fetchone()
    if not target:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")

    if str(target["role"]) == "admin":
        cur.execute(
            "SELECT COUNT(*) as cnt FROM users WHERE role = 'admin' AND is_active = 1 AND id <> ?",
            (user_id,),
        )
        other_admin_count = int(cur.fetchone()["cnt"])
        if other_admin_count <= 0:
            conn.close()
            raise HTTPException(
                status_code=400, detail="Cannot delete the last active admin")

    cur.execute(
        "SELECT COUNT(*) as cnt FROM documents WHERE user_id = ? AND status = 'active'",
        (user_id,),
    )
    active_doc_count = int(cur.fetchone()["cnt"])
    if active_doc_count > 0:
        conn.close()
        raise HTTPException(
            status_code=400, detail="Please delete this user's active documents first")

    cur.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    cur.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()

    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    log_audit(
        current_user["id"], "delete", "user", user_id,
        ip_address, user_agent, f"Deleted user: {target['username']}"
    )

    return DeleteResponse(message="User deleted successfully", deleted_id=user_id)


# ============ Skill Management ============
@router.get("/skills", response_model=SkillListResponse)
def admin_get_skills(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    search: str = Query(""),
    category: str = Query(""),
    scene: str = Query(""),
    status: str = Query(""),
    current_user: dict = Depends(require_admin),
):
    try:
        result = list_admin_skills(
            get_config(),
            page=page,
            page_size=page_size,
            search=search,
            category=category,
            scene=scene,
            status=status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SkillListResponse(
        items=[SkillResponse(**item) for item in result["items"]],
        total=result["total"],
        page=result["page"],
        page_size=result["page_size"],
    )


@router.get("/skills/{skill_id}", response_model=SkillResponse)
def admin_get_skill_detail(
    skill_id: str,
    current_user: dict = Depends(require_admin),
):
    item = get_admin_skill_detail(get_config(), skill_id)
    if not item:
        raise HTTPException(status_code=404, detail="Skill not found")
    return SkillResponse(**item)


@router.post("/skills", response_model=SkillResponse)
def admin_create_skill(
    payload: SkillCreateRequest,
    request: Request,
    current_user: dict = Depends(require_admin),
):
    try:
        item = create_skill(get_config(), str(
            current_user.get("id") or ""), payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    log_audit(
        current_user["id"],
        "create",
        "skill",
        item["id"],
        ip_address,
        user_agent,
        f"Created skill: {item['display_name']}",
    )
    return SkillResponse(**item)


@router.put("/skills/{skill_id}", response_model=SkillResponse)
def admin_update_skill(
    skill_id: str,
    payload: SkillUpdateRequest,
    request: Request,
    current_user: dict = Depends(require_admin),
):
    try:
        item = update_skill(get_config(), skill_id,
                            payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        message = str(exc)
        if message == "skill not found":
            raise HTTPException(
                status_code=404, detail="Skill not found") from exc
        raise HTTPException(status_code=400, detail=message) from exc

    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    log_audit(
        current_user["id"],
        "update",
        "skill",
        item["id"],
        ip_address,
        user_agent,
        f"Updated skill: {item['display_name']}",
    )
    return SkillResponse(**item)


@router.post("/skills/{skill_id}/status", response_model=SkillResponse)
def admin_set_skill_status(
    skill_id: str,
    payload: SkillStatusUpdateRequest,
    request: Request,
    current_user: dict = Depends(require_admin),
):
    try:
        item = set_skill_status(get_config(), skill_id, payload.status)
    except ValueError as exc:
        message = str(exc)
        if message == "skill not found":
            raise HTTPException(
                status_code=404, detail="Skill not found") from exc
        raise HTTPException(status_code=400, detail=message) from exc

    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    log_audit(
        current_user["id"],
        "update_status",
        "skill",
        item["id"],
        ip_address,
        user_agent,
        f"Set skill status to {item['status']}",
    )
    return SkillResponse(**item)


@router.delete("/skills/{skill_id}", response_model=DeleteResponse)
def admin_delete_skill(
    skill_id: str,
    request: Request,
    current_user: dict = Depends(require_admin),
):
    try:
        item = delete_skill(get_config(), skill_id)
    except ValueError as exc:
        message = str(exc)
        if message == "skill not found":
            raise HTTPException(
                status_code=404, detail="Skill not found") from exc
        raise HTTPException(status_code=400, detail=message) from exc

    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    log_audit(
        current_user["id"],
        "delete",
        "skill",
        skill_id,
        ip_address,
        user_agent,
        f"Deleted skill: {item['display_name']}",
    )
    return DeleteResponse(message="Skill deleted successfully", deleted_id=skill_id)


class VectorStoreConfigUpdate(BaseModel):
    engine: str


@router.get("/vector-store/config")
def get_vector_store_config(current_user: dict = Depends(require_admin)):
    cfg = get_config()
    engine = VectorStoreFactory.get_engine(cfg)
    chroma_available = VectorStoreFactory.is_chroma_available()
    return {
        "engine": engine,
        "chroma_available": chroma_available,
        "supported_engines": ["sqlite"] + (["chromadb"] if chroma_available else [])
    }


@router.put("/vector-store/config")
def update_vector_store_config(req: VectorStoreConfigUpdate, current_user: dict = Depends(require_admin)):
    if req.engine not in ['sqlite', 'chromadb']:
        raise HTTPException(status_code=400, detail="Unsupported engine")

    cfg = get_config()
    try:
        VectorStoreFactory.set_engine(cfg, req.engine)

        # We need to trigger the async task for processing pending files
        from app.services.vector_store_migration import trigger_migration
        trigger_migration(cfg)

        return {"status": "success", "engine": req.engine}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/vector-store/cleanup")
def cleanup_old_vector_data(current_user: dict = Depends(require_admin)):
    cfg = get_config()
    from app.services.vector_store_migration import cleanup_old_engine_data
    result = cleanup_old_engine_data(cfg)
    return result

# ============ Statistics ============


@router.get("/stats", response_model=StatsResponse)
def get_stats(current_user: dict = Depends(require_admin)):
    conn = get_conn(get_config())
    cur = conn.cursor()

    cur.execute(
        "SELECT COUNT(*) as cnt, COALESCE(SUM(file_size), 0) as total_size FROM documents WHERE status = 'active'")
    doc_stats = cur.fetchone()

    cur.execute("SELECT COUNT(*) as cnt FROM users")
    user_count = cur.fetchone()[0]

    cur.execute("""
        SELECT COALESCE(category, 'uncategorized') as cat, COUNT(*) as cnt 
        FROM documents WHERE status = 'active' GROUP BY category
    """)
    by_category = {row["cat"]: row["cnt"] for row in cur.fetchall()}

    cur.execute("""
        SELECT u.username, COUNT(d.id) as cnt 
        FROM users u LEFT JOIN documents d ON u.id = d.user_id AND d.status = 'active'
        GROUP BY u.id
    """)
    by_user = {row["username"]: row["cnt"] for row in cur.fetchall()}

    conn.close()

    return StatsResponse(
        total_documents=doc_stats["cnt"],
        total_users=user_count,
        total_size=doc_stats["total_size"],
        documents_by_category=by_category,
        documents_by_user=by_user
    )


@router.get("/llm-config", response_model=LLMConfigResponse)
def get_llm_config(current_user: dict = Depends(require_admin)):
    cfg = _ensure_llm_secret_migrated(get_config())
    llm_cfg, config_source, local_llm_enabled = _effective_llm_config(cfg)
    logger.info(
        "admin_llm_config_read user_id=%s provider=%s api_base=%s model=%s",
        str(current_user.get("id") or ""),
        str(llm_cfg.get("provider", "")),
        str(llm_cfg.get("api_base", "")),
        str(llm_cfg.get("model", "")),
    )
    return LLMConfigResponse(
        provider=str(llm_cfg.get("provider", "")),
        api_base=str(llm_cfg.get("api_base", "")),
        model=str(llm_cfg.get("model", "")),
        temperature=float(llm_cfg.get("temperature", 0.2)),
        max_tokens=int(llm_cfg.get("max_tokens", 2048)),
        timeout=int(llm_cfg.get("timeout", 60)),
        headers=llm_cfg.get("headers") if isinstance(
            llm_cfg.get("headers"), dict) else {},
        has_api_key=bool(llm_cfg.get("api_key")) or has_llm_api_key(cfg),
        config_source=config_source,
        local_llm_enabled=local_llm_enabled,
    )


@router.put("/llm-config", response_model=LLMConfigResponse)
def update_llm_config(payload: LLMConfigUpdate, request: Request, current_user: dict = Depends(require_admin)):
    data = _normalize_llm_payload(payload.model_dump())
    _validate_llm_config(data)
    logger.info(
        "admin_llm_config_update_start user_id=%s provider=%s api_base=%s model=%s timeout=%s max_tokens=%s",
        str(current_user.get("id") or ""),
        str(data.get("provider", "")),
        str(data.get("api_base", "")),
        str(data.get("model", "")),
        str(data.get("timeout", "")),
        str(data.get("max_tokens", "")),
    )
    cfg_now = get_config()
    plain_api_key = _clean_text(data.get("api_key", ""))
    if data.get("provider") in {"ollama", "llama_cpp"}:
        data["api_key"] = ""
        plain_api_key = ""
    if plain_api_key and not set_llm_api_key(cfg_now, plain_api_key):
        raise HTTPException(
            status_code=500, detail="failed to save api_key in secure store")
    data["api_key"] = ""
    cfg = update_config_patch(_sync_llm_patch(cfg_now, data))
    if hasattr(request.app.state, "llm"):
        request.app.state.llm.cfg = cfg
    logger.info(
        "admin_llm_config_update_done user_id=%s provider=%s api_base=%s model=%s",
        str(current_user.get("id") or ""),
        str(data.get("provider", "")),
        str(data.get("api_base", "")),
        str(data.get("model", "")),
    )
    llm_cfg, config_source, local_llm_enabled = _effective_llm_config(cfg)
    return LLMConfigResponse(
        provider=str(llm_cfg.get("provider", "")),
        api_base=str(llm_cfg.get("api_base", "")),
        model=str(llm_cfg.get("model", "")),
        temperature=float(llm_cfg.get("temperature", 0.2)),
        max_tokens=int(llm_cfg.get("max_tokens", 2048)),
        timeout=int(llm_cfg.get("timeout", 60)),
        headers=llm_cfg.get("headers") if isinstance(
            llm_cfg.get("headers"), dict) else {},
        has_api_key=has_llm_api_key(cfg),
        config_source=config_source,
        local_llm_enabled=local_llm_enabled,
    )


@router.get("/ollama/models", response_model=OllamaModelListResponse)
def get_ollama_models(
    force_refresh: bool = Query(False),
    current_user: dict = Depends(require_admin),
):
    cfg = get_config()
    effective_cfg, _source, _enabled = _effective_llm_config(cfg)
    current_model = _clean_text(effective_cfg.get("model"))
    provider = _clean_text(effective_cfg.get("provider")).lower()
    configured_host = ""
    if provider == "ollama":
        configured_host = _normalize_ollama_host(
            _clean_text(effective_cfg.get("api_base")) or DEFAULT_OLLAMA_HOST
        )
    host = configured_host if configured_host else DEFAULT_OLLAMA_HOST
    models, cached, stale, error, cache_ts = _get_cached_ollama_models(
        host, force_refresh=bool(force_refresh)
    )
    recommended_model = _choose_recommended_ollama_model(models)
    if current_model and not any(_clean_text(item.get("name")) == current_model for item in models):
        logger.warning(
            "admin_ollama_current_model_missing user_id=%s current_model=%s recommended_model=%s host=%s",
            str(current_user.get("id") or ""),
            current_model,
            recommended_model,
            host,
        )
    logger.info(
        "admin_ollama_models user_id=%s host=%s cached=%s stale=%s model_count=%s error=%s",
        str(current_user.get("id") or ""),
        host,
        cached,
        stale,
        len(models),
        error,
    )
    expires_at = cache_ts + OLLAMA_CACHE_TTL_SEC if cache_ts else 0.0
    return OllamaModelListResponse(
        ok=bool(models) and not error,
        host=host,
        reachable=not bool(error),
        cached=bool(cached),
        stale=bool(stale),
        cache_ttl_sec=OLLAMA_CACHE_TTL_SEC,
        cached_at=_iso_from_ts(cache_ts),
        expires_at=_iso_from_ts(expires_at),
        current_model=current_model,
        recommended_model=recommended_model,
        models=[OllamaModelItem(**item) for item in models],
        error=error,
    )


@router.get("/llama-cpp/models", response_model=LlamaCppModelListResponse)
def get_llamacpp_models(
    force_refresh: bool = Query(False),
    current_user: dict = Depends(require_admin),
):
    cfg = get_config()
    effective_cfg, _source, _enabled = _effective_llm_config(cfg)
    current_model = _clean_text(effective_cfg.get("model"))
    provider = _clean_text(effective_cfg.get("provider")).lower()
    configured_host = ""
    if provider == "llama_cpp":
        configured_host = _normalize_openai_host(
            _clean_text(effective_cfg.get("api_base")) or DEFAULT_LLAMACPP_HOST
        )
    host = configured_host if configured_host else DEFAULT_LLAMACPP_HOST
    models, cached, stale, error, cache_ts = _get_cached_llamacpp_models(
        host, force_refresh=bool(force_refresh)
    )
    recommended_model = current_model or (models[0]["name"] if models else "")
    logger.info(
        "admin_llamacpp_models user_id=%s host=%s cached=%s stale=%s model_count=%s error=%s",
        str(current_user.get("id") or ""),
        host,
        cached,
        stale,
        len(models),
        error,
    )
    expires_at = cache_ts + LLAMACPP_CACHE_TTL_SEC if cache_ts else 0.0
    return LlamaCppModelListResponse(
        ok=bool(models) and not error,
        host=host,
        reachable=not bool(error),
        cached=bool(cached),
        stale=bool(stale),
        cache_ttl_sec=LLAMACPP_CACHE_TTL_SEC,
        cached_at=_iso_from_ts(cache_ts),
        expires_at=_iso_from_ts(expires_at),
        current_model=current_model,
        recommended_model=recommended_model,
        models=[LlamaCppModelItem(**item) for item in models],
        error=error,
    )


@router.delete("/llm-config/api-key")
def clear_llm_api_key(request: Request, current_user: dict = Depends(require_admin)):
    cfg = get_config()
    if not delete_llm_api_key(cfg):
        raise HTTPException(status_code=500, detail="failed to clear api_key")
    llm_cfg = cfg.get("llm_config") if isinstance(
        cfg.get("llm_config"), dict) else {}
    if llm_cfg.get("api_key"):
        next_llm = dict(llm_cfg)
        next_llm["api_key"] = ""
        cfg = update_config_patch({"llm_config": next_llm})
    if hasattr(request.app.state, "llm"):
        request.app.state.llm.cfg = cfg
    return {"message": "api_key cleared"}


@router.get("/ui-config", response_model=UIConfigResponse)
def get_ui_config(current_user: dict = Depends(require_admin)):
    cfg = get_config()
    ui_cfg = _get_ui_config(cfg)
    return UIConfigResponse(**ui_cfg)


@router.put("/ui-config", response_model=UIConfigResponse)
def update_ui_config(payload: UIConfigUpdate, current_user: dict = Depends(require_admin)):
    cfg_current = get_config()
    current_ui = cfg_current.get("ui_config") if isinstance(
        cfg_current.get("ui_config"), dict) else {}
    next_theme = _normalize_theme(
        payload.default_theme if payload.default_theme is not None else current_ui.get("default_theme"))
    data = {
        "ui_config": {
            "show_citation_source": bool(payload.show_citation_source),
            "default_theme": next_theme,
        }
    }
    cfg = update_config_patch(data)
    ui_cfg = _get_ui_config(cfg)
    return UIConfigResponse(**ui_cfg)


@router.get("/memory-config", response_model=MemoryRuntimeConfigResponse)
def get_memory_runtime_config(current_user: dict = Depends(require_admin)):
    cfg = get_config()
    mem_cfg = _get_memory_runtime_config(cfg)
    return MemoryRuntimeConfigResponse(**mem_cfg)


@router.put("/memory-config", response_model=MemoryRuntimeConfigResponse)
def update_memory_runtime_config(payload: MemoryRuntimeConfigUpdate, current_user: dict = Depends(require_admin)):
    cfg_current = get_config()
    current = _get_memory_runtime_config(cfg_current)
    patch = payload.model_dump(exclude_unset=True)
    merged = dict(current)
    for k, v in patch.items():
        merged[k] = v
    mode = str(merged.get("memory_mode_when_disabled")
               or "classic").strip().lower()
    if mode not in {"classic"}:
        raise HTTPException(
            status_code=400, detail="memory_mode_when_disabled must be 'classic'")
    temp_mode = str(merged.get("memory_temporary_disable_fallback_mode")
                    or "classic").strip().lower()
    if temp_mode not in {"classic"}:
        raise HTTPException(
            status_code=400, detail="memory_temporary_disable_fallback_mode must be 'classic'")
    max_calls = int(merged.get("memory_max_llm_calls_per_audit") or 12)
    max_prompt_chars = int(merged.get(
        "memory_max_prompt_chars_per_clause") or 2400)
    if max_calls <= 0:
        raise HTTPException(
            status_code=400, detail="memory_max_llm_calls_per_audit must be positive")
    if max_prompt_chars < 300:
        raise HTTPException(
            status_code=400, detail="memory_max_prompt_chars_per_clause must be >= 300")
    save_payload = {
        "memory_module_enabled": bool(merged.get("memory_module_enabled", True)),
        "memory_mode_when_disabled": mode,
        "memory_disable_fallback_on_error": bool(merged.get("memory_disable_fallback_on_error", True)),
        "memory_token_guard_enabled": bool(merged.get("memory_token_guard_enabled", True)),
        "memory_max_llm_calls_per_audit": max_calls,
        "memory_max_prompt_chars_per_clause": max_prompt_chars,
    }
    temp_disable_payload = {
        "enabled": bool(merged.get("memory_temporary_disable_enabled", False)),
        "fallback_mode": temp_mode,
        "reason": str(merged.get("memory_temporary_disable_reason") or "edge_llm_context_limit").strip() or "edge_llm_context_limit",
        "trigger_source": str(merged.get("memory_temporary_disable_trigger_source") or "config.memory_temporary_disable").strip() or "config.memory_temporary_disable",
    }
    cfg = update_config_patch({
        "memory_runtime_config": save_payload,
        "memory_temporary_disable": temp_disable_payload,
    })
    out = _get_memory_runtime_config(cfg)
    return MemoryRuntimeConfigResponse(**out)


@router.get("/token-usage", response_model=TokenUsageResponse)
def get_token_usage(
    start: Optional[str] = None,
    end: Optional[str] = None,
    granularity: Literal["hour", "day", "week"] = "day",
    rank_by: Literal["file_path", "stage", "model"] = "file_path",
    top_n: int = Query(10, ge=1, le=50),
    max_rows: int = Query(50000, ge=1000, le=200000),
    alert_total_tokens: int = Query(12000, ge=1000, le=200000),
    alert_bucket_tokens: int = Query(80000, ge=1000, le=1000000),
    current_user: dict = Depends(require_admin)
):
    cfg = get_config()
    trace_dir = _llm_trace_dir(cfg)
    end_dt = _parse_dt(end, datetime.utcnow())
    start_dt = _parse_dt(start, end_dt - timedelta(days=7))
    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt
    if (end_dt - start_dt).days > 31:
        start_dt = end_dt - timedelta(days=31)
    series_map = {}
    ranking_map = {}
    totals = {"input_tokens": 0, "output_tokens": 0,
              "total_tokens": 0, "request_count": 0}
    alerts = []
    for row in _iter_trace_rows(trace_dir, start_dt, end_dt, max_rows):
        usage = row.get("usage") if isinstance(row.get("usage"), dict) else {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or (
            prompt_tokens + completion_tokens))
        if total_tokens <= 0:
            continue
        totals["input_tokens"] += prompt_tokens
        totals["output_tokens"] += completion_tokens
        totals["total_tokens"] += total_tokens
        totals["request_count"] += 1
        ts = _parse_dt(row.get("ts"))
        bucket = _bucket_key(ts, granularity)
        agg = series_map.setdefault(bucket, {
                                    "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "request_count": 0})
        agg["input_tokens"] += prompt_tokens
        agg["output_tokens"] += completion_tokens
        agg["total_tokens"] += total_tokens
        agg["request_count"] += 1
        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        if rank_by == "stage":
            key = str(meta.get("stage") or "unknown")
        elif rank_by == "model":
            key = str(row.get("model") or "unknown")
        else:
            key = str(meta.get("file_path") or meta.get(
                "document_id") or "unknown")
        r = ranking_map.setdefault(
            key, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "request_count": 0})
        r["input_tokens"] += prompt_tokens
        r["output_tokens"] += completion_tokens
        r["total_tokens"] += total_tokens
        r["request_count"] += 1
        if total_tokens >= alert_total_tokens:
            alerts.append({
                "level": "warning",
                "reason": "single_request_tokens_high",
                "value": total_tokens,
                "threshold": alert_total_tokens,
                "bucket": bucket,
                "meta": {"model": row.get("model"), "stage": meta.get("stage"), "file_path": meta.get("file_path")}
            })
    series = [
        TokenUsageSeriesItem(
            bucket=b,
            input_tokens=v["input_tokens"],
            output_tokens=v["output_tokens"],
            total_tokens=v["total_tokens"],
            request_count=v["request_count"]
        )
        for b, v in sorted(series_map.items(), key=lambda x: x[0])
    ]
    for item in series:
        if item.total_tokens >= alert_bucket_tokens:
            alerts.append({
                "level": "critical",
                "reason": "bucket_tokens_high",
                "value": item.total_tokens,
                "threshold": alert_bucket_tokens,
                "bucket": item.bucket
            })
    rankings = [
        TokenUsageRankingItem(
            key=k,
            input_tokens=v["input_tokens"],
            output_tokens=v["output_tokens"],
            total_tokens=v["total_tokens"],
            request_count=v["request_count"]
        )
        for k, v in sorted(ranking_map.items(), key=lambda x: x[1]["total_tokens"], reverse=True)[:top_n]
    ]
    return TokenUsageResponse(
        range_start=start_dt.isoformat(),
        range_end=end_dt.isoformat(),
        granularity=granularity,
        rank_by=rank_by,
        totals=TokenUsageTotals(**totals),
        series=series,
        rankings=rankings,
        alerts=[TokenUsageAlert(**a) for a in alerts][:100],
        last_updated=datetime.utcnow().isoformat()
    )


@router.get("/token-usage/csv")
def export_token_usage_csv(
    start: Optional[str] = None,
    end: Optional[str] = None,
    granularity: Literal["hour", "day", "week"] = "day",
    max_rows: int = Query(50000, ge=1000, le=200000),
    current_user: dict = Depends(require_admin)
):
    cfg = get_config()
    trace_dir = _llm_trace_dir(cfg)
    end_dt = _parse_dt(end, datetime.utcnow())
    start_dt = _parse_dt(start, end_dt - timedelta(days=7))
    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt
    if (end_dt - start_dt).days > 31:
        start_dt = end_dt - timedelta(days=31)
    series_map = {}
    for row in _iter_trace_rows(trace_dir, start_dt, end_dt, max_rows):
        usage = row.get("usage") if isinstance(row.get("usage"), dict) else {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or (
            prompt_tokens + completion_tokens))
        if total_tokens <= 0:
            continue
        ts = _parse_dt(row.get("ts"))
        bucket = _bucket_key(ts, granularity)
        agg = series_map.setdefault(bucket, {
                                    "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "request_count": 0})
        agg["input_tokens"] += prompt_tokens
        agg["output_tokens"] += completion_tokens
        agg["total_tokens"] += total_tokens
        agg["request_count"] += 1
    output = []
    output.append(["bucket", "input_tokens", "output_tokens",
                  "total_tokens", "request_count"])
    for b, v in sorted(series_map.items(), key=lambda x: x[0]):
        output.append([b, v["input_tokens"], v["output_tokens"],
                      v["total_tokens"], v["request_count"]])
    csv_buf = io.StringIO()
    writer = csv.writer(csv_buf)
    for row in output:
        writer.writerow(row)
    content = csv_buf.getvalue()
    headers = {"Content-Disposition": "attachment; filename=token_usage.csv"}
    return Response(content=content, media_type="text/csv", headers=headers)


@router.post("/memory/promotion/run")
def run_memory_promotion(
    payload: Optional[MemoryPromotionRunRequest] = None,
    current_user: dict = Depends(require_admin),
):
    cfg = get_config()
    req = payload or MemoryPromotionRunRequest()
    result = promote_episode_to_rule(
        cfg=cfg,
        episode_id=str(req.episode_id or ""),
        min_support_count=req.min_support_count,
        min_quality_score=req.min_quality_score,
        high_impact_threshold=req.high_impact_threshold,
    )
    if not bool(result.get("ok", False)):
        raise HTTPException(status_code=400, detail=str(
            result.get("reason") or "promotion_failed"))
    return result


@router.get("/memory/rules/pending")
def get_pending_memory_rules(
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(require_admin),
):
    cfg = get_config()
    items = list_pending_rule_memories(cfg=cfg, limit=limit)
    return {"items": items, "total": len(items)}


@router.post("/memory/rules/{rule_id}/review")
def review_pending_memory_rule(
    rule_id: str,
    payload: MemoryRuleReviewRequest,
    current_user: dict = Depends(require_admin),
):
    cfg = get_config()
    result = review_rule_memory(
        cfg=cfg,
        rule_id=rule_id,
        action=payload.action,
        reviewer_id=str(current_user.get("id") or ""),
        note=str(payload.note or ""),
    )
    if not bool(result.get("ok", False)):
        reason = str(result.get("reason") or "review_failed")
        status = 404 if reason == "not_found" else 400
        raise HTTPException(status_code=status, detail=reason)
    return result


@router.post("/llm-test", response_model=LLMTestResponse)
def test_llm(
    payload: Optional[LLMTestRequest] = None,
    current_user: dict = Depends(require_admin),
    llm=Depends(get_app_llm),
):
    prompt = ""
    if payload:
        prompt = str(payload.prompt or "").strip()
    if not prompt:
        prompt = "你是什么模型？"
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": prompt}
    ]
    cfg = getattr(llm, "cfg", {}) if hasattr(llm, "cfg") else {}
    llm_cfg, _config_source, _local_enabled = _effective_llm_config(cfg)
    logger.info(
        "admin_llm_test_start user_id=%s provider=%s api_base=%s model=%s prompt_len=%s",
        str(current_user.get("id") or ""),
        str(llm_cfg.get("provider", "")),
        str(llm_cfg.get("api_base", "")),
        str(llm_cfg.get("model", "")),
        len(prompt),
    )
    try:
        answer, _ = llm.chat(messages)
    except Exception as e:
        raw_err = str(e)
        user_err = _friendly_llm_test_error(llm_cfg, raw_err)
        logger.exception(
            "admin_llm_test_failed user_id=%s provider=%s api_base=%s model=%s err=%s",
            str(current_user.get("id") or ""),
            str(llm_cfg.get("provider", "")),
            str(llm_cfg.get("api_base", "")),
            str(llm_cfg.get("model", "")),
            str(e),
        )
        raise HTTPException(
            status_code=400, detail=f"llm test failed: {user_err}")
    logger.info(
        "admin_llm_test_done user_id=%s provider=%s api_base=%s model=%s answer_len=%s",
        str(current_user.get("id") or ""),
        str(llm_cfg.get("provider", "")),
        str(llm_cfg.get("api_base", "")),
        str(llm_cfg.get("model", "")),
        len(str(answer or "")),
    )
    return LLMTestResponse(ok=True, prompt=prompt, answer=answer)


# ==================== LLM Trace 全链路日志接口 ====================

class LlmTraceQuery(BaseModel):
    model_name: str = ""
    status: str = ""
    audit_id: str = ""
    date_from: str = ""
    date_to: str = ""
    stage: str = ""
    page: int = 1
    page_size: int = 20


class LlmTraceItem(BaseModel):
    id: int
    trace_id: str
    span_id: str
    parent_span_id: str = ""
    audit_id: str = ""
    stage: str
    task_profile: str = "default"
    model_role: str = "main"
    provider: str = ""
    api_base: str = ""
    model_name: str
    request_received_at: str = ""
    request_input_tokens_est: int = 0
    request_temperature: float = 0
    request_max_tokens: int = 0
    request_timeout: int = 0
    thinking_started_at: str = ""
    thinking_duration_ms: int = 0
    response_generated_at: str = ""
    response_content_length: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    total_latency_ms: int = 0
    retry_index: int = 0
    is_retry: bool = False
    retry_strategy: str = ""
    status: str
    error_type: str = ""
    error_message: str = ""
    ollama_load_duration_ms: int = 0
    ollama_prompt_eval_ms: int = 0
    ollama_eval_duration_ms: int = 0
    ollama_total_duration_ms: int = 0
    created_at: str = ""


class LlmTraceListResponse(BaseModel):
    items: List[LlmTraceItem]
    total: int
    page: int
    page_size: int


class LlmTraceDetailResponse(BaseModel):
    trace: dict
    full_request_messages: list
    full_response_content: str = ""
    full_thinking_content: str = ""
    jsonl_events: list


class LlmTraceStatsResponse(BaseModel):
    by_model: list
    by_stage: list
    daily: list


@router.get("/llm-traces", response_model=LlmTraceListResponse)
def list_llm_traces(
    model_name: str = Query(""),
    status: str = Query(""),
    audit_id: str = Query(""),
    date_from: str = Query(""),
    date_to: str = Query(""),
    stage: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    current_user: dict = Depends(require_admin),
):
    cfg = get_config()
    collector = get_trace_collector(cfg)
    result = collector.query_traces(
        model_name=model_name,
        status=status,
        audit_id=audit_id,
        date_from=date_from,
        date_to=date_to,
        stage=stage,
        page=page,
        page_size=page_size,
    )
    return result


@router.get("/llm-traces/{span_id}", response_model=LlmTraceDetailResponse)
def get_llm_trace_detail(
    span_id: str,
    current_user: dict = Depends(require_admin),
):
    cfg = get_config()
    collector = get_trace_collector(cfg)
    detail = collector.get_trace_detail(span_id)
    if not detail:
        raise HTTPException(status_code=404, detail="trace span not found")
    return {
        "trace": detail,
        "full_request_messages": detail.get("full_request_messages", []),
        "full_response_content": detail.get("full_response_content", ""),
        "full_thinking_content": detail.get("full_thinking_content", ""),
        "jsonl_events": detail.get("jsonl_events", []),
    }


@router.get("/llm-traces/stats/summary", response_model=LlmTraceStatsResponse)
def get_llm_trace_stats(
    date_from: str = Query(""),
    date_to: str = Query(""),
    current_user: dict = Depends(require_admin),
):
    cfg = get_config()
    collector = get_trace_collector(cfg)
    return collector.get_stats(date_from=date_from, date_to=date_to)


@router.delete("/llm-traces")
def cleanup_llm_traces(
    before_date: str = Query(..., description="删除此日期之前的日志，格式 YYYY-MM-DD"),
    current_user: dict = Depends(require_admin),
):
    cfg = get_config()
    collector = get_trace_collector(cfg)
    deleted = collector.delete_old_traces(before_date)
    return {"message": f"deleted {deleted} trace records before {before_date}", "deleted": deleted}
