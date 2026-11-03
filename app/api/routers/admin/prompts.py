"""Prompt CRUD, preview, bindings."""
from __future__ import annotations

import json
import uuid

from app.api.dependencies import require_admin
from app.core.auth import log_audit
from app.core.config import get_config
from app.core.database import get_conn
from app.services.prompt_binding import build_prompt_storage_payload, clear_prompt_bindings_for_prompt, get_default_prompt_definition, get_default_scene_prompt, get_prompt_binding_scene, get_prompt_editor_content, get_prompt_record, list_prompt_binding_scenes, list_prompt_bindings, map_prompt_bindings_by_prompt_id, normalize_prompt_editor_lang, restore_default_prompt, set_prompt_binding, sync_default_prompts
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from jinja2 import Environment, StrictUndefined, TemplateError, TemplateSyntaxError
from typing import Literal, Optional
from app.api.routers.admin._common import (
    _clean_optional_text,
)
from app.api.routers.admin.schemas import (
    DeleteResponse,
    PromptBindingListResponse,
    PromptBindingPromptSummary,
    PromptBindingSceneResponse,
    PromptBindingUpdateRequest,
    PromptDetailResponse,
    PromptListResponse,
    PromptPreviewRequest,
    PromptPreviewResponse,
    PromptStatusUpdateRequest,
    PromptSummaryResponse,
    PromptUpsertRequest,
)

router = APIRouter()


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
