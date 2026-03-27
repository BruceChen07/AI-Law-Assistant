import os
import json
from functools import lru_cache


def _resolve_path(base_dir: str, value):
    if not isinstance(value, str) or not value:
        return value
    value = value.replace("\\", os.sep).replace("/", os.sep)
    if os.path.isabs(value):
        return value
    return os.path.abspath(os.path.join(base_dir, value))


def _normalize_paths(cfg: dict, base_dir: str):
    path_keys = [
        "data_dir",
        "db_path",
        "files_dir",
        "static_dir",
        "log_dir",
        "log_base_dir",
        "embedding_model",
        "embedding_tokenizer_dir",
        "reranker_model_path",
    ]
    normalized = dict(cfg)
    for key in path_keys:
        normalized[key] = _resolve_path(base_dir, normalized.get(key))
    profiles = normalized.get("embedding_profiles")
    if isinstance(profiles, dict):
        normalized_profiles = {}
        for lang, profile in profiles.items():
            if isinstance(profile, dict):
                p = dict(profile)
                p["embedding_model"] = _resolve_path(
                    base_dir, p.get("embedding_model"))
                p["embedding_tokenizer_dir"] = _resolve_path(
                    base_dir, p.get("embedding_tokenizer_dir"))
                normalized_profiles[lang] = p
            else:
                normalized_profiles[lang] = profile
        normalized["embedding_profiles"] = normalized_profiles

    reranker_profiles = normalized.get("reranker_profiles")
    if isinstance(reranker_profiles, dict):
        normalized_reranker = {}
        for lang, path in reranker_profiles.items():
            normalized_reranker[lang] = _resolve_path(base_dir, path)
        normalized["reranker_profiles"] = normalized_reranker
    return normalized


@lru_cache()
def load_config(base_dir: str = None):
    if base_dir is None:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_path = os.environ.get(
        "APP_CONFIG", os.path.join(base_dir, "config.json"))
    if not os.path.exists(config_path):
        alt = os.path.join(base_dir, "config.example.json")
        if os.path.exists(alt):
            with open(alt, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                return _normalize_paths(cfg, os.path.dirname(os.path.abspath(alt)))
        raise RuntimeError(
            f"config.json not found at {config_path} and no example at {alt}")
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
        return _normalize_paths(cfg, os.path.dirname(os.path.abspath(config_path)))


def get_config(base_dir: str = None):
    return load_config(base_dir)


def ensure_dirs(cfg):
    for key in ["data_dir", "files_dir", "static_dir"]:
        d = cfg.get(key)
        if d and not os.path.exists(d):
            os.makedirs(d, exist_ok=True)


def get_config_path(base_dir: str = None) -> str:
    if base_dir is None:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.environ.get("APP_CONFIG", os.path.join(base_dir, "config.json"))


def save_config(cfg: dict, base_dir: str = None):
    path = get_config_path(base_dir)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=4)
    load_config.cache_clear()


def update_config_patch(patch: dict, base_dir: str = None) -> dict:
    path = get_config_path(base_dir)
    if not os.path.exists(path):
        raise RuntimeError(f"config.json not found at {path}")
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    for k, v in patch.items():
        cfg[k] = v
    save_config(cfg, base_dir)
    return _normalize_paths(cfg, os.path.dirname(os.path.abspath(path)))
