import os
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.core.utils import resolve_path

logger = logging.getLogger("law_assistant")


def _hub_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(cfg, dict):
        return {}
    v = cfg.get("model_hub")
    return v if isinstance(v, dict) else {}


def _dedup_keep_order(items: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for it in items:
        k = str(it or "").strip().lower()
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(k)
    return out


def get_model_source_order(cfg: Dict[str, Any], preferred_source: Optional[str] = None) -> List[str]:
    hub = _hub_cfg(cfg)
    preferred = hub.get("preferred_sources")
    if not isinstance(preferred, list) or not preferred:
        preferred = ["huggingface", "modelscope"]
    order = [str(x or "").strip().lower() for x in preferred if str(x or "").strip()]
    if preferred_source:
        src = str(preferred_source).strip().lower()
        if src:
            order = [src] + order
    order = _dedup_keep_order(order)
    if not order:
        return ["huggingface", "modelscope"]
    return order


def _enable_fallback(cfg: Dict[str, Any]) -> bool:
    hub = _hub_cfg(cfg)
    return bool(hub.get("enable_fallback", True))


def _cache_dir(cfg: Dict[str, Any]) -> str:
    hub = _hub_cfg(cfg)
    cache_dir = str(hub.get("cache_dir", "../models/cache")).strip()
    out = resolve_path(cache_dir)
    os.makedirs(out, exist_ok=True)
    return out


def _map_model_id(cfg: Dict[str, Any], source: str, model_id: str) -> str:
    hub = _hub_cfg(cfg)
    mapping = hub.get("model_mirror_map")
    if not isinstance(mapping, dict):
        return model_id
    entry = mapping.get(model_id)
    if not isinstance(entry, dict):
        return model_id
    mapped = entry.get(source)
    if not mapped:
        return model_id
    return str(mapped).strip() or model_id


def _download_from_huggingface(model_id: str, revision: str, cache_dir: str) -> str:
    from huggingface_hub import snapshot_download as hf_snapshot_download
    return str(hf_snapshot_download(repo_id=model_id, revision=revision or None, cache_dir=cache_dir))


def _download_from_modelscope(model_id: str, revision: str, cache_dir: str) -> str:
    from modelscope.hub.snapshot_download import snapshot_download as ms_snapshot_download
    return str(ms_snapshot_download(model_id=model_id, revision=revision or "master", cache_dir=cache_dir))


def _attempt_download(source: str, model_id: str, revision: str, cache_dir: str) -> Tuple[str, str]:
    if source == "huggingface":
        return _download_from_huggingface(model_id, revision, cache_dir), source
    if source == "modelscope":
        return _download_from_modelscope(model_id, revision, cache_dir), source
    raise RuntimeError(f"unsupported source: {source}")


def resolve_model_path(
    cfg: Dict[str, Any],
    model_ref: str,
    task: str = "",
    revision: str = "",
    preferred_source: Optional[str] = None
) -> str:
    ref = str(model_ref or "").strip()
    if not ref:
        return ""
    local_path = resolve_path(ref)
    if os.path.exists(local_path):
        return local_path

    order = get_model_source_order(cfg, preferred_source=preferred_source)
    if not _enable_fallback(cfg) and order:
        order = order[:1]
    cache_dir = _cache_dir(cfg)
    errors: List[str] = []
    for source in order:
        mapped_id = _map_model_id(cfg, source, ref)
        try:
            path, used_source = _attempt_download(source, mapped_id, revision, cache_dir)
            if path and os.path.exists(path):
                logger.info(
                    "model_hub_resolved task=%s model_ref=%s source=%s mapped_id=%s path=%s",
                    task,
                    ref,
                    used_source,
                    mapped_id,
                    path
                )
                return path
            errors.append(f"{source}:empty_path")
        except Exception as e:
            errors.append(f"{source}:{e}")
            logger.warning(
                "model_hub_download_failed task=%s model_ref=%s source=%s mapped_id=%s err=%s",
                task,
                ref,
                source,
                mapped_id,
                str(e)
            )
    raise RuntimeError(f"failed to resolve model '{ref}' for task '{task}', tried={order}, errors={errors}")
