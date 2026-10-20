from __future__ import annotations

import os
from typing import Any, Dict, List, Mapping, Optional, Tuple


PROVIDER_ALIASES = {
    "deepseek": "deepseek",
    "openai": "openai",
    "openai_compatible": "openai_compatible",
    "compatible": "openai_compatible",
    "qwen": "qwen",
    "dashscope": "qwen",
    "tongyi": "qwen",
    "wenxin": "wenxin",
    "ernie": "wenxin",
    "qianfan": "wenxin",
    "baidu": "wenxin",
    "ollama": "ollama",
}


PROVIDER_LABELS = {
    "deepseek": "DeepSeek",
    "openai": "OpenAI",
    "openai_compatible": "OpenAI Compatible",
    "qwen": "Qwen / DashScope",
    "wenxin": "Wenxin / Qianfan",
    "ollama": "Ollama",
}


CLOUD_PROVIDERS = {
    "deepseek",
    "openai",
    "openai_compatible",
    "qwen",
    "wenxin",
}


ENV_PROVIDER_HINTS = (
    ("deepseek", "DEEPSEEK_*", ("DEEPSEEK_API_KEY",
     "DEEPSEEK_MODEL", "DEEPSEEK_API_BASE")),
    ("openai", "OPENAI_*", ("OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL")),
    ("qwen", "DASHSCOPE_*", ("DASHSCOPE_API_KEY",
     "DASHSCOPE_MODEL", "DASHSCOPE_BASE_URL")),
    (
        "wenxin",
        "QIANFAN_*",
        (
            "QIANFAN_BEARER_TOKEN",
            "QIANFAN_API_KEY",
            "QIANFAN_MODEL",
            "QIANFAN_BASE_URL",
            "QIANFAN_ACCESS_KEY",
            "QIANFAN_SECRET_KEY",
        ),
    ),
)


PROVIDER_SPECS: Dict[str, Dict[str, Any]] = {
    "deepseek": {
        "default_base_url": "https://api.deepseek.com",
        "supported_env_vars": [
            "LLM_PROVIDER",
            "LLM_API_BASE",
            "LLM_MODEL",
            "LLM_API_KEY",
            "DEEPSEEK_API_BASE",
            "DEEPSEEK_MODEL",
            "DEEPSEEK_API_KEY",
        ],
        "base_url_envs": ["LLM_API_BASE", "DEEPSEEK_API_BASE"],
        "model_envs": ["LLM_MODEL", "DEEPSEEK_MODEL"],
        "api_key_envs": ["LLM_API_KEY", "DEEPSEEK_API_KEY"],
        "requires_api_key": True,
    },
    "openai": {
        "default_base_url": "https://api.openai.com/v1",
        "supported_env_vars": [
            "LLM_PROVIDER",
            "LLM_API_BASE",
            "LLM_MODEL",
            "LLM_API_KEY",
            "OPENAI_BASE_URL",
            "OPENAI_MODEL",
            "OPENAI_API_KEY",
        ],
        "base_url_envs": ["LLM_API_BASE", "OPENAI_BASE_URL"],
        "model_envs": ["LLM_MODEL", "OPENAI_MODEL"],
        "api_key_envs": ["LLM_API_KEY", "OPENAI_API_KEY"],
        "requires_api_key": True,
    },
    "openai_compatible": {
        "default_base_url": "",
        "supported_env_vars": [
            "LLM_PROVIDER",
            "LLM_API_BASE",
            "LLM_MODEL",
            "LLM_API_KEY",
        ],
        "base_url_envs": ["LLM_API_BASE"],
        "model_envs": ["LLM_MODEL"],
        "api_key_envs": ["LLM_API_KEY"],
        "requires_api_key": False,
    },
    "qwen": {
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "supported_env_vars": [
            "LLM_PROVIDER",
            "LLM_API_BASE",
            "LLM_MODEL",
            "LLM_API_KEY",
            "DASHSCOPE_BASE_URL",
            "DASHSCOPE_MODEL",
            "DASHSCOPE_API_KEY",
        ],
        "base_url_envs": ["LLM_API_BASE", "DASHSCOPE_BASE_URL"],
        "model_envs": ["LLM_MODEL", "DASHSCOPE_MODEL"],
        "api_key_envs": ["LLM_API_KEY", "DASHSCOPE_API_KEY"],
        "requires_api_key": True,
    },
    "wenxin": {
        "default_base_url": "https://qianfan.baidubce.com/v2",
        "supported_env_vars": [
            "LLM_PROVIDER",
            "LLM_API_BASE",
            "LLM_MODEL",
            "LLM_API_KEY",
            "QIANFAN_BASE_URL",
            "QIANFAN_MODEL",
            "QIANFAN_BEARER_TOKEN",
            "QIANFAN_API_KEY",
            "QIANFAN_ACCESS_KEY",
            "QIANFAN_SECRET_KEY",
        ],
        "base_url_envs": ["LLM_API_BASE", "QIANFAN_BASE_URL"],
        "model_envs": ["LLM_MODEL", "QIANFAN_MODEL"],
        "api_key_envs": ["LLM_API_KEY", "QIANFAN_BEARER_TOKEN", "QIANFAN_API_KEY"],
        "access_key_envs": ["QIANFAN_ACCESS_KEY"],
        "secret_key_envs": ["QIANFAN_SECRET_KEY"],
        "requires_api_key": True,
    },
    "ollama": {
        "default_base_url": "http://127.0.0.1:11434/v1",
        "supported_env_vars": [
            "LLM_PROVIDER",
            "LLM_API_BASE",
            "LLM_MODEL",
            "OLLAMA_HOST",
            "OLLAMA_MODEL",
        ],
        "base_url_envs": ["LLM_API_BASE"],
        "model_envs": ["LLM_MODEL", "OLLAMA_MODEL"],
        "api_key_envs": [],
        "requires_api_key": False,
    },
}


SECRET_PATHS = (
    "llm_config.api_key",
    "local_llm.main_model.api_key",
    "local_llm.small_model.api_key",
)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _get_nested(cfg: Dict[str, Any], path: str) -> Any:
    current: Any = cfg
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def normalize_provider(value: Any) -> str:
    provider = _clean_text(value).lower()
    if not provider:
        return "openai_compatible"
    return PROVIDER_ALIASES.get(provider, provider)


def get_provider_spec(provider: Any) -> Dict[str, Any]:
    normalized = normalize_provider(provider)
    return dict(PROVIDER_SPECS.get(normalized) or PROVIDER_SPECS["openai_compatible"])


def get_provider_label(provider: Any) -> str:
    normalized = normalize_provider(provider)
    return PROVIDER_LABELS.get(normalized, normalized or "Unknown")


def is_cloud_provider(provider: Any) -> bool:
    return normalize_provider(provider) in CLOUD_PROVIDERS


def detect_env_provider_hint(env: Optional[Mapping[str, str]] = None) -> Tuple[str, str]:
    env_map: Mapping[str, str] = env or os.environ
    raw_provider = _clean_text(env_map.get("LLM_PROVIDER", ""))
    if raw_provider:
        return normalize_provider(raw_provider), "LLM_PROVIDER"
    for provider, source, names in ENV_PROVIDER_HINTS:
        if any(_clean_text(env_map.get(name, "")) for name in names):
            return provider, source
    return "", ""


def _first_env(env: Mapping[str, str], names: List[str]) -> Tuple[str, str]:
    for name in names:
        value = _clean_text(env.get(name, ""))
        if value:
            return value, name
    return "", ""


def _resolve_base_url(
    provider: str,
    cfg: Dict[str, Any],
    env: Mapping[str, str],
    env_sources: Dict[str, str],
    allow_env_override: bool = True,
) -> str:
    spec = get_provider_spec(provider)
    if allow_env_override and provider == "ollama":
        host = _clean_text(env.get("OLLAMA_HOST", ""))
        if host:
            env_sources["api_base"] = "OLLAMA_HOST"
            return host.rstrip("/") + "/v1"
    if allow_env_override:
        value, name = _first_env(env, list(spec.get("base_url_envs") or []))
        if value:
            env_sources["api_base"] = name
            return value
    return _clean_text(cfg.get("api_base", "")) or _clean_text(spec.get("default_base_url", ""))


def _resolve_model(
    provider: str,
    cfg: Dict[str, Any],
    env: Mapping[str, str],
    env_sources: Dict[str, str],
    allow_env_override: bool = True,
) -> str:
    spec = get_provider_spec(provider)
    if allow_env_override:
        value, name = _first_env(env, list(spec.get("model_envs") or []))
        if value:
            env_sources["model"] = name
            return value
    return _clean_text(cfg.get("model", ""))


def _resolve_api_key_payload(
    provider: str,
    env: Mapping[str, str],
    env_sources: Dict[str, str],
) -> Dict[str, str]:
    spec = get_provider_spec(provider)
    payload: Dict[str, str] = {
        "api_key": "",
        "access_key": "",
        "secret_key": "",
    }
    value, name = _first_env(env, list(spec.get("api_key_envs") or []))
    if value:
        payload["api_key"] = value
        env_sources["api_key"] = name
        return payload
    access_key, access_name = _first_env(
        env, list(spec.get("access_key_envs") or []))
    secret_key, secret_name = _first_env(
        env, list(spec.get("secret_key_envs") or []))
    if access_key:
        payload["access_key"] = access_key
        env_sources["access_key"] = access_name
    if secret_key:
        payload["secret_key"] = secret_key
        env_sources["secret_key"] = secret_name
    return payload


def _resolve_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _resolve_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def collect_persisted_secret_issues(app_cfg: Dict[str, Any]) -> List[str]:
    issues: List[str] = []
    for path in SECRET_PATHS:
        if _clean_text(_get_nested(app_cfg, path)):
            issues.append(
                f"Persisted secret detected in `{path}`. Remove it and use environment variables instead.")
    return issues


def resolve_runtime_llm_config(
    cfg: Optional[Dict[str, Any]],
    env: Optional[Mapping[str, str]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    source_cfg = dict(cfg or {})
    env_map: Mapping[str, str] = env or os.environ
    env_sources: Dict[str, str] = {}
    lock_runtime_target = bool(source_cfg.get("_lock_runtime_target"))
    inferred_provider = ""
    inferred_provider_source = ""
    if not lock_runtime_target:
        inferred_provider, inferred_provider_source = detect_env_provider_hint(
            env_map)
    provider = normalize_provider(
        source_cfg.get("provider", "openai_compatible")
        if lock_runtime_target
        else (inferred_provider or source_cfg.get("provider", "openai_compatible"))
    )
    if not lock_runtime_target and inferred_provider_source:
        env_sources["provider"] = inferred_provider_source
    spec = get_provider_spec(provider)

    resolved = dict(source_cfg)
    resolved["provider"] = provider
    resolved["api_base"] = _resolve_base_url(
        provider, source_cfg, env_map, env_sources, allow_env_override=not lock_runtime_target)
    resolved["model"] = _resolve_model(
        provider, source_cfg, env_map, env_sources, allow_env_override=not lock_runtime_target)
    resolved["temperature"] = _resolve_float(
        _clean_text(env_map.get("LLM_TEMPERATURE", "")
                    ) or source_cfg.get("temperature", 0.2),
        0.2,
    )
    if _clean_text(env_map.get("LLM_TEMPERATURE", "")):
        env_sources["temperature"] = "LLM_TEMPERATURE"
    resolved["max_tokens"] = _resolve_int(
        _clean_text(env_map.get("LLM_MAX_TOKENS", "")
                    ) or source_cfg.get("max_tokens", 2048),
        2048,
    )
    if _clean_text(env_map.get("LLM_MAX_TOKENS", "")):
        env_sources["max_tokens"] = "LLM_MAX_TOKENS"
    resolved["timeout"] = _resolve_int(
        _clean_text(env_map.get("LLM_TIMEOUT", "")
                    ) or source_cfg.get("timeout", 60),
        60,
    )
    if _clean_text(env_map.get("LLM_TIMEOUT", "")):
        env_sources["timeout"] = "LLM_TIMEOUT"
    resolved["headers"] = dict(source_cfg.get("headers")
                               ) if isinstance(source_cfg.get("headers"), dict) else {}

    auth_payload = _resolve_api_key_payload(provider, env_map, env_sources)
    resolved["api_key"] = auth_payload.get("api_key", "")
    resolved["access_key"] = auth_payload.get("access_key", "")
    resolved["secret_key"] = auth_payload.get("secret_key", "")
    prefer_long_timeout = bool(source_cfg.get("_prefer_long_timeout"))
    task_profile = _clean_text(source_cfg.get("_runtime_task_profile", ""))
    if prefer_long_timeout and provider in {"deepseek", "openai", "openai_compatible", "qwen", "wenxin"}:
        if not _clean_text(env_map.get("LLM_TIMEOUT", "")):
            resolved["timeout"] = max(
                int(resolved.get("timeout", 60) or 60),
                int(source_cfg.get("_timeout_floor") or 90),
            )
        resolved["timeout_retry"] = max(
            int(source_cfg.get("timeout_retry") or 0),
            int(source_cfg.get("_timeout_retry_floor") or 480),
        )

    missing_env_vars: List[str] = []
    if not resolved["api_base"]:
        missing_env_vars.extend(list(spec.get("base_url_envs") or []))
    if not resolved["model"]:
        missing_env_vars.extend(list(spec.get("model_envs") or []))
    if bool(spec.get("requires_api_key", False)):
        if provider == "wenxin":
            has_bearer = bool(resolved["api_key"])
            has_aksk = bool(resolved["access_key"] and resolved["secret_key"])
            if not has_bearer and not has_aksk:
                missing_env_vars.extend(list(spec.get("api_key_envs") or []))
                missing_env_vars.extend(
                    list(spec.get("access_key_envs") or []))
                missing_env_vars.extend(
                    list(spec.get("secret_key_envs") or []))
        elif not resolved["api_key"]:
            missing_env_vars.extend(list(spec.get("api_key_envs") or []))
    missing_env_vars = sorted({item for item in missing_env_vars if item})

    meta = {
        "env_managed": True,
        "provider": provider,
        "provider_label": get_provider_label(provider),
        "supported_env_vars": list(spec.get("supported_env_vars") or []),
        "required_env_vars": sorted({item for item in (list(spec.get("api_key_envs") or []) + list(spec.get("base_url_envs") or []) + list(spec.get("model_envs") or []) + list(spec.get("access_key_envs") or []) + list(spec.get("secret_key_envs") or [])) if item}),
        "missing_env_vars": missing_env_vars,
        "env_sources": env_sources,
        "target_locked": lock_runtime_target,
        "task_profile": task_profile,
    }
    return resolved, meta
