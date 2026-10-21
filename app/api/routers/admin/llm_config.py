"""LLM configuration and connectivity test."""
from __future__ import annotations

from app.api.dependencies import get_app_llm, require_admin
from app.core.config import get_config
from app.services.prompt_binding import render_prompt_messages
from fastapi import APIRouter, Depends, HTTPException, Request
from typing import Optional
from app.api.routers.admin._common import (
    _clean_text,
)
from app.api.routers.admin.schemas import (
    LLMConfigResponse,
    LLMConfigUpdate,
    LLMTestRequest,
    LLMTestResponse,
)

router = APIRouter()


def _normalize_llm_payload(payload: dict) -> dict:
    data = dict(payload or {})
    data["api_base"] = _clean_text(data.get("api_base", ""))
    data["model"] = _clean_text(data.get("model", ""))
    if not isinstance(data.get("headers"), dict):
        data["headers"] = {}
    return data


def _validate_llm_config(payload: dict):
    api_base = str(payload.get("api_base", "")).strip()
    model = str(payload.get("model", "")).strip()
    if not api_base.startswith("http"):
        raise HTTPException(
            status_code=400, detail="api_base must be a valid http(s) url")
    if not model:
        raise HTTPException(status_code=400, detail="model is required")
    temperature = float(payload.get("temperature", 0.2))
    if temperature < 0 or temperature > 2:
        raise HTTPException(status_code=400, detail="temperature out of range")
    max_tokens = int(payload.get("max_tokens", 2048))
    if max_tokens <= 0:
        raise HTTPException(
            status_code=400, detail="max_tokens must be positive")
    timeout = int(payload.get("timeout", 60))
    if timeout <= 0:
        raise HTTPException(status_code=400, detail="timeout must be positive")


@router.get("/llm-config", response_model=LLMConfigResponse)
def get_llm_config(current_user: dict = Depends(require_admin), llm=Depends(get_app_llm)):
    runtime = llm.describe_runtime_config()
    return LLMConfigResponse(**runtime)


@router.put("/llm-config", response_model=LLMConfigResponse)
def update_llm_config(payload: LLMConfigUpdate, request: Request, current_user: dict = Depends(require_admin)):
    raise HTTPException(
        status_code=405,
        detail="LLM config is environment-managed. Update Windows environment variables instead of saving values in the app.",
    )


@router.delete("/llm-config/api-key")
def clear_llm_api_key(request: Request, current_user: dict = Depends(require_admin)):
    raise HTTPException(
        status_code=405,
        detail="API keys are environment-managed. Clear or update the corresponding Windows environment variables instead.",
    )


@router.post("/llm-test", response_model=LLMTestResponse)
def test_llm(
    payload: Optional[LLMTestRequest] = None,
    current_user: dict = Depends(require_admin),
    llm=Depends(get_app_llm),
):
    prompt = ""
    if payload:
        prompt = str(payload.prompt or "").strip()
    if not prompt:
        prompt = "你是什么模型？"
    rendered = render_prompt_messages(
        "admin.llm_test",
        {"prompt": prompt},
        cfg=get_config(),
    )
    messages = [
        {"role": "system", "content": str(
            rendered.get("rendered_system_text") or "")},
        {"role": "user", "content": str(
            rendered.get("rendered_text") or prompt)},
    ]
    try:
        answer, _ = llm.chat(messages)
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"llm test failed: {str(e)}")
    return LLMTestResponse(ok=True, prompt=prompt, answer=answer)
