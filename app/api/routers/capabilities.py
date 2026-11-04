from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.dependencies import get_current_user, require_admin
from app.core.config import get_config
from app.services.audit_capabilities import (
    create_rule_pack_draft,
    get_skill_detail,
    get_rule_pack_draft,
    list_rule_packs,
    list_rule_pack_drafts,
    list_rule_pack_versions,
    list_rules,
    list_templates,
    list_visible_skills,
    review_rule_pack_draft,
    submit_rule_pack_draft,
    update_rule_pack_draft,
)


class RulePackDraftCreateRequest(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=120)
    description: Optional[str] = None
    selector: Optional[Dict[str, Any]] = None
    source_note: Optional[str] = None
    change_summary: str = ""


class RulePackDraftUpdateRequest(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=120)
    description: Optional[str] = None
    selector: Optional[Dict[str, Any]] = None
    source_note: Optional[str] = None
    change_summary: Optional[str] = None


class RulePackDraftReviewRequest(BaseModel):
    action: str = Field(..., min_length=1, max_length=20)
    review_comment: str = ""


def build_router():
    router = APIRouter(prefix="/api/capabilities", tags=["capabilities"])

    @router.get("/skills")
    def get_skills(
        scene: str = Query("", description="按场景过滤"),
        category: str = Query("", description="按分类过滤"),
        current_user: dict = Depends(get_current_user),
    ):
        cfg = get_config()
        items = list_visible_skills(
            cfg,
            user_id=str(current_user.get("id") or ""),
            scene=str(scene or "").strip(),
            category=str(category or "").strip(),
        )
        return {"items": items, "total": len(items)}

    @router.get("/skills/{skill_id}")
    def get_skill(
        skill_id: str,
        current_user: dict = Depends(get_current_user),
    ):
        cfg = get_config()
        item = get_skill_detail(
            cfg, skill_id, user_id=str(current_user.get("id") or ""))
        if not item:
            raise HTTPException(status_code=404, detail="skill not found")
        return item

    @router.get("/rule-packs")
    def get_rule_packs(
        scene: str = Query("", description="按场景过滤"),
        current_user: dict = Depends(get_current_user),
    ):
        cfg = get_config()
        items = list_rule_packs(
            cfg,
            user_id=str(current_user.get("id") or ""),
            scene=str(scene or "").strip(),
        )
        return {"items": items, "total": len(items)}

    @router.get("/rule-packs/{pack_id}/versions")
    def get_rule_pack_versions(
        pack_id: str,
        current_user: dict = Depends(get_current_user),
    ):
        cfg = get_config()
        items = list_rule_pack_versions(
            cfg,
            pack_id=pack_id,
            user_id=str(current_user.get("id") or ""),
        )
        return {"items": items, "total": len(items)}

    @router.get("/rule-pack-drafts")
    def get_rule_pack_drafts(
        status: str = Query("", description="按状态过滤"),
        base_pack_id: str = Query("", description="按基础规则包过滤"),
        current_user: dict = Depends(get_current_user),
    ):
        cfg = get_config()
        is_admin = str(current_user.get("role") or "") == "admin"
        items = list_rule_pack_drafts(
            cfg,
            owner_id=str(current_user.get("id") or ""),
            status=str(status or "").strip(),
            base_pack_id=str(base_pack_id or "").strip(),
            include_all=is_admin,
        )
        return {"items": items, "total": len(items)}

    @router.get("/rule-pack-drafts/{draft_id}")
    def get_draft_detail(
        draft_id: str,
        current_user: dict = Depends(get_current_user),
    ):
        cfg = get_config()
        is_admin = str(current_user.get("role") or "") == "admin"
        item = get_rule_pack_draft(
            cfg,
            draft_id=draft_id,
            owner_id=str(current_user.get("id") or ""),
            include_all=is_admin,
        )
        if not item:
            raise HTTPException(
                status_code=404, detail="rule pack draft not found")
        return item

    @router.post("/rule-packs/{pack_id}/drafts")
    def create_draft(
        pack_id: str,
        payload: RulePackDraftCreateRequest,
        current_user: dict = Depends(get_current_user),
    ):
        cfg = get_config()
        try:
            item = create_rule_pack_draft(
                cfg,
                owner_id=str(current_user.get("id") or ""),
                base_pack_id=pack_id,
                display_name=payload.display_name,
                description=payload.description,
                selector=payload.selector,
                source_note=payload.source_note,
                change_summary=payload.change_summary,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return item

    @router.put("/rule-pack-drafts/{draft_id}")
    def update_draft(
        draft_id: str,
        payload: RulePackDraftUpdateRequest,
        current_user: dict = Depends(get_current_user),
    ):
        cfg = get_config()
        try:
            item = update_rule_pack_draft(
                cfg,
                draft_id=draft_id,
                owner_id=str(current_user.get("id") or ""),
                display_name=payload.display_name,
                description=payload.description,
                selector=payload.selector,
                source_note=payload.source_note,
                change_summary=payload.change_summary,
            )
        except ValueError as exc:
            status_code = 404 if str(
                exc) == "rule pack draft not found" else 400
            raise HTTPException(status_code=status_code,
                                detail=str(exc)) from exc
        return item

    @router.post("/rule-pack-drafts/{draft_id}/submit")
    def submit_draft(
        draft_id: str,
        current_user: dict = Depends(get_current_user),
    ):
        cfg = get_config()
        try:
            item = submit_rule_pack_draft(
                cfg,
                draft_id=draft_id,
                owner_id=str(current_user.get("id") or ""),
            )
        except ValueError as exc:
            status_code = 404 if str(
                exc) == "rule pack draft not found" else 400
            raise HTTPException(status_code=status_code,
                                detail=str(exc)) from exc
        return item

    @router.post("/rule-pack-drafts/{draft_id}/review")
    def review_draft(
        draft_id: str,
        payload: RulePackDraftReviewRequest,
        current_user: dict = Depends(require_admin),
    ):
        cfg = get_config()
        try:
            item = review_rule_pack_draft(
                cfg,
                draft_id=draft_id,
                reviewer_id=str(current_user.get("id") or ""),
                action=payload.action,
                review_comment=payload.review_comment,
            )
        except ValueError as exc:
            status_code = 404 if str(
                exc) == "rule pack draft not found" else 400
            raise HTTPException(status_code=status_code,
                                detail=str(exc)) from exc
        return item

    @router.get("/rules")
    def get_rules(
        pack_id: str = Query("", description="规则包 ID"),
        rule_type: str = Query("", description="规则类型"),
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
        current_user: dict = Depends(get_current_user),
    ):
        _ = current_user
        cfg = get_config()
        result = list_rules(
            cfg,
            pack_id=str(pack_id or "").strip(),
            rule_type=str(rule_type or "").strip(),
            limit=limit,
            offset=offset,
        )
        return result

    @router.get("/templates")
    def get_templates(
        scene: str = Query("", description="按场景过滤"),
        current_user: dict = Depends(get_current_user),
    ):
        cfg = get_config()
        items = list_templates(
            cfg,
            user_id=str(current_user.get("id") or ""),
            scene=str(scene or "").strip(),
        )
        return {"items": items, "total": len(items)}

    return router
