"""Config resolution shared by the contract handler submodules.

Handlers used to close over the `cfg` argument of `build_router(cfg)`. Now that
they are module-level functions they need a dependency. Two sources are tried,
in order:

1. `request.app.state.cfg` - set by `app_factory` in production.
2. The config most recently passed to `build_router(cfg)` - this keeps the
   factory's signature meaningful and lets tests build a bare `FastAPI()`,
   call `build_router(test_cfg)` and get exactly that config.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import HTTPException, Request

_active_cfg: Optional[Dict[str, Any]] = None


def set_active_config(cfg: Optional[Dict[str, Any]]) -> None:
    """Remember the config bound by the most recent `build_router(cfg)` call."""
    global _active_cfg
    _active_cfg = cfg


def contracts_cfg(request: Request) -> Dict[str, Any]:
    cfg = getattr(request.app.state, "cfg", None)
    if cfg is None:
        cfg = _active_cfg
    if cfg is None:
        raise HTTPException(status_code=503, detail="config unavailable")
    return cfg
