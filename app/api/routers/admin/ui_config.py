"""UI theme configuration."""
from __future__ import annotations

from app.api.dependencies import require_admin
from app.core.config import get_config, update_config_patch
from fastapi import APIRouter, Depends
from app.api.routers.admin._common import (
    _normalize_theme,
)
from app.api.routers.admin.schemas import (
    UIConfigResponse,
    UIConfigUpdate,
)

router = APIRouter()


def _get_ui_config(cfg: dict) -> dict:
    ui_cfg = cfg.get("ui_config") if isinstance(
        cfg.get("ui_config"), dict) else {}
    return {
        "show_citation_source": bool(ui_cfg.get("show_citation_source", False)),
        "default_theme": _normalize_theme(ui_cfg.get("default_theme")),
    }


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
