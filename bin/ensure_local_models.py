import argparse
import json
import os
import shutil
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
    for name in names:
        if os.path.exists(os.path.join(base_dir, name)):
            return True
    return False


def _has_weight_file(base_dir: str) -> bool:
    if not base_dir or not os.path.isdir(base_dir):
        return False
    if _any_exists(base_dir, WEIGHT_MARKERS):
        return True
    for root, _dirs, files in os.walk(base_dir):
        for f in files:
            low = f.lower()
            if low.endswith(".safetensors") or low.startswith("pytorch_model"):
                return True
    return False


def _embedding_default_model_id(lang: str) -> str:
    if _normalize_lang(lang, default="zh") == "en":
        return "BAAI/bge-small-en-v1.5"
    return "BAAI/bge-small-zh-v1.5"


def _normalize_translation_model_id(model_id: str) -> str:
    mid = _clean_text(model_id)
    if not mid:
        return "tencent/HY-MT1.5-1.8B"
    if "/" in mid:
        return mid
    if mid.lower().startswith("hy-mt"):
        return f"tencent/{mid}"
    return mid


def _translation_default_target(repo_root: str, model_id: str) -> str:
    safe = _normalize_translation_model_id(model_id).replace("/", "__").replace("\\", "__").replace(":", "_")
    return os.path.join(repo_root, "models", "translation", safe)


def _find_first_onnx(src_dir: str) -> str:
    out: List[str] = []
    for root, _dirs, files in os.walk(src_dir):
        for f in files:
            if f.lower().endswith(".onnx"):
                out.append(os.path.join(root, f))
    out.sort(key=lambda x: len(x))
    return out[0] if out else ""


def _copy_tree(src_dir: str, dst_dir: str) -> None:
    os.makedirs(dst_dir, exist_ok=True)
    for name in os.listdir(src_dir):
        src = os.path.join(src_dir, name)
        dst = os.path.join(dst_dir, name)
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)


def _download_snapshot_modelscope(model_id: str) -> str:
    from modelscope.hub.snapshot_download import snapshot_download
    return snapshot_download(model_id=model_id, revision="master")


def _download_embedding(profile: Dict[str, Any], lang: str) -> Tuple[bool, str]:
    model_id = _clean_text(profile.get("embedding_model_id")
                           ) or _embedding_default_model_id(lang)
    model_path = _clean_text(profile.get("embedding_model"))
    tokenizer_dir = _clean_text(profile.get("embedding_tokenizer_dir"))
    if not tokenizer_dir:
        if model_path:
            tokenizer_dir = os.path.dirname(model_path)
        else:
            return False, "embedding_tokenizer_dir is empty"
    if not model_path:
        model_path = os.path.join(tokenizer_dir, "model.onnx")
    os.makedirs(tokenizer_dir, exist_ok=True)
    os.makedirs(os.path.dirname(model_path) or tokenizer_dir, exist_ok=True)
    src_dir = _download_snapshot_modelscope(model_id)
    onnx = _find_first_onnx(src_dir)
    if not onnx:
        return False, f"no onnx file found in snapshot for {model_id}"
    shutil.copy2(onnx, model_path)
    for name in TOKENIZER_MARKERS + ["special_tokens_map.json", "config.json"]:
        src = os.path.join(src_dir, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(tokenizer_dir, name))
    return True, f"downloaded {model_id}"


def _download_reranker(target_dir: str, model_id: str) -> Tuple[bool, str]:
    if not target_dir:
        return False, "reranker target path is empty"
    os.makedirs(target_dir, exist_ok=True)
    src_dir = _download_snapshot_modelscope(model_id)
    _copy_tree(src_dir, target_dir)
    return True, f"downloaded {model_id}"


def _download_translation(target_dir: str, model_id: str) -> Tuple[bool, str]:
    if not target_dir:
        return False, "translation target path is empty"
    os.makedirs(target_dir, exist_ok=True)
    model_ref = _normalize_translation_model_id(model_id)
    from huggingface_hub import snapshot_download
    snapshot_download(
        repo_id=model_ref,
        local_dir=target_dir,
        local_dir_use_symlinks=False,
        resume_download=True,
    )
    return True, f"downloaded {model_ref} to {target_dir}"


def _embedding_items(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    profiles = cfg.get("embedding_profiles")
    if isinstance(profiles, dict) and profiles:
        out = []
        for lang, p in profiles.items():
            if not isinstance(p, dict):
                continue
            out.append({
                "type": "embedding",
                "name": f"embedding:{lang}",
                "lang": _normalize_lang(lang, default="zh"),
                "profile": p,
            })
        return out
    return [{
        "type": "embedding",
        "name": "embedding:default",
        "lang": _normalize_lang(cfg.get("default_language", "zh"), default="zh"),
        "profile": {
            "embedding_model": cfg.get("embedding_model", ""),
            "embedding_tokenizer_dir": cfg.get("embedding_tokenizer_dir", ""),
            "embedding_model_id": cfg.get("embedding_model_id", ""),
        },
    }]


def _reranker_items(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    default_id = _clean_text(cfg.get("reranker_model_id")
                             ) or "BAAI/bge-reranker-base"
    model_path = _clean_text(cfg.get("reranker_model_path"))
    if model_path:
        out.append({
            "type": "reranker",
            "name": "reranker:default",
            "path": model_path,
            "model_id": default_id,
        })
    profiles = cfg.get("reranker_profiles")
    if isinstance(profiles, dict):
        for lang, path in profiles.items():
            p = _clean_text(path)
            if not p:
                continue
            out.append({
                "type": "reranker",
                "name": f"reranker:{lang}",
                "path": p,
                "model_id": default_id,
            })
    dedup = {}
    for it in out:
        dedup[_clean_text(it.get("path"))] = it
    return list(dedup.values())


def _translation_item(cfg: Dict[str, Any], repo_root: str, include_optional: bool) -> List[Dict[str, Any]]:
    t = cfg.get("translation_config")
    if not isinstance(t, dict):
        if not include_optional:
            return []
        model_id = "tencent/HY-MT1.5-1.8B"
        model_dir = _translation_default_target(repo_root, model_id)
        return [{
            "type": "translation",
            "name": "translation:model",
            "path": model_dir,
            "model_id": model_id,
            "enabled": False,
            "reason": "translation_config missing, use default optional check",
        }]
    enabled = bool(t.get("enabled", False))
    backend = _clean_text(t.get("backend", "hy_mt_local")).lower()
    if not include_optional and not enabled:
        return []
    if backend == "mock":
        return [{
            "type": "translation",
            "name": "translation:mock",
            "skipped": True,
            "reason": "mock backend",
        }]
    model_id = _normalize_translation_model_id(
        _clean_text(t.get("model_id", "tencent/HY-MT1.5-1.8B")) or "tencent/HY-MT1.5-1.8B")
    model_dir = _clean_text(t.get("model_dir", ""))
    if not model_dir:
        model_dir = _translation_default_target(repo_root, model_id)
        t["model_dir"] = model_dir
    return [{
        "type": "translation",
        "name": "translation:model",
        "path": model_dir,
        "model_id": model_id,
        "enabled": enabled,
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
    }


def _check_reranker(path: str) -> Tuple[bool, Dict[str, Any]]:
    dir_ok = bool(path and os.path.isdir(path))
    tokenizer_ok = _any_exists(path, TOKENIZER_MARKERS)
    weight_ok = _any_exists(path, WEIGHT_MARKERS)
    config_ok = bool(path and os.path.exists(
        os.path.join(path, "config.json")))
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
    config_ok = bool(path and os.path.exists(
        os.path.join(path, "config.json")))
    return dir_ok and tokenizer_ok and weight_ok and config_ok, {
        "path": path,
        "dir_ok": dir_ok,
        "tokenizer_ok": tokenizer_ok,
        "weight_ok": weight_ok,
        "config_ok": config_ok,
    }


def _selected(model_types: Any, kind: str) -> bool:
    if model_types is None:
        return True
    if isinstance(model_types, str):
        values = [x.strip().lower()
                  for x in model_types.split(",") if x.strip()]
    elif isinstance(model_types, (list, tuple, set)):
        values = [str(x).strip().lower()
                  for x in model_types if str(x).strip()]
    else:
        values = []
    if not values:
        return True
    if "all" in values:
        return True
    return str(kind).strip().lower() in values


def ensure_models(
    cfg: Dict[str, Any] = None,
    check_only: bool = False,
    include_optional: bool = False,
    model_types: Any = None,
) -> Dict[str, Any]:
    config = cfg or get_config()
    app_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = str(Path(app_dir).parent)
    report: Dict[str, Any] = {
        "check_only": bool(check_only),
        "include_optional": bool(include_optional),
        "model_types": "all" if model_types is None else str(model_types),
        "models": [],
    }

    if _selected(model_types, "embedding"):
        for it in _embedding_items(config):
            ok, detail = _check_embedding(it["profile"])
            row = {"name": it["name"], "type": it["type"],
                   "ok": ok, "detail": detail}
            if not ok and not check_only:
                try:
                    downloaded, message = _download_embedding(
                        it["profile"], it["lang"])
                    ok2, detail2 = _check_embedding(it["profile"])
                    row["downloaded"] = downloaded
                    row["message"] = message
                    row["ok"] = ok2
                    row["detail"] = detail2
                except Exception as e:
                    row["downloaded"] = False
                    row["message"] = str(e)
            report["models"].append(row)

    if _selected(model_types, "reranker"):
        for it in _reranker_items(config):
            ok, detail = _check_reranker(it["path"])
            row = {"name": it["name"], "type": it["type"],
                   "ok": ok, "detail": detail}
            if not ok and not check_only:
                try:
                    downloaded, message = _download_reranker(
                        it["path"], it["model_id"])
                    ok2, detail2 = _check_reranker(it["path"])
                    row["downloaded"] = downloaded
                    row["message"] = message
                    row["ok"] = ok2
                    row["detail"] = detail2
                except Exception as e:
                    row["downloaded"] = False
                    row["message"] = str(e)
            report["models"].append(row)

    if _selected(model_types, "translation"):
        for it in _translation_item(config, repo_root, include_optional=bool(include_optional)):
            if it.get("skipped"):
                report["models"].append({
                    "name": it["name"],
                    "type": it["type"],
                    "ok": True,
                    "skipped": True,
                    "reason": it["reason"],
                })
                continue
            ok, detail = _check_translation(it["path"])
            row = {
                "name": it["name"],
                "type": it["type"],
                "ok": ok,
                "detail": detail,
                "enabled": it.get("enabled", False),
                "path": it["path"],
                "model_id": it["model_id"],
                "reason": it.get("reason", ""),
            }
            if not ok and not check_only:
                try:
                    downloaded, message = _download_translation(
                        it["path"], it["model_id"])
                    ok2, detail2 = _check_translation(it["path"])
                    row["downloaded"] = downloaded
                    row["message"] = message
                    row["ok"] = ok2
                    row["detail"] = detail2
                except Exception as e:
                    row["downloaded"] = False
                    row["message"] = str(e)
            report["models"].append(row)

    report["all_ready"] = all(bool(x.get("ok"))
                              for x in report["models"]) if report["models"] else True
    return report


def main():
    parser = argparse.ArgumentParser()
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
