import os
import uuid
import json
import csv
import io
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Literal
from fastapi import APIRouter, HTTPException, Depends, Query, Request, Response
from pydantic import BaseModel
from jinja2 import Environment, StrictUndefined, TemplateError, TemplateSyntaxError
from app.core.auth import get_all_users, update_user_role, log_audit
from app.api.dependencies import require_admin, get_app_llm
from app.core.database import get_conn
from app.core.config import get_config, update_config_patch
from app.vector_store.factory import VectorStoreFactory
from app.services.memory_promotion import (
    list_pending_rule_memories,
    promote_episode_to_rule,
    review_rule_memory,
)
from app.services.prompt_binding import (
    build_prompt_storage_payload,
    clear_prompt_bindings_for_prompt,
    get_default_prompt_definition,
    render_prompt_messages,
    get_prompt_binding,
    get_prompt_binding_scene,
    get_default_scene_prompt,
    get_prompt_editor_content,
    get_prompt_record,
    list_prompt_binding_scenes,
    list_prompt_bindings,
    map_prompt_bindings_by_prompt_id,
    normalize_prompt_editor_lang,
    restore_default_prompt,
    set_prompt_binding,
    sync_default_prompts,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])
logger = logging.getLogger("law_assistant")


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


class PromptSummaryResponse(BaseModel):
    id: str
    prompt_key: str
    name: str
    description: str
    status: Literal["enabled", "disabled"]
    created_by: Optional[str] = None
    created_at: str
    updated_at: str
    binding_scene_keys: List[str] = []
    source: Literal["system", "custom"] = "custom"
    readonly: bool = False
    source_module: Optional[str] = None
    managed_scope: Literal["system", "custom"] = "custom"


class PromptDetailResponse(PromptSummaryResponse):
    editor_lang: Literal["zh", "en"] = "zh"
    display_system_text: str = ""
    display_focus_text: str = ""
    display_template_text: str = ""
    system_text: str
    template_text: str
    sample_input_json: str


class PromptListResponse(BaseModel):
    items: List[PromptSummaryResponse]
    total: int
    page: int
    page_size: int


class PromptUpsertRequest(BaseModel):
    name: str
    description: Optional[str] = ""
    editor_lang: Optional[str] = "zh"
    display_system_text: Optional[str] = ""
    display_focus_text: Optional[str] = ""
    display_template_text: Optional[str] = ""
    system_text: Optional[str] = ""
    template_text: Optional[str] = ""
    sample_input_json: Optional[str] = "{}"
    prompt_key: Optional[str] = ""


class PromptStatusUpdateRequest(BaseModel):
    enabled: bool


class PromptPreviewRequest(BaseModel):
    prompt_id: Optional[str] = ""
    prompt_key: Optional[str] = ""
    editor_lang: Optional[str] = "zh"
    display_system_text: Optional[str] = ""
    display_focus_text: Optional[str] = ""
    display_template_text: Optional[str] = ""
    system_text: Optional[str] = ""
    template_text: Optional[str] = ""
    sample_input_json: Optional[str] = "{}"


class PromptPreviewResponse(BaseModel):
    rendered_system_text: str
    rendered_text: str
    normalized_context: dict


class PromptBindingPromptSummary(BaseModel):
    id: str
    name: str
    description: str
    status: Literal["enabled", "disabled"]
    updated_at: str


class PromptBindingSceneResponse(BaseModel):
    scene_key: str
    label: str
    description: str
    source_module: str
    active_source: Literal["bound_prompt", "system_default"]
    updated_by: Optional[str] = None
    updated_at: Optional[str] = None
    active_prompt: Optional[PromptBindingPromptSummary] = None


class PromptBindingListResponse(BaseModel):
    items: List[PromptBindingSceneResponse]


class PromptBindingUpdateRequest(BaseModel):
    prompt_id: Optional[str] = None


# ============ Stats Models ============
class StatsResponse(BaseModel):
    total_documents: int
    total_users: int
    total_size: int
    documents_by_category: dict
    documents_by_user: dict


class LLMConfigResponse(BaseModel):
    provider: str
    provider_label: str
    api_base: str
    model: str
    temperature: float
    max_tokens: int
    timeout: int
    headers: dict
    env_managed: bool
    supported_env_vars: List[str]
    required_env_vars: List[str]
    missing_env_vars: List[str]
    env_sources: dict
    security_issues: List[str]
    route: dict


class LLMConfigUpdate(BaseModel):
    provider: str
    api_base: str
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
    risk_notice: str


class MemoryRuntimeConfigUpdate(BaseModel):
    memory_module_enabled: Optional[bool] = None
    memory_mode_when_disabled: Optional[str] = None
    memory_disable_fallback_on_error: Optional[bool] = None
    memory_token_guard_enabled: Optional[bool] = None
    memory_max_llm_calls_per_audit: Optional[int] = None
    memory_max_prompt_chars_per_clause: Optional[int] = None


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


def _normalize_llm_payload(payload: dict) -> dict:
    data = dict(payload or {})
    data["api_base"] = _clean_text(data.get("api_base", ""))
    data["model"] = _clean_text(data.get("model", ""))
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
    mode = str(raw.get("memory_mode_when_disabled")
               or "classic").strip().lower()
    if mode not in {"classic"}:
        mode = "classic"
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
        "risk_notice": "Enabling memory mode may increase LLM calls and token cost; disable it for lower-cost audits with potentially reduced recall quality.",
    }


def _clean_optional_text(value: Optional[str]) -> str:
    return str(value or "").strip()


def _normalize_prompt_status(enabled: bool) -> str:
    return "enabled" if enabled else "disabled"


def _validate_prompt_name(name: Optional[str]) -> str:
    clean_name = _clean_optional_text(name)
    if not clean_name:
        raise HTTPException(status_code=400, detail="prompt name is required")
    if len(clean_name) > 120:
        raise HTTPException(
            status_code=400, detail="prompt name must be <= 120 characters")
    return clean_name


def _validate_prompt_template(template_text: Optional[str]) -> str:
    clean_template = str(template_text or "").strip()
    if not clean_template:
        raise HTTPException(
            status_code=400, detail="template_text is required")
    if len(clean_template) > 50000:
        raise HTTPException(
            status_code=400, detail="template_text must be <= 50000 characters")
    return clean_template


def _validate_prompt_system_text(system_text: Optional[str]) -> str:
    clean_system = str(system_text or "").strip()
    if len(clean_system) > 20000:
        raise HTTPException(
            status_code=400, detail="system_text must be <= 20000 characters")
    return clean_system


def _validate_prompt_key(prompt_key: Optional[str]) -> str:
    clean_key = str(prompt_key or "").strip()
    if not clean_key:
        return ""
    if len(clean_key) > 160:
        raise HTTPException(
            status_code=400, detail="prompt_key must be <= 160 characters")
    for ch in clean_key:
        if not (ch.isalnum() or ch in {"_", "-", ".", ":"}):
            raise HTTPException(
                status_code=400, detail="prompt_key contains invalid characters")
    return clean_key


def _normalize_prompt_sample_input(sample_input_json: Optional[str]) -> tuple[str, dict]:
    raw = str(sample_input_json or "").strip() or "{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"sample_input_json must be valid JSON: {exc.msg}",
        ) from exc
    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=400, detail="sample_input_json must be a JSON object")
    normalized = json.dumps(parsed, ensure_ascii=False,
                            indent=2, sort_keys=True)
    return normalized, parsed


def _normalize_prompt_payload(payload: PromptUpsertRequest) -> tuple[dict, dict]:
    sample_input_json, context = _normalize_prompt_sample_input(
        payload.sample_input_json)
    editor_lang = normalize_prompt_editor_lang(payload.editor_lang)
    display_system_text = _validate_prompt_system_text(
        payload.display_system_text if payload.display_system_text is not None else payload.system_text
    )
    display_focus_text = _validate_prompt_system_text(
        payload.display_focus_text)
    display_template_text = _validate_prompt_template(
        payload.display_template_text if payload.display_template_text is not None and str(payload.display_template_text).strip()
        else payload.template_text
    )
    data = {
        "name": _validate_prompt_name(payload.name),
        "description": _clean_optional_text(payload.description),
        "prompt_key": _validate_prompt_key(payload.prompt_key),
        "editor_lang": editor_lang,
        "display_system_text": display_system_text,
        "display_focus_text": display_focus_text,
        "display_template_text": display_template_text,
        "sample_input_json": sample_input_json,
    }
    return data, context


def _render_prompt_preview(system_text: str, template_text: str, context: dict) -> tuple[str, str]:
    env = Environment(undefined=StrictUndefined, autoescape=False)
    try:
        rendered_system = ""
        if str(system_text or "").strip():
            rendered_system = env.from_string(system_text).render(**context)
        template = env.from_string(template_text)
        return rendered_system, template.render(**context)
    except TemplateSyntaxError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"template syntax error at line {exc.lineno}: {exc.message}",
        ) from exc
    except TemplateError as exc:
        raise HTTPException(
            status_code=400, detail=f"template render failed: {str(exc)}") from exc


def _build_prompt_preview_context(context: dict, editor_lang: str) -> dict:
    preview_context = dict(context or {})
    locale = normalize_prompt_editor_lang(editor_lang)
    # Admin preview should always reflect the language currently being edited.
    preview_context["lang"] = locale
    preview_context["norm_lang"] = locale
    preview_context.setdefault("tax_focus", False)
    return preview_context


def _prompt_row_to_summary(row, binding_map: Optional[dict] = None) -> PromptSummaryResponse:
    prompt_id = str(row["id"] or "")
    managed_scope = str(row["managed_scope"] or "custom")
    return PromptSummaryResponse(
        id=prompt_id,
        prompt_key=str(row["prompt_key"] or ""),
        name=row["name"],
        description=str(row["description"] or ""),
        status=row["status"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        binding_scene_keys=sorted(set((binding_map or {}).get(prompt_id, []))),
        source="system" if managed_scope == "system" else "custom",
        readonly=False,
        source_module=str(row["source_module"] or "") or None,
        managed_scope="system" if managed_scope == "system" else "custom",
    )


def _prompt_row_to_detail(row, binding_map: Optional[dict] = None, editor_lang: str = "zh") -> PromptDetailResponse:
    prompt_id = str(row["id"] or "")
    managed_scope = str(row["managed_scope"] or "custom")
    editor_content = get_prompt_editor_content(row, editor_lang)
    return PromptDetailResponse(
        id=prompt_id,
        prompt_key=str(row["prompt_key"] or ""),
        name=row["name"],
        description=str(row["description"] or ""),
        editor_lang=str(editor_content.get("editor_lang") or "zh"),
        display_system_text=str(
            editor_content.get("display_system_text") or ""),
        display_focus_text=str(editor_content.get("display_focus_text") or ""),
        display_template_text=str(
            editor_content.get("display_template_text") or ""),
        system_text=str(row["system_text"] or ""),
        template_text=row["template_text"],
        sample_input_json=str(row["sample_input_json"] or "{}"),
        status=row["status"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        binding_scene_keys=sorted(set((binding_map or {}).get(prompt_id, []))),
        source="system" if managed_scope == "system" else "custom",
        readonly=False,
        source_module=str(row["source_module"] or "") or None,
        managed_scope="system" if managed_scope == "system" else "custom",
    )


def _build_prompt_binding_response(scene_key: str, scene_meta: dict, binding_row: Optional[dict]) -> PromptBindingSceneResponse:
    active_prompt = None
    active_source = "system_default"
    updated_by = None
    updated_at = None
    if binding_row:
        updated_by = str(binding_row.get("updated_by") or "") or None
        updated_at = str(binding_row.get("updated_at") or "") or None
        prompt_id = str(binding_row.get("prompt_id") or "").strip()
        prompt_status = str(binding_row.get("status") or "").strip()
        prompt_name = str(binding_row.get("name") or "").strip()
        if prompt_id and prompt_name and prompt_status == "enabled":
            active_source = "bound_prompt"
            active_prompt = PromptBindingPromptSummary(
                id=prompt_id,
                name=prompt_name,
                description=str(binding_row.get("description") or ""),
                status=prompt_status,
                updated_at=str(binding_row.get("prompt_updated_at")
                               or binding_row.get("updated_at") or ""),
            )
    if not active_prompt:
        default_prompt = get_default_scene_prompt(scene_key)
        if default_prompt:
            active_prompt = PromptBindingPromptSummary(
                id=str(default_prompt.get("id") or ""),
                name=str(default_prompt.get("name") or ""),
                description=str(default_prompt.get("description") or ""),
                status="enabled",
                updated_at="",
            )
    return PromptBindingSceneResponse(
        scene_key=scene_key,
        label=str(scene_meta.get("label") or scene_key),
        description=str(scene_meta.get("description") or ""),
        source_module=str(scene_meta.get("source_module") or ""),
        active_source=active_source,
        updated_by=updated_by,
        updated_at=updated_at,
        active_prompt=active_prompt,
    )


def _default_scene_keys_by_prompt_id(cfg: Optional[dict] = None) -> dict:
    mapping = {}
    runtime_cfg = cfg or get_config()
    for scene_meta in list_prompt_binding_scenes():
        scene_key = str(scene_meta.get("scene_key") or "")
        default_prompt = get_default_scene_prompt(scene_key, runtime_cfg)
        prompt_id = str((default_prompt or {}).get("id") or "").strip()
        if not scene_key or not prompt_id:
            continue
        mapping.setdefault(prompt_id, []).append(scene_key)
    return mapping


def _combined_prompt_binding_map(cfg: Optional[dict] = None) -> dict:
    runtime_cfg = cfg or get_config()
    merged = {}
    for source_map in (
        _default_scene_keys_by_prompt_id(runtime_cfg),
        map_prompt_bindings_by_prompt_id(runtime_cfg),
    ):
        for prompt_id, scene_keys in source_map.items():
            merged.setdefault(prompt_id, [])
            for scene_key in scene_keys:
                if scene_key not in merged[prompt_id]:
                    merged[prompt_id].append(scene_key)
    return merged


def _ensure_prompt_key_unique(cur, prompt_key: str, exclude_prompt_id: Optional[str] = None) -> None:
    clean_key = str(prompt_key or "").strip()
    if not clean_key:
        return
    params = [clean_key.lower()]
    sql = "SELECT id FROM audit_prompt WHERE lower(prompt_key) = ?"
    if exclude_prompt_id:
        sql += " AND id <> ?"
        params.append(str(exclude_prompt_id or "").strip())
    cur.execute(sql, tuple(params))
    if cur.fetchone():
        raise HTTPException(
            status_code=409, detail="Prompt key already exists")


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


@router.get("/prompts", response_model=PromptListResponse)
def list_prompts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    search: Optional[str] = None,
    status: Optional[Literal["enabled", "disabled"]] = None,
    current_user: dict = Depends(require_admin),
):
    cfg = get_config()
    sync_default_prompts(cfg)
    binding_map = _combined_prompt_binding_map(cfg)
    conn = get_conn(cfg)
    cur = conn.cursor()
    where_clauses = ["1 = 1"]
    params = []
    if search:
        where_clauses.append(
            "(name LIKE ? OR description LIKE ? OR prompt_key LIKE ? OR source_module LIKE ?)"
        )
        params.extend([f"%{search}%", f"%{search}%",
                      f"%{search}%", f"%{search}%"])
    if status:
        where_clauses.append("status = ?")
        params.append(status)
    where_sql = " AND ".join(where_clauses)
    cur.execute(
        f"""
        SELECT
            id, prompt_key, name, description, system_text, template_text,
            sample_input_json, source_module, managed_scope, status,
            created_by, created_at, updated_at
        FROM audit_prompt
        WHERE {where_sql}
        ORDER BY
            CASE managed_scope WHEN 'system' THEN 0 ELSE 1 END,
            updated_at DESC,
            created_at DESC
        """,
        params,
    )
    rows = cur.fetchall()
    conn.close()
    items = [_prompt_row_to_summary(row, binding_map) for row in rows]
    total = len(items)
    offset = (page - 1) * page_size
    paged_items = items[offset: offset + page_size]
    return PromptListResponse(
        items=paged_items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/prompts/preview", response_model=PromptPreviewResponse)
def preview_prompt(
    payload: PromptPreviewRequest,
    current_user: dict = Depends(require_admin),
):
    editor_lang = normalize_prompt_editor_lang(payload.editor_lang)
    display_system_text = _validate_prompt_system_text(
        payload.display_system_text if payload.display_system_text is not None else payload.system_text
    )
    display_focus_text = _validate_prompt_system_text(
        payload.display_focus_text)
    display_template_text = _validate_prompt_template(
        payload.display_template_text if payload.display_template_text is not None and str(payload.display_template_text).strip()
        else payload.template_text
    )
    normalized_json, context = _normalize_prompt_sample_input(
        payload.sample_input_json)
    cfg = get_config()
    sync_default_prompts(cfg)
    existing_row = get_prompt_record(payload.prompt_id, cfg) if str(
        payload.prompt_id or "").strip() else None
    default_item = get_default_prompt_definition(payload.prompt_key) if str(
        payload.prompt_key or "").strip() else None
    storage_payload = build_prompt_storage_payload(
        existing_row=existing_row,
        default_item=default_item,
        editor_lang=editor_lang,
        display_system_text=display_system_text,
        display_focus_text=display_focus_text,
        display_template_text=display_template_text,
    )
    preview_context = _build_prompt_preview_context(context, editor_lang)
    rendered_system_text, rendered_text = _render_prompt_preview(
        str(storage_payload.get("system_text") or ""),
        str(storage_payload.get("template_text") or ""),
        preview_context,
    )
    return PromptPreviewResponse(
        rendered_system_text=rendered_system_text,
        rendered_text=rendered_text,
        normalized_context=json.loads(normalized_json),
    )


@router.get("/prompts/{prompt_id}", response_model=PromptDetailResponse)
def get_prompt(
    prompt_id: str,
    editor_lang: Optional[str] = Query("zh"),
    current_user: dict = Depends(require_admin),
):
    cfg = get_config()
    sync_default_prompts(cfg)
    binding_map = _combined_prompt_binding_map(cfg)
    row = get_prompt_record(prompt_id, cfg)
    if not row:
        raise HTTPException(status_code=404, detail="Prompt not found")
    return _prompt_row_to_detail(row, binding_map, normalize_prompt_editor_lang(editor_lang))


@router.post("/prompts", response_model=PromptDetailResponse)
def create_prompt(
    payload: PromptUpsertRequest,
    request: Request,
    current_user: dict = Depends(require_admin),
):
    data, context = _normalize_prompt_payload(payload)
    cfg = get_config()
    sync_default_prompts(cfg)
    default_item = get_default_prompt_definition(
        data["prompt_key"]) if data["prompt_key"] else None
    storage_payload = build_prompt_storage_payload(
        existing_row=None,
        default_item=default_item,
        editor_lang=data["editor_lang"],
        display_system_text=data["display_system_text"],
        display_focus_text=data["display_focus_text"],
        display_template_text=data["display_template_text"],
    )
    preview_context = _build_prompt_preview_context(
        context, data["editor_lang"])
    _render_prompt_preview(
        str(storage_payload.get("system_text") or ""),
        str(storage_payload.get("template_text") or ""),
        preview_context,
    )
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM audit_prompt WHERE lower(name) = lower(?)", (data["name"],))
    existing = cur.fetchone()
    if existing:
        conn.close()
        raise HTTPException(
            status_code=409, detail="Prompt name already exists")
    _ensure_prompt_key_unique(cur, data["prompt_key"])
    now = datetime.utcnow().isoformat()
    prompt_id = str(uuid.uuid4())
    cur.execute(
        """
        INSERT INTO audit_prompt(
            id, name, prompt_key, description, system_text, template_text,
            localized_system_texts, localized_focus_texts, localized_template_texts,
            placeholder_map_json, sample_input_json, source_module, managed_scope, status, created_by, created_at, updated_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            prompt_id,
            data["name"],
            data["prompt_key"] or None,
            data["description"],
            str(storage_payload.get("system_text") or ""),
            str(storage_payload.get("template_text") or ""),
            str(storage_payload.get("localized_system_texts") or "{}"),
            str(storage_payload.get("localized_focus_texts") or "{}"),
            str(storage_payload.get("localized_template_texts") or "{}"),
            str(storage_payload.get("placeholder_map_json") or "{}"),
            data["sample_input_json"],
            "",
            "custom",
            "enabled",
            str(current_user.get("username") or current_user.get("id") or ""),
            now,
            now,
        ),
    )
    conn.commit()
    cur.execute("SELECT * FROM audit_prompt WHERE id = ?", (prompt_id,))
    row = cur.fetchone()
    conn.close()
    binding_map = _combined_prompt_binding_map(cfg)
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    log_audit(
        current_user["id"], "create", "prompt", prompt_id,
        ip_address, user_agent, f"Created prompt: {data['name']}"
    )
    return _prompt_row_to_detail(row, binding_map, data["editor_lang"])


@router.put("/prompts/{prompt_id}", response_model=PromptDetailResponse)
def update_prompt(
    prompt_id: str,
    payload: PromptUpsertRequest,
    request: Request,
    current_user: dict = Depends(require_admin),
):
    data, context = _normalize_prompt_payload(payload)
    cfg = get_config()
    sync_default_prompts(cfg)
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("SELECT * FROM audit_prompt WHERE id = ?", (prompt_id,))
    existing = cur.fetchone()
    if not existing:
        conn.close()
        raise HTTPException(status_code=404, detail="Prompt not found")
    cur.execute(
        "SELECT id FROM audit_prompt WHERE lower(name) = lower(?) AND id <> ?",
        (data["name"], prompt_id),
    )
    duplicate = cur.fetchone()
    if duplicate:
        conn.close()
        raise HTTPException(
            status_code=409, detail="Prompt name already exists")
    managed_scope = str(existing["managed_scope"] or "custom")
    next_prompt_key = str(existing["prompt_key"] or "").strip(
    ) if managed_scope == "system" else data["prompt_key"]
    _ensure_prompt_key_unique(cur, next_prompt_key, prompt_id)
    default_item = get_default_prompt_definition(
        next_prompt_key) if next_prompt_key else None
    storage_payload = build_prompt_storage_payload(
        existing_row=dict(existing),
        default_item=default_item,
        editor_lang=data["editor_lang"],
        display_system_text=data["display_system_text"],
        display_focus_text=data["display_focus_text"],
        display_template_text=data["display_template_text"],
    )
    preview_context = _build_prompt_preview_context(
        context, data["editor_lang"])
    _render_prompt_preview(
        str(storage_payload.get("system_text") or ""),
        str(storage_payload.get("template_text") or ""),
        preview_context,
    )
    now = datetime.utcnow().isoformat()
    cur.execute(
        """
        UPDATE audit_prompt
        SET name = ?, prompt_key = ?, description = ?, system_text = ?,
            template_text = ?, localized_system_texts = ?, localized_focus_texts = ?,
            localized_template_texts = ?, placeholder_map_json = ?, sample_input_json = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            data["name"],
            next_prompt_key or None,
            data["description"],
            str(storage_payload.get("system_text") or ""),
            str(storage_payload.get("template_text") or ""),
            str(storage_payload.get("localized_system_texts") or "{}"),
            str(storage_payload.get("localized_focus_texts") or "{}"),
            str(storage_payload.get("localized_template_texts") or "{}"),
            str(storage_payload.get("placeholder_map_json") or "{}"),
            data["sample_input_json"],
            now,
            prompt_id,
        ),
    )
    conn.commit()
    cur.execute("SELECT * FROM audit_prompt WHERE id = ?", (prompt_id,))
    row = cur.fetchone()
    conn.close()
    binding_map = _combined_prompt_binding_map(cfg)
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    log_audit(
        current_user["id"], "update", "prompt", prompt_id,
        ip_address, user_agent, f"Updated prompt: {data['name']}"
    )
    return _prompt_row_to_detail(row, binding_map, data["editor_lang"])


@router.put("/prompts/{prompt_id}/status", response_model=PromptDetailResponse)
def update_prompt_status(
    prompt_id: str,
    payload: PromptStatusUpdateRequest,
    request: Request,
    current_user: dict = Depends(require_admin),
):
    cfg = get_config()
    sync_default_prompts(cfg)
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("SELECT * FROM audit_prompt WHERE id = ?", (prompt_id,))
    existing = cur.fetchone()
    if not existing:
        conn.close()
        raise HTTPException(status_code=404, detail="Prompt not found")
    status = _normalize_prompt_status(payload.enabled)
    if str(existing["managed_scope"] or "custom") == "system" and status != "enabled":
        conn.close()
        raise HTTPException(
            status_code=400,
            detail="System prompts cannot be disabled. Edit the prompt or restore defaults instead.",
        )
    now = datetime.utcnow().isoformat()
    cur.execute(
        "UPDATE audit_prompt SET status = ?, updated_at = ? WHERE id = ?",
        (status, now, prompt_id),
    )
    conn.commit()
    cur.execute("SELECT * FROM audit_prompt WHERE id = ?", (prompt_id,))
    row = cur.fetchone()
    conn.close()
    if status != "enabled":
        clear_prompt_bindings_for_prompt(prompt_id, cfg)
    binding_map = _combined_prompt_binding_map(cfg)
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    log_audit(
        current_user["id"], "update_status", "prompt", prompt_id,
        ip_address, user_agent, f"Set prompt status to {status}: {existing['name']}"
    )
    return _prompt_row_to_detail(row, binding_map)


@router.delete("/prompts/{prompt_id}", response_model=DeleteResponse)
def delete_prompt(
    prompt_id: str,
    request: Request,
    current_user: dict = Depends(require_admin),
):
    cfg = get_config()
    sync_default_prompts(cfg)
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name, managed_scope FROM audit_prompt WHERE id = ?", (prompt_id,))
    existing = cur.fetchone()
    if not existing:
        conn.close()
        raise HTTPException(status_code=404, detail="Prompt not found")
    if str(existing["managed_scope"] or "custom") == "system":
        conn.close()
        raise HTTPException(
            status_code=400,
            detail="System prompts cannot be deleted. Restore defaults instead.",
        )
    cur.execute("DELETE FROM audit_prompt WHERE id = ?", (prompt_id,))
    conn.commit()
    conn.close()
    clear_prompt_bindings_for_prompt(prompt_id, cfg)
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    log_audit(
        current_user["id"], "delete", "prompt", prompt_id,
        ip_address, user_agent, f"Deleted prompt: {existing['name']}"
    )
    return DeleteResponse(message="Prompt deleted successfully", deleted_id=prompt_id)


@router.post("/prompts/{prompt_id}/restore-default", response_model=PromptDetailResponse)
def restore_prompt_default(
    prompt_id: str,
    request: Request,
    editor_lang: Optional[str] = Query("zh"),
    current_user: dict = Depends(require_admin),
):
    cfg = get_config()
    try:
        restored = restore_default_prompt(
            prompt_id,
            str(current_user.get("username") or current_user.get("id") or ""),
            cfg,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not restored:
        raise HTTPException(status_code=404, detail="Prompt not found")
    binding_map = _combined_prompt_binding_map(cfg)
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    log_audit(
        current_user["id"], "restore_default", "prompt", prompt_id,
        ip_address, user_agent, f"Restored prompt defaults: {prompt_id}"
    )
    return _prompt_row_to_detail(restored, binding_map, normalize_prompt_editor_lang(editor_lang))


@router.get("/prompt-bindings", response_model=PromptBindingListResponse)
def list_prompt_binding_configs(current_user: dict = Depends(require_admin)):
    binding_rows = {
        str(row.get("scene_key") or ""): row
        for row in list_prompt_bindings(get_config())
    }
    items = []
    for scene_meta in list_prompt_binding_scenes():
        scene_key = str(scene_meta.get("scene_key") or "")
        items.append(
            _build_prompt_binding_response(
                scene_key,
                scene_meta,
                binding_rows.get(scene_key),
            )
        )
    return PromptBindingListResponse(items=items)


@router.put("/prompt-bindings/{scene_key}", response_model=PromptBindingSceneResponse)
def update_prompt_binding(
    scene_key: str,
    payload: PromptBindingUpdateRequest,
    request: Request,
    current_user: dict = Depends(require_admin),
):
    scene_meta = get_prompt_binding_scene(scene_key)
    if not scene_meta:
        raise HTTPException(
            status_code=404, detail="Prompt binding scene not found")
    try:
        binding_row = set_prompt_binding(
            scene_key,
            payload.prompt_id,
            str(current_user.get("username") or current_user.get("id") or ""),
            get_config(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    if str(payload.prompt_id or "").strip():
        detail = f"Bound prompt {payload.prompt_id} to scene {scene_key}"
    else:
        detail = f"Cleared prompt binding for scene {scene_key}"
    log_audit(
        current_user["id"], "update_binding", "prompt_binding", scene_key,
        ip_address, user_agent, detail,
    )
    return _build_prompt_binding_response(scene_key, scene_meta, binding_row or None)


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
def get_llm_config(current_user: dict = Depends(require_admin), llm=Depends(get_app_llm)):
    runtime = llm.describe_runtime_config()
    return LLMConfigResponse(**runtime)


@router.put("/llm-config", response_model=LLMConfigResponse)
def update_llm_config(payload: LLMConfigUpdate, request: Request, current_user: dict = Depends(require_admin)):
    raise HTTPException(
        status_code=405,
        detail="LLM config is environment-managed. Update Windows environment variables instead of saving values in the app.",
    )


@router.delete("/llm-config/api-key")
def clear_llm_api_key(request: Request, current_user: dict = Depends(require_admin)):
    raise HTTPException(
        status_code=405,
        detail="API keys are environment-managed. Clear or update the corresponding Windows environment variables instead.",
    )


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
    cfg = update_config_patch({"memory_runtime_config": save_payload})
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
    rendered = render_prompt_messages(
        "admin.llm_test",
        {"prompt": prompt},
        cfg=get_config(),
    )
    messages = [
        {"role": "system", "content": str(
            rendered.get("rendered_system_text") or "")},
        {"role": "user", "content": str(
            rendered.get("rendered_text") or prompt)},
    ]
    try:
        answer, _ = llm.chat(messages)
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"llm test failed: {str(e)}")
    return LLMTestResponse(ok=True, prompt=prompt, answer=answer)
