"""Audit skill administration."""
from __future__ import annotations

from app.api.dependencies import require_admin
from app.core.auth import log_audit
from app.core.config import get_config
from app.services.audit_capabilities import create_skill, delete_skill, get_admin_skill_detail, list_admin_skills, set_skill_status, update_skill
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from app.api.routers.admin.schemas import (
    DeleteResponse,
    SkillCreateRequest,
    SkillListResponse,
    SkillResponse,
    SkillStatusUpdateRequest,
    SkillUpdateRequest,
)

router = APIRouter()


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
