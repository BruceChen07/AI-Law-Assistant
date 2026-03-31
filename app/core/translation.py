import hashlib
import logging
import os
import re
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("law_assistant")


def _clean_text(v: Any) -> str:
    return str(v or "").strip()


def _normalize_lang(v: Any, default: str = "zh") -> str:
    s = _clean_text(v).lower()
    if s.startswith("en"):
        return "en"
    if s.startswith("zh"):
        return "zh"
    return default


def _normalize_translation_model_id(model_id: str) -> str:
    mid = _clean_text(model_id)
    if not mid:
        return "tencent/HY-MT1.5-1.8B"
    if "/" in mid:
        return mid
    if mid.lower().startswith("hy-mt"):
        return f"tencent/{mid}"
    return mid


class TranslationService:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg or {}
        self._tokenizer = None
        self._model = None
        self._torch = None
        self._is_seq2seq = True
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._cache_size = int(
            self._settings().get("cache_size", 2000) or 2000)
        self._ready = False
        self._load_error = ""

    def _settings(self) -> Dict[str, Any]:
        c = self.cfg.get("translation_config")
        if isinstance(c, dict):
            return c
        return {}

    def is_enabled(self) -> bool:
        c = self._settings()
        return bool(c.get("enabled", False))

    def mode(self) -> str:
        c = self._settings()
        m = _clean_text(c.get("mode", "dual")).lower()
        return m if m in {"dual", "translate_only"} else "dual"

    def target_language(self) -> str:
        c = self._settings()
        return _normalize_lang(c.get("target_lang", "zh"), default="zh")

    def source_languages(self) -> List[str]:
        c = self._settings()
        items = c.get("source_langs", ["en"])
        if not isinstance(items, list):
            return ["en"]
        out: List[str] = []
        for it in items:
            out.append(_normalize_lang(it, default="en"))
        return list(dict.fromkeys(out))

    def should_translate(self, src_lang: str, target_lang: str) -> bool:
        if not self.is_enabled():
            return False
        src = _normalize_lang(src_lang, default="en")
        tgt = _normalize_lang(target_lang, default="zh")
        if src == tgt:
            return False
        return src in self.source_languages() and tgt == self.target_language()

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": self.is_enabled(),
            "ready": self._ready,
            "error": self._load_error,
            "model_id": self._model_id(),
            "backend": self._backend(),
        }

    def _backend(self) -> str:
        c = self._settings()
        return _clean_text(c.get("backend", "hy_mt_local")).lower() or "hy_mt_local"

    def _model_id(self) -> str:
        c = self._settings()
        model_id = _normalize_translation_model_id(
            _clean_text(c.get("model_id", "tencent/HY-MT1.5-1.8B")))
        model_dir = _clean_text(c.get("model_dir", ""))
        if model_dir:
            return model_dir
        return model_id

    def _device(self) -> str:
        c = self._settings()
        return _clean_text(c.get("device", "cpu")).lower() or "cpu"

    def _max_source_chars(self) -> int:
        c = self._settings()
        return max(80, min(int(c.get("max_source_chars", 600) or 600), 5000))

    def _max_new_tokens(self) -> int:
        c = self._settings()
        return max(16, min(int(c.get("max_new_tokens", 320) or 320), 2048))

    def _num_beams(self) -> int:
        c = self._settings()
        return max(1, min(int(c.get("num_beams", 4) or 4), 8))

    def _translate_prompt(self, text: str, src_lang: str, target_lang: str) -> str:
        backend = self._backend()
        if backend == "hy_mt_local":
            return text
        return f"Translate from {src_lang} to {target_lang}:\n{text}"

    def _cache_key(self, text: str, src_lang: str, target_lang: str) -> str:
        payload = f"{self._model_id()}|{src_lang}|{target_lang}|{text}"
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    def _cache_get(self, key: str) -> Optional[str]:
        value = self._cache.get(key)
        if value is None:
            return None
        self._cache.move_to_end(key)
        return value

    def _cache_put(self, key: str, value: str) -> None:
        if not key:
            return
        self._cache[key] = value
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    def _protect_terms(self, text: str) -> Tuple[str, Dict[str, str]]:
        terms = self._settings().get("glossary", {})
        if not isinstance(terms, dict) or not terms:
            return text, {}
        out = text
        placeholders: Dict[str, str] = {}
        idx = 0
        for src_term, dst_term in terms.items():
            src = _clean_text(src_term)
            dst = _clean_text(dst_term)
            if not src or not dst:
                continue
            token = f"__TERM_{idx}__"
            idx += 1
            if src in out:
                out = out.replace(src, token)
                placeholders[token] = dst
        return out, placeholders

    def _restore_terms(self, text: str, placeholders: Dict[str, str]) -> str:
        out = text
        for token, val in placeholders.items():
            out = out.replace(token, val)
        return out

    def _ensure_model(self) -> bool:
        if self._ready:
            return True
        if not self.is_enabled():
            return False
        backend = self._backend()
        if backend == "mock":
            self._ready = True
            self._load_error = ""
            return True
        try:
            from transformers import AutoTokenizer, AutoModelForSeq2SeqLM, AutoModelForCausalLM
            import torch
            self._torch = torch
            model_ref = self._model_id()
            local_only = os.path.isdir(model_ref)
            self._tokenizer = AutoTokenizer.from_pretrained(
                model_ref, local_files_only=local_only, trust_remote_code=True)
            try:
                self._model = AutoModelForCausalLM.from_pretrained(
                    model_ref, local_files_only=local_only, trust_remote_code=True)
                self._is_seq2seq = False
            except Exception:
                self._model = AutoModelForSeq2SeqLM.from_pretrained(
                    model_ref, local_files_only=local_only, trust_remote_code=True)
                self._is_seq2seq = True
            if self._device() == "cuda" and torch.cuda.is_available():
                self._model = self._model.to("cuda")
            self._ready = True
            self._load_error = ""
            logger.info(
                "translation_model_ready model=%s backend=%s", model_ref, backend)
            return True
        except Exception as e:
            self._ready = False
            self._load_error = str(e)
            logger.warning(
                "translation_model_not_ready backend=%s err=%s", backend, str(e))
            return False

    def _infer(self, text: str, src_lang: str, target_lang: str) -> str:
        backend = self._backend()
        if backend == "mock":
            return f"ZH::{text}"
        if not self._ensure_model():
            raise RuntimeError(
                f"translation model not ready: {self._load_error}")
        prompt = self._translate_prompt(text, src_lang, target_lang)
        tok = self._tokenizer(
            prompt,
            return_tensors="pt",
            truncation=True,
            max_length=max(32, self._max_source_chars()),
        )
        if "token_type_ids" in tok:
            del tok["token_type_ids"]
        if self._device() == "cuda" and self._torch and self._torch.cuda.is_available():
            tok = {k: v.to("cuda") for k, v in tok.items()}
        with self._torch.no_grad():
            out = self._model.generate(
                **tok,
                max_new_tokens=self._max_new_tokens(),
                num_beams=self._num_beams(),
                do_sample=False,
            )
        text_out = self._tokenizer.decode(out[0], skip_special_tokens=True)
        if not self._is_seq2seq:
            marker = "translation:"
            low = text_out.lower()
            pos = low.rfind(marker)
            if pos >= 0:
                text_out = text_out[pos + len(marker):].strip()
        return _clean_text(text_out)

    def translate_query(self, text: str, src_lang: str = "en", target_lang: str = "zh") -> Dict[str, Any]:
        raw = _clean_text(text)
        src = _normalize_lang(src_lang, default="en")
        tgt = _normalize_lang(target_lang, default="zh")
        if not raw:
            return {"ok": False, "text": "", "used_cache": False, "error": "empty text"}
        if not self.should_translate(src, tgt):
            return {"ok": False, "text": raw, "used_cache": False, "error": "disabled"}
        clipped = raw[: self._max_source_chars()]
        key = self._cache_key(clipped, src, tgt)
        hit = self._cache_get(key)
        if hit:
            return {"ok": True, "text": hit, "used_cache": True, "error": ""}
        protected, placeholders = self._protect_terms(clipped)
        try:
            translated = self._infer(protected, src, tgt)
            translated = self._restore_terms(translated, placeholders)
            translated = re.sub(r"\s+", " ", translated).strip()
            if not translated:
                return {"ok": False, "text": raw, "used_cache": False, "error": "empty translation"}
            self._cache_put(key, translated)
            return {"ok": True, "text": translated, "used_cache": False, "error": ""}
        except Exception as e:
            logger.warning(
                "translation_query_failed src=%s tgt=%s err=%s", src, tgt, str(e))
            return {"ok": False, "text": raw, "used_cache": False, "error": str(e)}
