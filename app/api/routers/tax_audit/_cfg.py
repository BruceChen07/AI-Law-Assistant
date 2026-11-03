"""Config resolution shared by the tax-audit handler submodules.

Mirrors `contracts/_cfg.py`: prefer the config on `app.state`, fall back to
the one bound by the most recent `build_router(cfg)` call so tests can build
a bare `FastAPI()`.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import HTTPException, Request

_active_cfg: Optional[Dict[str, Any]] = None


def set_active_config(cfg: Optional[Dict[str, Any]]) -> None:
    global _active_cfg
    _active_cfg = cfg


def tax_cfg(request: Request) -> Dict[str, Any]:
    cfg = getattr(request.app.state, "cfg", None)
    if cfg is None:
        cfg = _active_cfg
    if cfg is None:
        raise HTTPException(status_code=503, detail="config unavailable")
    return cfg
