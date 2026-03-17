import os
import re
import logging
from typing import List, Tuple, Optional, Dict, Any
from pypdf import PdfReader
from docx import Document
from app.core.ocr import OCREngineManager

logger = logging.getLogger("law_assistant")

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resolve_path(p: str, base_dir: str = APP_ROOT) -> str:
    if not p:
        return ""
    # Normalize path separators
    p = p.replace("\\", os.sep).replace("/", os.sep)
    if os.path.isabs(p):
        return p
    return os.path.abspath(os.path.join(base_dir, p))


def _safe_decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("gbk", errors="ignore")


def _extract_docx(path: str) -> str:
    doc = Document(path)
    lines = [p.text for p in doc.paragraphs]
    return "\n".join([l for l in lines if l is not None])


def _extract_pdf_text(path: str) -> Tuple[str, int]:
    reader = PdfReader(path)
    pages = []
    for p in reader.pages:
        pages.append(p.extract_text() or "")
    return "\n\n".join(pages), len(pages)


def extract_text(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".txt":
        with open(path, "rb") as f:
            data = f.read()
        return _safe_decode(data)
    if ext == ".docx":
        return _extract_docx(path)
    if ext == ".pdf":
        text, _ = _extract_pdf_text(path)
        return text
    raise ValueError("unsupported file type")


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

    text, page_count = _extract_pdf_text(path)
    meta["page_count"] = page_count
    logger.info("pdf_extract_done file=%s pages=%s text_length=%s", path, page_count, len(text))

    ocr_enabled = bool(cfg.get("ocr_enabled", True))
    ocr_min_len = int(cfg.get("ocr_min_text_length", 200))
    ocr_langs = str(cfg.get("ocr_languages", "chi_sim+eng"))
    ocr_dpi = int(cfg.get("ocr_dpi", 220))

    if ocr_enabled and len(text.strip()) < ocr_min_len:
        logger.info(
            "ocr_trigger file=%s text_length=%s min_len=%s langs=%s dpi=%s",
            path,
            len(text.strip()),
            ocr_min_len,
            ocr_langs,
            ocr_dpi
        )
        try:
            manager = OCREngineManager(cfg)
            ocr_text, ocr_pages, engine = manager.ocr_pdf(path, ocr_langs, ocr_dpi, doc_type="pdf")
            if ocr_text.strip():
                meta["ocr_used"] = True
                meta["ocr_engine"] = engine or ""
                meta["page_count"] = max(page_count, ocr_pages)
                logger.info(
                    "ocr_done file=%s engine=%s pages=%s text_length=%s",
                    path,
                    engine,
                    ocr_pages,
                    len(ocr_text)
                )
                return ocr_text, meta
        except Exception:
            logger.exception("ocr_failed file=%s", path)
    return text, meta


def split_articles(text):
    text = re.sub(r"\r\n", "\n", text)
    parts = re.split(r"(第[一二三四五六七八九十百千0-9]+条)", text)
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
            items.append((f"段{idx}", p))
    return items


def tokenize_query(q: str) -> List[str]:
    words = re.findall(r"[\u4e00-\u9fff]+", q)
    words += re.findall(r"[A-Za-z]+", q)
    words += re.findall(r"[0-9]+", q)
    return [w for w in words if len(w) >= 2][:10]


def best_sentence(text: str, tokens: List[str]) -> tuple[str, int]:
    sents = re.split(r"[。；;\n\r]+", text)
    best = ("", 0)
    for s in sents:
        score = sum(1 for t in tokens if t in s)
        if score > best[1]:
            best = (s.strip(), score)
    return best
