from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import httpx

from app.core.llm_env import get_provider_label, normalize_provider

if TYPE_CHECKING:
    from app.core.llm import LLMService


logger = logging.getLogger("law_assistant")


class BaseLLMProvider(ABC):
    provider_name = "openai_compatible"

    @property
    def display_name(self) -> str:
        return get_provider_label(self.provider_name)

    def ensure_ready(self, cfg: Dict[str, Any]) -> None:
        missing = list(cfg.get("_missing_env_vars") or [])
        if missing:
            joined = ", ".join(missing)
            raise RuntimeError(
                f"{self.display_name} environment variables missing: {joined}")

    @abstractmethod
    def chat(
        self,
        service: "LLMService",
        messages: List[Dict[str, str]],
        cfg: Dict[str, Any],
    ) -> Tuple[str, Dict[str, Any]]:
        raise NotImplementedError


class OpenAICompatibleProvider(BaseLLMProvider):
    def _resolve_api_key(
        self,
        service: "LLMService",
        cfg: Dict[str, Any],
    ) -> str:
        return service._clean_text(cfg.get("api_key", ""))

    def chat(
        self,
        service: "LLMService",
        messages: List[Dict[str, str]],
        cfg: Dict[str, Any],
    ) -> Tuple[str, Dict[str, Any]]:
        self.ensure_ready(cfg)
        api_key = self._resolve_api_key(service, cfg)
        return service._chat_via_openai_compatible(messages, cfg, api_key)


class OpenAIProvider(OpenAICompatibleProvider):
    provider_name = "openai"


class QwenProvider(OpenAICompatibleProvider):
    provider_name = "qwen"


class WenxinProvider(OpenAICompatibleProvider):
    provider_name = "wenxin"

    def __init__(self) -> None:
        self._token_cache: Dict[str, Dict[str, Any]] = {}

    def _resolve_api_key(
        self,
        service: "LLMService",
        cfg: Dict[str, Any],
    ) -> str:
        bearer = service._clean_text(cfg.get("api_key", ""))
        if bearer:
            return bearer
        access_key = service._clean_text(cfg.get("access_key", ""))
        secret_key = service._clean_text(cfg.get("secret_key", ""))
        if not access_key or not secret_key:
            self.ensure_ready(cfg)
            raise RuntimeError(
                "Wenxin credentials missing. Configure QIANFAN_BEARER_TOKEN / QIANFAN_API_KEY or QIANFAN_ACCESS_KEY + QIANFAN_SECRET_KEY.")
        cache_key = f"{access_key}:{secret_key}"
        now = time.time()
        cached = self._token_cache.get(cache_key) or {}
        if cached and float(cached.get("expires_at", 0)) > now + 60:
            return str(cached.get("token") or "")
        timeout = max(5, int(cfg.get("timeout", 60)))
        response = httpx.get(
            "https://aip.baidubce.com/oauth/2.0/token",
            params={
                "grant_type": "client_credentials",
                "client_id": access_key,
                "client_secret": secret_key,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        token = service._clean_text(payload.get("access_token", ""))
        if not token:
            raise RuntimeError("Failed to obtain Wenxin access token from Qianfan.")
        expires_in = int(payload.get("expires_in") or 1800)
        self._token_cache[cache_key] = {
            "token": token,
            "expires_at": now + max(300, expires_in),
        }
        logger.info(
            "wenxin_access_token_refreshed provider=%s expires_in=%s",
            self.provider_name,
            expires_in,
        )
        return token


class OllamaProvider(BaseLLMProvider):
    provider_name = "ollama"

    def chat(
        self,
        service: "LLMService",
        messages: List[Dict[str, str]],
        cfg: Dict[str, Any],
    ) -> Tuple[str, Dict[str, Any]]:
        self.ensure_ready(cfg)
        return service._chat_via_ollama_provider(messages, cfg)


_PROVIDER_REGISTRY: Dict[str, BaseLLMProvider] = {
    "openai": OpenAIProvider(),
    "openai_compatible": OpenAICompatibleProvider(),
    "qwen": QwenProvider(),
    "wenxin": WenxinProvider(),
    "ollama": OllamaProvider(),
}


def get_provider(provider: Optional[str]) -> BaseLLMProvider:
    normalized = normalize_provider(provider)
    return _PROVIDER_REGISTRY.get(normalized) or _PROVIDER_REGISTRY["openai_compatible"]

