"""Shared runtime helpers for local LLM routing, fallback and worker limits."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Tuple


Validator = Callable[[Any, Any], bool]


def _clean_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _clean_list(value: Any) -> list:
    return list(value) if isinstance(value, (list, tuple)) else []


def _is_enabled(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def get_local_llm_cfg(cfg: Dict[str, Any] | None) -> Dict[str, Any]:
    return _clean_dict((cfg or {}).get("local_llm"))


def is_cloud_fallback_enabled(cfg: Dict[str, Any] | None) -> bool:
    return _is_enabled(get_local_llm_cfg(cfg).get("cloud_fallback_enabled"), True)


def is_high_risk_force_cloud(cfg: Dict[str, Any] | None) -> bool:
    routing_cfg = _clean_dict(get_local_llm_cfg(cfg).get("routing"))
    return _is_enabled(routing_cfg.get("high_risk_force_cloud"), False)


def get_local_worker_limit(
    cfg: Dict[str, Any] | None,
    local_key: str,
    global_key: str,
    default: int,
    task_count: int | None = None,
) -> int:
    local_cfg = get_local_llm_cfg(cfg)
    execution_cfg = _clean_dict(local_cfg.get("execution"))
    try:
        value = int(execution_cfg.get(local_key) or (
            cfg or {}).get(global_key) or default)
    except Exception:
        value = int(default)
    value = max(1, value)
    if task_count is not None:
        value = min(value, max(1, int(task_count)))
    return value


def get_cloud_review_labels(cfg: Dict[str, Any] | None) -> list[str]:
    execution_cfg = _clean_dict(get_local_llm_cfg(cfg).get("execution"))
    labels = [str(x).strip().lower() for x in _clean_list(
        execution_cfg.get("tax_match_cloud_review_labels")) if str(x).strip()]
    return labels or ["non_compliant"]


def get_tax_match_min_confidence(cfg: Dict[str, Any] | None, default: float = 0.65) -> float:
    execution_cfg = _clean_dict(get_local_llm_cfg(cfg).get("execution"))
    try:
        return float(execution_cfg.get("tax_match_min_confidence") or default)
    except Exception:
        return float(default)


def should_retry_on_error(cfg: Dict[str, Any] | None, default: bool = True) -> bool:
    execution_cfg = _clean_dict(get_local_llm_cfg(cfg).get("execution"))
    return _is_enabled(execution_cfg.get("fallback_on_error"), default)


def should_retry_on_invalid_json(cfg: Dict[str, Any] | None, default: bool = True) -> bool:
    execution_cfg = _clean_dict(get_local_llm_cfg(cfg).get("execution"))
    return _is_enabled(execution_cfg.get("fallback_on_invalid_json"), default)


def get_execution_flag(cfg: Dict[str, Any] | None, key: str, default: bool = False) -> bool:
    execution_cfg = _clean_dict(get_local_llm_cfg(cfg).get("execution"))
    return _is_enabled(execution_cfg.get(key), default)


def _call_task_profile(llm, messages, task_profile: str, overrides: Optional[Dict[str, Any]] = None):
    if hasattr(llm, "chat_with_profile"):
        return llm.chat_with_profile(messages, task_profile, overrides=overrides)
    next_overrides = dict(overrides or {})
    next_overrides["_task_profile"] = task_profile
    return llm.chat(messages, overrides=next_overrides)


def call_with_fallback(
    llm,
    cfg: Dict[str, Any] | None,
    messages,
    task_profile: str,
    overrides: Optional[Dict[str, Any]] = None,
    validator: Optional[Validator] = None,
    force_cloud: bool = False,
    retry_on_error: bool = True,
    retry_on_invalid: bool = True,
) -> Tuple[Any, Any, Dict[str, Any]]:
    retry_on_error = bool(retry_on_error) and should_retry_on_error(cfg, True)
    retry_on_invalid = bool(
        retry_on_invalid) and should_retry_on_invalid_json(cfg, True)
    meta = {
        "fallback_used": False,
        "fallback_reason": "",
        "final_model_role": "cloud_fallback" if force_cloud else "auto",
    }
    primary_overrides = dict(overrides or {})
    if force_cloud:
        primary_overrides["_model_role"] = "cloud_fallback"
    try:
        text, raw = _call_task_profile(
            llm, messages, task_profile, primary_overrides)
    except Exception:
        if force_cloud or not retry_on_error or not is_cloud_fallback_enabled(cfg):
            raise
        cloud_overrides = dict(overrides or {})
        cloud_overrides["_model_role"] = "cloud_fallback"
        text, raw = _call_task_profile(
            llm, messages, task_profile, cloud_overrides)
        meta["fallback_used"] = True
        meta["fallback_reason"] = "error"
        meta["final_model_role"] = "cloud_fallback"
        return text, raw, meta

    is_valid = validator(text, raw) if callable(validator) else True
    if force_cloud or is_valid or not retry_on_invalid or not is_cloud_fallback_enabled(cfg):
        route = raw.get("_route") if isinstance(
            raw, dict) and isinstance(raw.get("_route"), dict) else {}
        if route.get("selected_role"):
            meta["final_model_role"] = str(route.get("selected_role"))
        return text, raw, meta

    cloud_overrides = dict(overrides or {})
    cloud_overrides["_model_role"] = "cloud_fallback"
    cloud_text, cloud_raw = _call_task_profile(
        llm, messages, task_profile, cloud_overrides)
    meta["fallback_used"] = True
    meta["fallback_reason"] = "invalid_result"
    meta["final_model_role"] = "cloud_fallback"
    return cloud_text, cloud_raw, meta
