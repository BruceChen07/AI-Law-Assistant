"""Agent profile CRUD and binding validation."""
from __future__ import annotations

import uuid

from app.core.database import get_conn
from typing import Any, Dict, List, Optional
from app.services.audit_capabilities._normalizers import (
    _json_text,
    _normalize_string_list,
    _utc_now_iso,
    _validate_agent_limits,
    _validate_agent_runtime_combination,
)
from app.services.audit_capabilities._row_mappers import (
    _row_to_agent_profile,
)
from app.services.audit_capabilities.rule_packs import (
    list_rule_packs,
)
from app.services.audit_capabilities.skills import (
    ensure_audit_capabilities_seeded,
    list_visible_skills,
)
from app.services.audit_capabilities.templates import (
    get_template_detail,
)


def _validate_agent_bindings(
    cfg: Dict[str, Any],
    owner_id: str,
    scene: str,
    enabled_skill_ids: Optional[List[str]],
    enabled_rule_pack_ids: Optional[List[str]],
) -> None:
    if enabled_skill_ids is not None:
        if not enabled_skill_ids:
            raise ValueError("enabled_skill_ids must not be empty")
        valid_skill_ids = {
            item["id"] for item in list_visible_skills(cfg, user_id=owner_id, scene=scene)
        }
        invalid_skills = [
            item for item in enabled_skill_ids if item not in valid_skill_ids]
        if invalid_skills:
            raise ValueError(f"invalid skill ids: {', '.join(invalid_skills)}")

    if enabled_rule_pack_ids is not None:
        valid_rule_pack_ids = {
            item["id"] for item in list_rule_packs(cfg, user_id=owner_id, scene=scene)
        }
        invalid_rule_packs = [
            item for item in enabled_rule_pack_ids if item not in valid_rule_pack_ids
        ]
        if invalid_rule_packs:
            raise ValueError(
                f"invalid rule pack ids: {', '.join(invalid_rule_packs)}")


def list_agent_profiles(cfg: Dict[str, Any], owner_id: str, scene: str = "") -> List[Dict[str, Any]]:
    ensure_audit_capabilities_seeded(cfg)
    conn = get_conn(cfg)
    cur = conn.cursor()
    clauses = ["owner_id=?", "status='active'"]
    params: List[Any] = [owner_id]
    if scene:
        clauses.append("scene=?")
        params.append(scene)
    cur.execute(
        f"""
        SELECT *
        FROM agent_profile
        WHERE {' AND '.join(clauses)}
        ORDER BY created_at DESC, display_name ASC
        """,
        params,
    )
    rows = cur.fetchall()
    conn.close()
    return [_row_to_agent_profile(dict(row)) for row in rows]


def get_agent_profile(cfg: Dict[str, Any], profile_id: str, owner_id: str) -> Optional[Dict[str, Any]]:
    ensure_audit_capabilities_seeded(cfg)
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM agent_profile
        WHERE id=? AND owner_id=? AND status='active'
        """,
        (profile_id, owner_id),
    )
    row = cur.fetchone()
    conn.close()
    return _row_to_agent_profile(dict(row)) if row else None


def create_agent_profile(
    cfg: Dict[str, Any],
    owner_id: str,
    display_name: str,
    template_id: str,
    description: str = "",
    system_prompt: str = "",
    enabled_skill_ids: Optional[List[str]] = None,
    enabled_rule_pack_ids: Optional[List[str]] = None,
    max_llm_steps: Optional[int] = None,
    max_skills_per_run: Optional[int] = None,
) -> Dict[str, Any]:
    ensure_audit_capabilities_seeded(cfg)
    template = get_template_detail(cfg, template_id, user_id=owner_id)
    if not template:
        raise ValueError("template not found")

    normalized_skill_ids = _normalize_string_list(
        enabled_skill_ids, "enabled_skill_ids")
    normalized_rule_pack_ids = _normalize_string_list(
        enabled_rule_pack_ids, "enabled_rule_pack_ids")
    _validate_agent_limits(max_llm_steps, max_skills_per_run)

    final_skill_ids = (
        list(normalized_skill_ids)
        if normalized_skill_ids is not None
        else list(template.get("skill_ids") or [])
    )
    final_rule_pack_ids = (
        list(normalized_rule_pack_ids)
        if normalized_rule_pack_ids is not None
        else list(template.get("rule_pack_ids") or [])
    )
    _validate_agent_bindings(
        cfg,
        owner_id=owner_id,
        scene=template["scene"],
        enabled_skill_ids=final_skill_ids,
        enabled_rule_pack_ids=final_rule_pack_ids,
    )
    resolved_max_skills_per_run = max(
        1, int(max_skills_per_run or template.get("max_skills_per_run") or 6))
    _validate_agent_runtime_combination(
        final_skill_ids, resolved_max_skills_per_run)

    now = _utc_now_iso()
    profile_id = str(uuid.uuid4())
    payload = {
        "id": profile_id,
        "owner_id": owner_id,
        "display_name": str(display_name or "").strip(),
        "description": str(description or "").strip(),
        "scene": str(template["scene"]),
        "template_id": template_id,
        "enabled_skill_ids_json": _json_text(final_skill_ids),
        "enabled_rule_pack_ids_json": _json_text(final_rule_pack_ids),
        "system_prompt": str(system_prompt or "").strip(),
        "max_llm_steps": max(1, int(max_llm_steps or template.get("max_llm_steps") or 2)),
        "max_skills_per_run": resolved_max_skills_per_run,
        "status": "active",
        "created_at": now,
        "updated_at": now,
    }
    if not payload["display_name"]:
        raise ValueError("display_name is required")

    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO agent_profile(
            id, owner_id, display_name, description, scene, template_id,
            enabled_skill_ids_json, enabled_rule_pack_ids_json, system_prompt,
            max_llm_steps, max_skills_per_run, status, created_at, updated_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            payload["id"],
            payload["owner_id"],
            payload["display_name"],
            payload["description"],
            payload["scene"],
            payload["template_id"],
            payload["enabled_skill_ids_json"],
            payload["enabled_rule_pack_ids_json"],
            payload["system_prompt"],
            payload["max_llm_steps"],
            payload["max_skills_per_run"],
            payload["status"],
            payload["created_at"],
            payload["updated_at"],
        ),
    )
    conn.commit()
    conn.close()
    return get_agent_profile(cfg, profile_id, owner_id) or {}


def update_agent_profile(
    cfg: Dict[str, Any],
    profile_id: str,
    owner_id: str,
    display_name: Optional[str] = None,
    description: Optional[str] = None,
    system_prompt: Optional[str] = None,
    enabled_skill_ids: Optional[List[str]] = None,
    enabled_rule_pack_ids: Optional[List[str]] = None,
    max_llm_steps: Optional[int] = None,
    max_skills_per_run: Optional[int] = None,
) -> Dict[str, Any]:
    ensure_audit_capabilities_seeded(cfg)
    current = get_agent_profile(cfg, profile_id, owner_id)
    if not current:
        raise ValueError("agent profile not found")

    normalized_skill_ids = _normalize_string_list(
        enabled_skill_ids, "enabled_skill_ids")
    normalized_rule_pack_ids = _normalize_string_list(
        enabled_rule_pack_ids, "enabled_rule_pack_ids")
    _validate_agent_limits(max_llm_steps, max_skills_per_run)

    next_skill_ids = (
        list(normalized_skill_ids)
        if normalized_skill_ids is not None
        else list(current.get("enabled_skill_ids") or [])
    )
    next_rule_pack_ids = (
        list(normalized_rule_pack_ids)
        if normalized_rule_pack_ids is not None
        else list(current.get("enabled_rule_pack_ids") or [])
    )
    next_max_skills_per_run = (
        int(max_skills_per_run)
        if max_skills_per_run is not None
        else int(current.get("max_skills_per_run") or 1)
    )

    _validate_agent_bindings(
        cfg,
        owner_id=owner_id,
        scene=str(current.get("scene") or ""),
        enabled_skill_ids=next_skill_ids,
        enabled_rule_pack_ids=next_rule_pack_ids,
    )
    _validate_agent_runtime_combination(
        next_skill_ids, next_max_skills_per_run)

    updates: Dict[str, Any] = {}
    if display_name is not None:
        normalized_display_name = str(display_name or "").strip()
        if not normalized_display_name:
            raise ValueError("display_name is required")
        updates["display_name"] = normalized_display_name
    if description is not None:
        updates["description"] = str(description or "").strip()
    if system_prompt is not None:
        updates["system_prompt"] = str(system_prompt or "").strip()
    if normalized_skill_ids is not None:
        updates["enabled_skill_ids_json"] = _json_text(next_skill_ids)
    if normalized_rule_pack_ids is not None:
        updates["enabled_rule_pack_ids_json"] = _json_text(next_rule_pack_ids)
    if max_llm_steps is not None:
        updates["max_llm_steps"] = int(max_llm_steps)
    if max_skills_per_run is not None:
        updates["max_skills_per_run"] = next_max_skills_per_run

    if not updates:
        raise ValueError("no fields to update")

    updates["updated_at"] = _utc_now_iso()
    set_clause = ", ".join(f"{column}=?" for column in updates.keys())
    params = list(updates.values()) + [profile_id, owner_id]

    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        f"""
        UPDATE agent_profile
        SET {set_clause}
        WHERE id=? AND owner_id=? AND status='active'
        """,
        params,
    )
    conn.commit()
    conn.close()
    return get_agent_profile(cfg, profile_id, owner_id) or {}
