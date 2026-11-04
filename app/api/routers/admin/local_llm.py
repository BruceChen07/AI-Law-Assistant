"""Admin endpoints for the loopback llama.cpp runtime."""

from __future__ import annotations

import time
from typing import Any, Dict, List

from fastapi import APIRouter, Depends

from app.api.dependencies import require_admin
from app.core.config import get_config
from app.local_llm.policy import ensure_llamacpp_target


router = APIRouter()
MODEL_CACHE_TTL_SECONDS = 30.0
LLAMACPP_MODELS_CACHE: Dict[str, Any] = {
    "host": "",
    "ts": 0.0,
    "models": [],
    "error": "",
}


def _normalize_models(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = payload.get("data") if isinstance(payload.get("data"), list) else []
    models = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        name = str(row.get("id") or row.get("model") or "").strip()
        if not name:
            continue
        models.append(
            {
                "name": name,
                "model": name,
                "size": int(metadata.get("size_bytes") or 0),
                "family": str(metadata.get("family") or ""),
                "quantization": str(metadata.get("quantization") or ""),
                "context_length": int(metadata.get("context_length") or 0),
            }
        )
    return models


@router.get("/llama-cpp/models")
def get_llamacpp_models(
    force_refresh: bool = False,
    current_user: dict = Depends(require_admin),
):
    from app.api.routers import admin as admin_package

    cfg = get_config()
    target = dict((cfg.get("local_llm") or {}).get("main_model") or {})
    api_base = str(target.get("api_base") or "").rstrip("/")
    ensure_llamacpp_target(target.get("provider"), api_base)
    now = time.time()
    cache = LLAMACPP_MODELS_CACHE
    if (
        not force_refresh
        and cache.get("host") == api_base
        and now - float(cache.get("ts") or 0) < MODEL_CACHE_TTL_SECONDS
    ):
        models = list(cache.get("models") or [])
        error = str(cache.get("error") or "")
        return {
            "ok": not error,
            "reachable": not error,
            "models": models,
            "cached": True,
            "error": error,
        }

    try:
        response = admin_package.httpx.get(f"{api_base}/models", timeout=5)
        response.raise_for_status()
        models = _normalize_models(response.json())
        error = ""
    except Exception as exc:
        models = []
        error = str(exc)
    cache.update(
        {"host": api_base, "ts": now, "models": models, "error": error}
    )
    return {
        "ok": not error,
        "reachable": not error,
        "models": models,
        "cached": False,
        "error": error,
    }
