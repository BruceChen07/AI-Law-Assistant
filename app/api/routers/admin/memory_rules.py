"""Memory promotion runs and pending-rule review."""
from __future__ import annotations

from app.api.dependencies import require_admin
from app.core.config import get_config
from app.services.memory_promotion import list_pending_rule_memories, promote_episode_to_rule, review_rule_memory
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional
from app.api.routers.admin.schemas import (
    MemoryPromotionRunRequest,
    MemoryRuleReviewRequest,
)

router = APIRouter()


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
