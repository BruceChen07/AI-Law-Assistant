from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.dependencies import get_current_user
from app.core.config import get_config
from app.services.audit_capabilities import (
    create_agent_profile,
    get_agent_profile,
    list_agent_profiles,
    update_agent_profile,
)


class AgentProfileCreateRequest(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=120)
    template_id: str = Field(..., min_length=1, max_length=120)
    description: str = ""
    system_prompt: str = ""
    enabled_skill_ids: Optional[List[str]] = None
    enabled_rule_pack_ids: Optional[List[str]] = None
    max_llm_steps: Optional[int] = Field(default=None, ge=1, le=10)
    max_skills_per_run: Optional[int] = Field(default=None, ge=1, le=20)


class AgentProfileUpdateRequest(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=120)
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    enabled_skill_ids: Optional[List[str]] = None
    enabled_rule_pack_ids: Optional[List[str]] = None
    max_llm_steps: Optional[int] = Field(default=None, ge=1, le=10)
    max_skills_per_run: Optional[int] = Field(default=None, ge=1, le=20)


def build_router():
    router = APIRouter(prefix="/api/agents", tags=["agents"])

    @router.get("")
    def get_agents(
        scene: str = Query("", description="按场景过滤"),
        current_user: dict = Depends(get_current_user),
    ):
        cfg = get_config()
        items = list_agent_profiles(
            cfg,
            owner_id=str(current_user.get("id") or ""),
            scene=str(scene or "").strip(),
        )
        return {"items": items, "total": len(items)}

    @router.get("/{profile_id}")
    def get_agent(
        profile_id: str,
        current_user: dict = Depends(get_current_user),
    ):
        cfg = get_config()
        item = get_agent_profile(
            cfg, profile_id, owner_id=str(current_user.get("id") or ""))
        if not item:
            raise HTTPException(
                status_code=404, detail="agent profile not found")
        return item

    @router.post("")
    def create_agent(
        payload: AgentProfileCreateRequest,
        current_user: dict = Depends(get_current_user),
    ):
        cfg = get_config()
        try:
            item = create_agent_profile(
                cfg,
                owner_id=str(current_user.get("id") or ""),
                display_name=payload.display_name,
                template_id=payload.template_id,
                description=payload.description,
                system_prompt=payload.system_prompt,
                enabled_skill_ids=payload.enabled_skill_ids,
                enabled_rule_pack_ids=payload.enabled_rule_pack_ids,
                max_llm_steps=payload.max_llm_steps,
                max_skills_per_run=payload.max_skills_per_run,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return item

    @router.put("/{profile_id}")
    def update_agent(
        profile_id: str,
        payload: AgentProfileUpdateRequest,
        current_user: dict = Depends(get_current_user),
    ):
        cfg = get_config()
        try:
            item = update_agent_profile(
                cfg,
                profile_id=profile_id,
                owner_id=str(current_user.get("id") or ""),
                display_name=payload.display_name,
                description=payload.description,
                system_prompt=payload.system_prompt,
                enabled_skill_ids=payload.enabled_skill_ids,
                enabled_rule_pack_ids=payload.enabled_rule_pack_ids,
                max_llm_steps=payload.max_llm_steps,
                max_skills_per_run=payload.max_skills_per_run,
            )
        except ValueError as exc:
            status_code = 404 if str(exc) == "agent profile not found" else 400
            raise HTTPException(status_code=status_code,
                                detail=str(exc)) from exc
        return item

    return router
