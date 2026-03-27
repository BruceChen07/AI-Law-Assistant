import os
import logging
import numpy as np
import onnxruntime as ort
from transformers import AutoTokenizer
from typing import Dict, Any, Optional
from app.core.utils import resolve_path

logger = logging.getLogger("law_assistant")

_embed_registry: Dict[str, Dict[str, Any]] = {}
_default_embed_lang = "zh"


def _normalize_lang(lang: Optional[str], default: str = "zh") -> str:
    s = str(lang or "").strip().lower().replace("_", "-")
    if not s:
        return str(default or "zh").strip().lower()
    if s.startswith("zh"):
        return "zh"
    if s.startswith("en"):
        return "en"
    return str(default or "zh").strip().lower()


def _default_instruction(lang: str) -> str:
    return "Represent this sentence for retrieving relevant passages:" if _normalize_lang(lang, default="zh") == "en" else "为这个句子生成表示以用于检索相关文章："


def _mean_pool(last_hidden: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
    mask = attention_mask.astype(np.float32)[..., None]
    summed = (last_hidden * mask).sum(axis=1)
    denom = np.clip(mask.sum(axis=1), 1e-9, None)
    return summed / denom


def _load_one_embedder(lang: str, p: Dict[str, Any]) -> bool:
    model_path = resolve_path(str(p.get("embedding_model", "")))
    tokenizer_dir = resolve_path(str(p.get("embedding_tokenizer_dir", "")))
    if not model_path or not os.path.exists(model_path):
        logger.warning(
            "embedding_model_missing lang=%s path=%s", lang, model_path)
        return False
    if not tokenizer_dir or not os.path.exists(tokenizer_dir):
        logger.warning(
            "embedding_tokenizer_missing lang=%s path=%s", lang, tokenizer_dir)
        return False
    so = ort.SessionOptions()
    so.intra_op_num_threads = int(p.get("embedding_threads", 2))
    sess = ort.InferenceSession(model_path, sess_options=so, providers=[
                                "CPUExecutionProvider"])
    tokenizer = AutoTokenizer.from_pretrained(
        tokenizer_dir, local_files_only=True, use_fast=True)
    prof = {
        "lang": lang,
        "model_id": str(p.get("embedding_model_id", "unknown")),
        "source": str(p.get("embedding_source", "local")),
        "model_path": model_path,
        "tokenizer_dir": tokenizer_dir,
        "max_len": int(p.get("embedding_max_seq_len", 512)),
        "pooling": str(p.get("embedding_pooling", "cls")).lower(),
        "query_instruction": str(p.get("embedding_query_instruction", _default_instruction(lang))).strip(),
        "inputs": [i.name for i in sess.get_inputs()],
        "sess": sess,
        "tokenizer": tokenizer,
    }
    _embed_registry[lang] = prof
    logger.info("embedding_ready lang=%s model_id=%s source=%s model=%s",
                lang, prof["model_id"], prof["source"], model_path)
    return True


def load_embedders(cfg):
    global _default_embed_lang
    _embed_registry.clear()
    _default_embed_lang = _normalize_lang(str(cfg.get("default_language", "zh")), default="zh")
    profiles = cfg.get("embedding_profiles")
    if not isinstance(profiles, dict) or not profiles:
        profiles = {
            _default_embed_lang: {
                "embedding_model": cfg.get("embedding_model", ""),
                "embedding_tokenizer_dir": cfg.get("embedding_tokenizer_dir", ""),
                "embedding_model_id": cfg.get("embedding_model_id", "unknown"),
                "embedding_source": cfg.get("embedding_source", "local"),
                "embedding_max_seq_len": cfg.get("embedding_max_seq_len", 512),
                "embedding_pooling": cfg.get("embedding_pooling", "cls"),
                "embedding_query_instruction": cfg.get("embedding_query_instruction", _default_instruction(_default_embed_lang)),
                "embedding_threads": cfg.get("embedding_threads", 2),
            }
        }
    ok = 0
    for lang, p in profiles.items():
        if isinstance(p, dict) and _load_one_embedder(_normalize_lang(str(lang), default=_default_embed_lang), p):
            ok += 1
    return ok


def get_embed_profile(lang: Optional[str]):
    k = _normalize_lang(lang, default=_default_embed_lang or "zh")
    return _embed_registry.get(k) or _embed_registry.get(_default_embed_lang)


def get_registry_status():
    return {
        "ready": len(_embed_registry) > 0,
        "default_language": _default_embed_lang,
        "languages": list(_embed_registry.keys()),
        "registry": _embed_registry
    }


def compute_embedding(text: str, is_query: bool = False, lang: Optional[str] = None):
    prof = get_embed_profile(lang)
    if not prof:
        return None
    payload = text.strip()
    if is_query and prof["query_instruction"]:
        payload = f"{prof['query_instruction']}{payload}"
    encoded = prof["tokenizer"](
        payload, truncation=True, max_length=prof["max_len"], padding="max_length", return_tensors="np")
    feed = {}
    for k in prof["inputs"]:
        if k in encoded:
            feed[k] = encoded[k].astype(np.int64)
        elif k == "token_type_ids":
            feed[k] = np.zeros_like(encoded["input_ids"], dtype=np.int64)
    out = prof["sess"].run(None, feed)
    if not out:
        return None
    first = out[0]
    if first.ndim == 3:
        vec = _mean_pool(first.astype(np.float32), encoded.get("attention_mask", np.ones((first.shape[0], first.shape[1]), dtype=np.int64)).astype(
            np.int64))[0] if prof["pooling"] == "mean" else first[:, 0, :].astype(np.float32)[0]
    elif first.ndim == 2:
        vec = first.astype(np.float32)[0]
    else:
        return None
    norm = np.linalg.norm(vec) + 1e-9
    return vec / norm


class EmbeddingService:
    def __init__(self, default_language: str = "zh"):
        self._default_language = _normalize_lang(default_language, default="zh")
        self._registry: Dict[str, Dict[str, Any]] = {}

    def _load_one(self, lang: str, p: Dict[str, Any]) -> bool:
        model_path = resolve_path(str(p.get("embedding_model", "")))
        tokenizer_dir = resolve_path(str(p.get("embedding_tokenizer_dir", "")))
        if not model_path or not os.path.exists(model_path):
            logger.warning(
                "embedding_model_missing lang=%s path=%s", lang, model_path)
            return False
        if not tokenizer_dir or not os.path.exists(tokenizer_dir):
            logger.warning(
                "embedding_tokenizer_missing lang=%s path=%s", lang, tokenizer_dir)
            return False
        so = ort.SessionOptions()
        so.intra_op_num_threads = int(p.get("embedding_threads", 2))
        sess = ort.InferenceSession(model_path, sess_options=so, providers=[
                                    "CPUExecutionProvider"])
        tokenizer = AutoTokenizer.from_pretrained(
            tokenizer_dir, local_files_only=True, use_fast=True)
        self._registry[lang] = {
            "lang": lang,
            "model_id": str(p.get("embedding_model_id", "unknown")),
            "source": str(p.get("embedding_source", "local")),
            "model_path": model_path,
            "tokenizer_dir": tokenizer_dir,
            "max_len": int(p.get("embedding_max_seq_len", 512)),
            "pooling": str(p.get("embedding_pooling", "cls")).lower(),
            "query_instruction": str(p.get("embedding_query_instruction", _default_instruction(lang))).strip(),
            "inputs": [i.name for i in sess.get_inputs()],
            "sess": sess,
            "tokenizer": tokenizer,
        }
        return True

    def load_embedders(self, cfg):
        self._registry.clear()
        self._default_language = _normalize_lang(str(cfg.get("default_language", "zh")), default="zh")
        profiles = cfg.get("embedding_profiles") or {
            self._default_language: cfg}
        ok = 0
        for lang, p in profiles.items():
            if isinstance(p, dict) and self._load_one(_normalize_lang(str(lang), default=self._default_language), p):
                ok += 1
        return ok

    def get_embed_profile(self, lang: Optional[str]):
        k = _normalize_lang(lang, default=self._default_language or "zh")
        return self._registry.get(k) or self._registry.get(self._default_language)

    def get_registry_status(self):
        return {
            "ready": len(self._registry) > 0,
            "default_language": self._default_language,
            "languages": list(self._registry.keys()),
            "registry": self._registry,
        }

    def compute_embedding(self, text: str, is_query: bool = False, lang: Optional[str] = None):
        prof = self.get_embed_profile(lang)
        if not prof:
            return None
        payload = text.strip()
        if is_query and prof["query_instruction"]:
            payload = f"{prof['query_instruction']}{payload}"
        encoded = prof["tokenizer"](
            payload, truncation=True, max_length=prof["max_len"], padding="max_length", return_tensors="np")
        feed = {}
        for k in prof["inputs"]:
            if k in encoded:
                feed[k] = encoded[k].astype(np.int64)
            elif k == "token_type_ids":
                feed[k] = np.zeros_like(encoded["input_ids"], dtype=np.int64)
        out = prof["sess"].run(None, feed)
        if not out:
            return None
        first = out[0]
        if first.ndim == 3:
            vec = _mean_pool(first.astype(np.float32), encoded.get("attention_mask", np.ones((first.shape[0], first.shape[1]), dtype=np.int64)).astype(
                np.int64))[0] if prof["pooling"] == "mean" else first[:, 0, :].astype(np.float32)[0]
        elif first.ndim == 2:
            vec = first.astype(np.float32)[0]
        else:
            return None
        norm = np.linalg.norm(vec) + 1e-9
        return vec / norm
