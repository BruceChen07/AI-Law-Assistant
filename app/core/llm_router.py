from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


DEFAULT_TASK_ROLE_MAP = {
    "default": "main",
    "contract_audit_main": "main",
    "contract_audit_memory": "main",
    "contract_clause_audit": "main",
    "tax_risk_main": "main",
    "memory_flush": "small",
    "tax_match_small": "small",
    "entity_extract_small": "small",
}


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _clean_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _is_enabled(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _normalize_role(value: Any) -> str:
    role = _clean_text(value).lower()
    aliases = {
        "primary": "main",
        "secondary": "small",
        "local_main": "main",
        "local_small": "small",
        "base_service": "base",
        "default_service": "base",
    }
    return aliases.get(role, role)


def _normalize_model_cfg(base_cfg: Dict[str, Any], target_cfg: Dict[str, Any], local_cfg: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base_cfg)
    merged.update({k: v for k, v in target_cfg.items() if v is not None})
    base_headers = _clean_dict(base_cfg.get("headers"))
    target_headers = _clean_dict(target_cfg.get("headers"))
    merged["headers"] = {**base_headers, **target_headers}
    if target_cfg.get("api_key") in {None, ""}:
        merged["api_key"] = base_cfg.get("api_key", "")
    if target_cfg.get("timeout") in {None, ""} and local_cfg.get("timeout_sec") not in {None, ""}:
        merged["timeout"] = local_cfg.get("timeout_sec")
    if target_cfg.get("provider") in {None, ""}:
        merged["provider"] = base_cfg.get("provider", "openai_compatible")
    return merged


def _is_model_target_ready(cfg: Dict[str, Any]) -> bool:
    return bool(_clean_text(cfg.get("api_base")) and _clean_text(cfg.get("model")))


def _resolve_task_role(local_cfg: Dict[str, Any], task_profile: str, preferred_role: str) -> Tuple[str, str]:
    normalized_preferred = _normalize_role(preferred_role)
    # 安全约束：不允许 preferred_role 直接指定 "base"（云端），
    # 仅接受 "main" / "small" 两个本地角色
    if normalized_preferred in {"main", "small"}:
        return normalized_preferred, "preferred_role"
    if normalized_preferred == "base":
        # 忽略云端请求，降级到默认本地角色
        default_role = _normalize_role(DEFAULT_TASK_ROLE_MAP.get(
            task_profile) or DEFAULT_TASK_ROLE_MAP["default"])
        return default_role, "base_role_rejected"
    routing_cfg = _clean_dict(local_cfg.get("routing"))
    profile_map = _clean_dict(routing_cfg.get("task_profiles"))
    mapped_role = _normalize_role(profile_map.get(task_profile))
    # 安全约束：task_profiles 映射也不允许 "base"
    if mapped_role in {"main", "small"}:
        return mapped_role, "task_profile_map"
    default_role = _normalize_role(DEFAULT_TASK_ROLE_MAP.get(
        task_profile) or DEFAULT_TASK_ROLE_MAP["default"])
    return default_role, "default_map"


def resolve_llm_route(
    app_cfg: Dict[str, Any],
    task_profile: Optional[str] = None,
    model_role: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    task_profile = _clean_text(task_profile) or "default"
    preferred_role = _clean_text(model_role)
    base_cfg = _clean_dict((app_cfg or {}).get("llm_config"))
    local_cfg = _clean_dict((app_cfg or {}).get("local_llm"))
    local_enabled = _is_enabled(local_cfg.get("enabled"), False)
    routing_enabled = _is_enabled(local_cfg.get("routing_enabled"), True)
    allow_small_to_main_fallback = _is_enabled(
        local_cfg.get("allow_small_to_main_fallback"), True)

    if not local_enabled:
        return dict(base_cfg), {
            "task_profile": task_profile,
            "requested_role": preferred_role or "auto",
            "selected_role": "base",
            "selected_source": "llm_config",
            "reason": "local_disabled",
            "local_enabled": False,
            "allow_small_to_main_fallback": allow_small_to_main_fallback,
        }

    resolved_role, reason = _resolve_task_role(
        local_cfg,
        task_profile,
        preferred_role if routing_enabled else "main",
    )

    target_key = {
        "main": "main_model",
        "small": "small_model",
        "base": "",
    }.get(resolved_role, "main_model")

    # allow_cloud_fallback: 显式开启才允许回退到 llm_config（云端）
    # 默认 False = 纯本地部署，禁止任何云端逃逸
    allow_cloud_fallback = _is_enabled(local_cfg.get("allow_cloud_fallback"), False)

    if resolved_role == "base":
        if not allow_cloud_fallback:
            # 拒绝云端路由，强制降级到本地 main_model
            main_cfg = _clean_dict(local_cfg.get("main_model"))
            if _is_model_target_ready(main_cfg):
                merged_cfg = _normalize_model_cfg(base_cfg, main_cfg, local_cfg)
                return merged_cfg, {
                    "task_profile": task_profile,
                    "requested_role": preferred_role or "auto",
                    "selected_role": "main",
                    "selected_source": "main_model",
                    "reason": "base_role_rejected_no_cloud",
                    "local_enabled": True,
                    "allow_cloud_fallback": False,
                }
            # main_model 也不可用，返回 base 但在 meta 中标记
        return dict(base_cfg), {
            "task_profile": task_profile,
            "requested_role": preferred_role or "auto",
            "selected_role": "base",
            "selected_source": "llm_config",
            "reason": reason,
            "local_enabled": True,
            "allow_cloud_fallback": allow_cloud_fallback,
        }

    local_target_cfg = _clean_dict(local_cfg.get(target_key))
    if _is_model_target_ready(local_target_cfg):
        merged_cfg = _normalize_model_cfg(
            base_cfg, local_target_cfg, local_cfg)
        return merged_cfg, {
            "task_profile": task_profile,
            "requested_role": preferred_role or "auto",
            "selected_role": resolved_role,
            "selected_source": target_key,
            "reason": reason,
            "local_enabled": True,
            "allow_small_to_main_fallback": allow_small_to_main_fallback,
        }

    main_cfg = _clean_dict(local_cfg.get("main_model"))
    if resolved_role == "small" and allow_small_to_main_fallback and _is_model_target_ready(main_cfg):
        merged_cfg = _normalize_model_cfg(base_cfg, main_cfg, local_cfg)
        return merged_cfg, {
            "task_profile": task_profile,
            "requested_role": preferred_role or "auto",
            "selected_role": "main",
            "selected_source": "main_model",
            "reason": "small_missing_main_fallback",
            "local_enabled": True,
            "allow_small_to_main_fallback": True,
        }

    # 安全约束：本地模型配置缺失时，默认拒绝静默回退到云端
    if _is_model_target_ready(base_cfg) and allow_cloud_fallback:
        return dict(base_cfg), {
            "task_profile": task_profile,
            "requested_role": preferred_role or "auto",
            "selected_role": "base",
            "selected_source": "llm_config",
            "reason": f"{resolved_role}_missing_cloud_fallback",
            "local_enabled": True,
            "allow_cloud_fallback": True,
            "allow_small_to_main_fallback": allow_small_to_main_fallback,
        }

    # 本地模型不可用且禁止云端回退 → 返回不完整的本地配置 + 告警 meta
    merged_cfg = _normalize_model_cfg(base_cfg, local_target_cfg, local_cfg)
    return merged_cfg, {
        "task_profile": task_profile,
        "requested_role": preferred_role or "auto",
        "selected_role": resolved_role,
        "selected_source": target_key or "main_model",
        "reason": f"{resolved_role}_missing_no_cloud_fallback",
        "local_enabled": True,
        "allow_cloud_fallback": False,
        "allow_small_to_main_fallback": allow_small_to_main_fallback,
    }
