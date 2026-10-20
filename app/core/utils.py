import os
import re
import logging
import threading
import jieba
from typing import List, Tuple, Optional, Dict, Any
from app.core.config import get_config
from app.services.document_text_extractors import (
    extract_docx_text,
    extract_pdf_text,
    extract_text_from_extension,
    safe_decode,
)

logger = logging.getLogger("law_assistant")


class OCRExtractionError(RuntimeError):
    """Raised when OCR is required for PDF extraction but OCR pipeline fails."""


APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_JIEBA_READY = False
_JIEBA_LOCK = threading.Lock()
_JIEBA_HMM_ENABLED = False


def resolve_path(p: str, base_dir: str = APP_ROOT) -> str:
    if not p:
        return ""
    # Normalize path separators
    p = p.replace("\\", os.sep).replace("/", os.sep)
    if os.path.isabs(p):
        return p
    return os.path.abspath(os.path.join(base_dir, p))


def _safe_decode(data: bytes) -> str:
    return safe_decode(data)


def _extract_docx(path: str) -> str:
    return extract_docx_text(path)


def _extract_pdf_text(path: str) -> Tuple[str, int]:
    return extract_pdf_text(path)


def extract_text(path: str) -> str:
    return extract_text_from_extension(path)


def extract_text_with_config(cfg: Dict[str, Any], path: str) -> Tuple[str, Dict[str, Any]]:
    ext = os.path.splitext(path)[1].lower()
    meta = {
        "ext": ext,
        "ocr_used": False,
        "page_count": 0,
        "ocr_engine": ""
    }
    if ext == ".txt":
        with open(path, "rb") as f:
            data = f.read()
        return _safe_decode(data), meta
    if ext == ".docx":
        return _extract_docx(path), meta
    if ext != ".pdf":
        raise ValueError("unsupported file type")

    from app.services.pdf_pipeline import extract_pdf_text_with_pipeline

    try:
        text, pdf_meta = extract_pdf_text_with_pipeline(
            cfg,
            path,
            raise_on_ocr_failure=False,
        )
        merged_meta = dict(meta)
        merged_meta.update(pdf_meta or {})
        logger.info(
            "pdf_pipeline_extract_done file=%s pages=%s text_length=%s backend=%s route=%s",
            path,
            merged_meta.get("page_count"),
            len(text),
            merged_meta.get("pdf_backend"),
            merged_meta.get("route"),
        )
        if bool(merged_meta.get("ocr_used")) and not str(text or "").strip():
            raise OCRExtractionError("OCR_PARSE_FAILED: OCR returned empty text for scanned PDF")
        if not str(text or "").strip() and bool(cfg.get("ocr_enabled", True)):
            raise OCRExtractionError("OCR_PARSE_FAILED: PDF text extraction returned empty text")
        return text, merged_meta
    except OCRExtractionError:
        raise
    except Exception as e:
        logger.exception("pdf_pipeline_extract_failed file=%s", path)
        raise OCRExtractionError(
            f"OCR_PARSE_FAILED: {type(e).__name__}: {e}"
        ) from e


def split_articles(text):
    text = re.sub(r"\r\n", "\n", text)
    english_mode = len(re.findall(r"[A-Za-z]", text)) >= max(
        20, len(re.findall(r"[\u4e00-\u9fff]", text)))
    parts = re.split(
        r"(?m)^\s*(第[一二三四五六七八九十百千0-9]+条|(?:Article|Section|Chapter|Part)\s+[0-9IVXLCM]+(?:\.[0-9]+)*)",
        text,
        flags=re.IGNORECASE,
    )
    items = []
    if len(parts) >= 3:
        i = 1
        while i < len(parts) - 1:
            article_no = parts[i].strip()
            content = parts[i + 1].strip()
            if content:
                items.append((article_no, content))
            i += 2
    if not items:
        paras = [p.strip() for p in text.split("\n") if p.strip()]
        for idx, p in enumerate(paras, 1):
            items.append(((f"Para {idx}" if english_mode else f"段{idx}"), p))
    return items


def _init_jieba() -> None:
    global _JIEBA_READY, _JIEBA_HMM_ENABLED
    if _JIEBA_READY:
        return
    with _JIEBA_LOCK:
        if _JIEBA_READY:
            return
        cfg = get_config()
        _JIEBA_HMM_ENABLED = bool(cfg.get("jieba_hmm_enabled", False))
        raw_path = str(cfg.get("jieba_user_dict_path", "../data/dict/law_dict.txt") or "").strip()
        if raw_path:
            dict_path = resolve_path(raw_path, APP_ROOT)
            if os.path.exists(dict_path):
                jieba.load_userdict(dict_path)
                logger.info("jieba_user_dict_loaded path=%s", dict_path)
            else:
                logger.warning("jieba_user_dict_missing path=%s", dict_path)
        force_words = cfg.get("jieba_force_words", [])
        if isinstance(force_words, list):
            for item in force_words:
                word = str(item or "").strip()
                if word:
                    jieba.suggest_freq(word, tune=True)
        _JIEBA_READY = True


def tokenize_text_for_fts(text: str) -> str:
    """Tokenize text using jieba for SQLite FTS indexing."""
    if not text:
        return ""
    _init_jieba()
    return " ".join(jieba.cut(text, HMM=_JIEBA_HMM_ENABLED))


def tokenize_query(q: str) -> List[str]:
    if not q.strip():
        return []
    _init_jieba()
    words = list(jieba.cut_for_search(q, HMM=_JIEBA_HMM_ENABLED))
    res = []
    for w in words:
        w = w.strip()
        if len(w) >= 2 or (len(w) == 1 and w.isalnum()):
            res.append(w)
    return res[:15]


def best_sentence(text: str, tokens: List[str]) -> tuple[str, int]:
    sents = re.split(r"[。！？!?；;.\n\r]+", text)
    normalized_tokens = [(str(t), str(t).lower()) for t in tokens]
    best = ("", 0)
    for s in sents:
        s_clean = s.strip()
        if not s_clean:
            continue
        s_low = s_clean.lower()
        score = sum(1 for t_raw, t_low in normalized_tokens if (
            t_raw in s_clean or t_low in s_low))
        if score > best[1]:
            best = (s_clean, score)
    return best
