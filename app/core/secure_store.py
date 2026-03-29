import importlib
import logging
from typing import Any, Dict

logger = logging.getLogger("law_assistant")


def _clean_text(v: Any) -> str:
    return str(v or "").strip()


def _secret_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(cfg, dict):
        return {}
    v = cfg.get("secret_store")
    return v if isinstance(v, dict) else {}


def _enabled(cfg: Dict[str, Any]) -> bool:
    s = _secret_cfg(cfg)
    return bool(s.get("enabled", True))


def _backend(cfg: Dict[str, Any]) -> str:
    s = _secret_cfg(cfg)
    return _clean_text(s.get("backend", "keyring")).lower() or "keyring"


def _service_name(cfg: Dict[str, Any]) -> str:
    s = _secret_cfg(cfg)
    return _clean_text(s.get("service_name", "ai-law-assistant")) or "ai-law-assistant"


def _llm_key_name(cfg: Dict[str, Any]) -> str:
    s = _secret_cfg(cfg)
    return _clean_text(s.get("llm_api_key_name", "llm_api_key")) or "llm_api_key"


def _keyring_module():
    try:
        return importlib.import_module("keyring")
    except Exception as e:
        logger.warning("secure_store_keyring_unavailable err=%s", str(e))
        return None


def set_llm_api_key(cfg: Dict[str, Any], api_key: str) -> bool:
    if not _enabled(cfg):
        return False
    if _backend(cfg) != "keyring":
        return False
    key = _clean_text(api_key)
    kr = _keyring_module()
    if not kr:
        return False
    service = _service_name(cfg)
    name = _llm_key_name(cfg)
    try:
        if key:
            kr.set_password(service, name, key)
            return True
        try:
            kr.delete_password(service, name)
        except Exception:
            pass
        return True
    except Exception as e:
        logger.warning(
            "secure_store_set_failed service=%s name=%s err=%s", service, name, str(e))
        return False


def get_llm_api_key(cfg: Dict[str, Any]) -> str:
    if not _enabled(cfg):
        return ""
    if _backend(cfg) != "keyring":
        return ""
    kr = _keyring_module()
    if not kr:
        return ""
    service = _service_name(cfg)
    name = _llm_key_name(cfg)
    try:
        return _clean_text(kr.get_password(service, name))
    except Exception as e:
        logger.warning(
            "secure_store_get_failed service=%s name=%s err=%s", service, name, str(e))
        return ""


def has_llm_api_key(cfg: Dict[str, Any]) -> bool:
    return bool(get_llm_api_key(cfg))


def delete_llm_api_key(cfg: Dict[str, Any]) -> bool:
    return set_llm_api_key(cfg, "")
