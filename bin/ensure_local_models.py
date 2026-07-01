import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

try:
    from app.core.config import get_config
except ModuleNotFoundError:
    _CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
    _REPO_ROOT = os.path.dirname(_CURRENT_DIR)
    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)
    from app.core.config import get_config


TOKENIZER_MARKERS = [
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.txt",
    "vocab.json",
    "merges.txt",
    "sentencepiece.bpe.model",
]

WEIGHT_MARKERS = [
    "model.safetensors",
    "pytorch_model.bin",
    "pytorch_model.bin.index.json",
]


def _clean_text(v: Any) -> str:
    return str(v or "").strip()


def _normalize_lang(v: Any, default: str = "zh") -> str:
    s = _clean_text(v).lower()
    if s.startswith("en"):
        return "en"
    if s.startswith("zh"):
        return "zh"
    return default


def _any_exists(base_dir: str, names: List[str]) -> bool:
    if not base_dir or not os.path.isdir(base_dir):
        return False
    return any(os.path.exists(os.path.join(base_dir, name)) for name in names)


def _has_weight_file(base_dir: str) -> bool:
    if not base_dir or not os.path.isdir(base_dir):
        return False
    if _any_exists(base_dir, WEIGHT_MARKERS):
        return True
    for root, _dirs, files in os.walk(base_dir):
        for file_name in files:
            low = file_name.lower()
            if low.endswith(".safetensors") or low.startswith("pytorch_model"):
                return True
    return False


def _selected(model_types: Any, kind: str) -> bool:
    if model_types is None:
        return True
    if isinstance(model_types, str):
        values = [x.strip().lower() for x in model_types.split(",") if x.strip()]
    elif isinstance(model_types, (list, tuple, set)):
        values = [str(x).strip().lower() for x in model_types if str(x).strip()]
    else:
        values = []
    if not values or "all" in values:
        return True
    return str(kind).strip().lower() in values


def _embedding_items(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    profiles = cfg.get("embedding_profiles")
    if isinstance(profiles, dict) and profiles:
        return [
            {
                "type": "embedding",
                "name": f"embedding:{lang}",
                "lang": _normalize_lang(lang, default="zh"),
                "profile": profile,
            }
            for lang, profile in profiles.items()
            if isinstance(profile, dict)
        ]
    return []


def _reranker_items(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    model_path = _clean_text(cfg.get("reranker_model_path"))
    if model_path:
        out.append({"type": "reranker", "name": "reranker:default", "path": model_path})
    profiles = cfg.get("reranker_profiles")
    if isinstance(profiles, dict):
        for lang, path in profiles.items():
            if _clean_text(path):
                out.append({"type": "reranker", "name": f"reranker:{lang}", "path": _clean_text(path)})
    dedup: Dict[str, Dict[str, Any]] = {}
    for item in out:
        dedup[_clean_text(item["path"])] = item
    return list(dedup.values())


def _translation_items(cfg: Dict[str, Any], include_optional: bool) -> List[Dict[str, Any]]:
    t_cfg = cfg.get("translation_config")
    if not isinstance(t_cfg, dict):
        return []
    enabled = bool(t_cfg.get("enabled", False))
    backend = _clean_text(t_cfg.get("backend", "hy_mt_local")).lower()
    if backend == "mock":
        return [{
            "type": "translation",
            "name": "translation:mock",
            "skipped": True,
            "reason": "mock backend",
        }]
    if not enabled and not include_optional:
        return []
    return [{
        "type": "translation",
        "name": "translation:model",
        "path": _clean_text(t_cfg.get("model_dir")),
        "enabled": enabled,
        "reason": "" if enabled else "translation disabled, checked as optional",
    }]


def _check_embedding(profile: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    model_path = _clean_text(profile.get("embedding_model"))
    tokenizer_dir = _clean_text(profile.get("embedding_tokenizer_dir"))
    model_ok = bool(model_path and os.path.exists(model_path))
    tokenizer_ok = _any_exists(tokenizer_dir, TOKENIZER_MARKERS)
    return model_ok and tokenizer_ok, {
        "model_path": model_path,
        "tokenizer_dir": tokenizer_dir,
        "model_ok": model_ok,
        "tokenizer_ok": tokenizer_ok,
        "source": _clean_text(profile.get("embedding_source")),
    }


def _check_reranker(path: str) -> Tuple[bool, Dict[str, Any]]:
    dir_ok = bool(path and os.path.isdir(path))
    tokenizer_ok = _any_exists(path, TOKENIZER_MARKERS)
    weight_ok = _has_weight_file(path)
    config_ok = bool(path and os.path.exists(os.path.join(path, "config.json")))
    return dir_ok and tokenizer_ok and weight_ok and config_ok, {
        "path": path,
        "dir_ok": dir_ok,
        "tokenizer_ok": tokenizer_ok,
        "weight_ok": weight_ok,
        "config_ok": config_ok,
    }


def _check_translation(path: str) -> Tuple[bool, Dict[str, Any]]:
    dir_ok = bool(path and os.path.isdir(path))
    tokenizer_ok = _any_exists(path, TOKENIZER_MARKERS)
    weight_ok = _has_weight_file(path)
    config_ok = bool(path and os.path.exists(os.path.join(path, "config.json")))
    return dir_ok and tokenizer_ok and weight_ok and config_ok, {
        "path": path,
        "dir_ok": dir_ok,
        "tokenizer_ok": tokenizer_ok,
        "weight_ok": weight_ok,
        "config_ok": config_ok,
    }


def ensure_models(
    cfg: Dict[str, Any] = None,
    check_only: bool = False,
    include_optional: bool = False,
    model_types: Any = None,
) -> Dict[str, Any]:
    config = cfg or get_config()
    report: Dict[str, Any] = {
        "check_only": bool(check_only),
        "include_optional": bool(include_optional),
        "model_types": "all" if model_types is None else str(model_types),
        "mode": "offline_validation_only",
        "models": [],
    }

    if _selected(model_types, "embedding"):
        for item in _embedding_items(config):
            ok, detail = _check_embedding(item["profile"])
            report["models"].append({
                "name": item["name"],
                "type": item["type"],
                "ok": ok,
                "detail": detail,
                "downloaded": False,
                "message": "" if ok else "embedding assets must be staged from internal artifact repository",
            })

    if _selected(model_types, "reranker"):
        for item in _reranker_items(config):
            ok, detail = _check_reranker(item["path"])
            report["models"].append({
                "name": item["name"],
                "type": item["type"],
                "ok": ok,
                "detail": detail,
                "downloaded": False,
                "message": "" if ok else "reranker assets must be staged from internal artifact repository",
            })

    if _selected(model_types, "translation"):
        for item in _translation_items(config, include_optional=bool(include_optional)):
            if item.get("skipped"):
                report["models"].append({
                    "name": item["name"],
                    "type": item["type"],
                    "ok": True,
                    "skipped": True,
                    "reason": item["reason"],
                })
                continue
            ok, detail = _check_translation(item["path"])
            report["models"].append({
                "name": item["name"],
                "type": item["type"],
                "ok": ok,
                "detail": detail,
                "enabled": bool(item.get("enabled", False)),
                "downloaded": False,
                "reason": item.get("reason", ""),
                "message": "" if ok else "translation assets must be staged from internal artifact repository",
            })

    report["all_ready"] = all(bool(x.get("ok")) for x in report["models"]) if report["models"] else True
    return report


def main():
    parser = argparse.ArgumentParser(
        description="Validate that all model assets required by the enterprise offline profile exist locally."
    )
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--include-optional", action="store_true")
    parser.add_argument("--types", default="all")
    args = parser.parse_args()
    report = ensure_models(
        cfg=get_config(),
        check_only=bool(args.check_only),
        include_optional=bool(args.include_optional),
        model_types=args.types,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
