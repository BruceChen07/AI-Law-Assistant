"""llama.cpp provider adapter using its OpenAI-compatible local endpoint."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Dict, List, Tuple

import httpx

from app.local_llm.policy import ensure_llamacpp_target

if TYPE_CHECKING:
    from app.core.llm import LLMService

logger = logging.getLogger("law_assistant")


class LlamaCppProvider:
    provider_name = "llama_cpp"
    display_name = "llama.cpp"

    def ensure_ready(self, cfg: Dict[str, Any]) -> None:
        ensure_llamacpp_target(cfg.get("provider"), cfg.get("api_base"))
        if not str(cfg.get("model") or "").strip():
            raise RuntimeError("llama.cpp model is required.")

    def chat(
        self,
        service: "LLMService",
        messages: List[Dict[str, str]],
        cfg: Dict[str, Any],
    ) -> Tuple[str, Dict[str, Any]]:
        self.ensure_ready(cfg)
        api_base = service._build_base_url(str(cfg.get("api_base") or ""))
        model = service._clean_text(cfg.get("model"))
        temperature = float(cfg.get("temperature", 0.2))
        max_tokens = int(cfg.get("max_tokens", 2048))
        timeout = int(cfg.get("timeout", 60))
        request_url = f"{api_base}/chat/completions"
        request_body = service._build_openai_compatible_body(
            model, messages, temperature, max_tokens, cfg
        )
        headers = service._build_headers(None, cfg.get("headers"))
        try:
            raw = service._post_json(request_url, request_body, headers, timeout)
        except httpx.TimeoutException as exc:
            retry_timeout = max(timeout, int(cfg.get("timeout_retry", 240)))
            retry_tokens = max(220, min(max_tokens, int(max_tokens * 0.6)))
            retry_body = service._build_openai_compatible_body(
                model, messages, temperature, retry_tokens, cfg
            )
            logger.warning(
                "llamacpp_timeout_retry model=%s timeout=%s->%s max_tokens=%s->%s",
                model,
                timeout,
                retry_timeout,
                max_tokens,
                retry_tokens,
            )
            try:
                raw = service._post_json(
                    request_url, retry_body, headers, retry_timeout
                )
            except Exception as retry_exc:
                raise RuntimeError(
                    f"llama.cpp request failed after timeout retry: {retry_exc}"
                ) from retry_exc

        choices = raw.get("choices") if isinstance(raw.get("choices"), list) else []
        first = choices[0] if choices else {}
        message = first.get("message") if isinstance(first.get("message"), dict) else {}
        content = message.get("content") or ""
        if isinstance(content, list):
            content = json.dumps(content, ensure_ascii=False)
        reasoning = message.get("reasoning_content") or ""
        if not str(content).strip() and str(reasoning).strip():
            content = reasoning
            message["content"] = content
            raw["_used_reasoning_content_fallback"] = True
        return str(content), raw
