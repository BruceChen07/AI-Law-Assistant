"""Memory runtime configuration."""
from __future__ import annotations

from app.api.dependencies import require_admin
from app.core.config import get_config, update_config_patch
from fastapi import APIRouter, Depends, HTTPException
from app.api.routers.admin.schemas import (
    MemoryRuntimeConfigResponse,
    MemoryRuntimeConfigUpdate,
)

router = APIRouter()


def _get_memory_runtime_config(cfg: dict) -> dict:
    raw = cfg.get("memory_runtime_config") if isinstance(
        cfg.get("memory_runtime_config"), dict) else {}
    mode = str(raw.get("memory_mode_when_disabled")
               or "classic").strip().lower()
    if mode not in {"classic"}:
        mode = "classic"
    max_calls = int(raw.get("memory_max_llm_calls_per_audit") or 12)
    max_prompt_chars = int(
        raw.get("memory_max_prompt_chars_per_clause") or 2400)
    max_calls = max(1, min(max_calls, 200))
    max_prompt_chars = max(300, min(max_prompt_chars, 12000))
    return {
        "memory_module_enabled": bool(raw.get("memory_module_enabled", True)),
        "memory_mode_when_disabled": mode,
        "memory_disable_fallback_on_error": bool(raw.get("memory_disable_fallback_on_error", True)),
        "memory_token_guard_enabled": bool(raw.get("memory_token_guard_enabled", True)),
        "memory_max_llm_calls_per_audit": max_calls,
        "memory_max_prompt_chars_per_clause": max_prompt_chars,
        "risk_notice": "Enabling memory mode may increase LLM calls and token cost; disable it for lower-cost audits with potentially reduced recall quality.",
    }


@router.get("/memory-config", response_model=MemoryRuntimeConfigResponse)
def get_memory_runtime_config(current_user: dict = Depends(require_admin)):
    cfg = get_config()
    mem_cfg = _get_memory_runtime_config(cfg)
    return MemoryRuntimeConfigResponse(**mem_cfg)


@router.put("/memory-config", response_model=MemoryRuntimeConfigResponse)
def update_memory_runtime_config(payload: MemoryRuntimeConfigUpdate, current_user: dict = Depends(require_admin)):
    cfg_current = get_config()
    current = _get_memory_runtime_config(cfg_current)
    patch = payload.model_dump(exclude_unset=True)
    merged = dict(current)
    for k, v in patch.items():
        merged[k] = v
    mode = str(merged.get("memory_mode_when_disabled")
               or "classic").strip().lower()
    if mode not in {"classic"}:
        raise HTTPException(
            status_code=400, detail="memory_mode_when_disabled must be 'classic'")
    max_calls = int(merged.get("memory_max_llm_calls_per_audit") or 12)
    max_prompt_chars = int(merged.get(
        "memory_max_prompt_chars_per_clause") or 2400)
    if max_calls <= 0:
        raise HTTPException(
            status_code=400, detail="memory_max_llm_calls_per_audit must be positive")
    if max_prompt_chars < 300:
        raise HTTPException(
            status_code=400, detail="memory_max_prompt_chars_per_clause must be >= 300")
    save_payload = {
        "memory_module_enabled": bool(merged.get("memory_module_enabled", True)),
        "memory_mode_when_disabled": mode,
        "memory_disable_fallback_on_error": bool(merged.get("memory_disable_fallback_on_error", True)),
        "memory_token_guard_enabled": bool(merged.get("memory_token_guard_enabled", True)),
        "memory_max_llm_calls_per_audit": max_calls,
        "memory_max_prompt_chars_per_clause": max_prompt_chars,
    }
    cfg = update_config_patch({"memory_runtime_config": save_payload})
    out = _get_memory_runtime_config(cfg)
    return MemoryRuntimeConfigResponse(**out)
