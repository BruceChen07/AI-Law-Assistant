"""Runtime policy for the pure llama.cpp distribution."""

from __future__ import annotations

import os
from typing import Any, Mapping
from urllib.parse import urlsplit


_TRUE_VALUES = {"1", "true", "yes", "on"}
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _enabled(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in _TRUE_VALUES


def local_llm_enabled(env: Mapping[str, str] | None = None) -> bool:
    env_map = env or os.environ
    return _enabled(env_map.get("ENABLE_LOCAL_LLM"), True)


def cloud_api_test_enabled(env: Mapping[str, str] | None = None) -> bool:
    env_map = env or os.environ
    return _enabled(env_map.get("ENABLE_CLOUD_API_TEST"), False)


def assert_local_runtime_enabled(env: Mapping[str, str] | None = None) -> None:
    if not local_llm_enabled(env):
        raise RuntimeError(
            "Local LLM runtime is disabled. Set ENABLE_LOCAL_LLM=true."
        )
    if cloud_api_test_enabled(env):
        raise RuntimeError(
            "ENABLE_CLOUD_API_TEST is not supported by the pure llama.cpp branch."
        )


def ensure_llamacpp_target(provider: Any, api_base: Any) -> None:
    assert_local_runtime_enabled()
    normalized_provider = str(provider or "").strip().lower()
    if normalized_provider != "llama_cpp":
        raise RuntimeError(
            f"Provider '{normalized_provider or '<empty>'}' is blocked; "
            "the pure local branch only permits llama_cpp."
        )

    parsed = urlsplit(str(api_base or "").strip())
    host = str(parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in _LOOPBACK_HOSTS:
        raise RuntimeError(
            "llama.cpp api_base must use a loopback host "
            "(127.0.0.1, ::1, or localhost)."
        )
