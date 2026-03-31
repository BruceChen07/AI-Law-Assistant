import os
import re
import zipfile
import logging
import importlib
from html import unescape
from datetime import datetime
from xml.etree import ElementTree as ET
from docx import Document
from app.core.utils import extract_text_with_config
from app.core.ocr import OCREngineManager
from app.services.crud import (
    get_tax_regulation_document,
    update_tax_regulation_document_status,
    replace_tax_rules_for_document,
)

logger = logging.getLogger("law_assistant")

TAX_TYPES = [
    "增值税",
    "企业所得税",
    "个人所得税",
    "消费税",
    "印花税",
    "附加税",
    "税",
    "vat",
    "value added tax",
    "corporate income tax",
    "individual income tax",
    "stamp duty",
    "tax",
]

REGION_TOKENS = [
    "全国",
    "北京市",
    "上海市",
    "广东省",
    "深圳市",
    "china",
    "beijing",
    "shanghai",
    "guangdong",
    "shenzhen",
]


def _safe_decode(data: bytes) -> str:
    for enc in ["utf-8", "gb18030", "gbk", "latin-1"]:
        try:
            return data.decode(enc)
        except Exception:
            continue
    return data.decode("utf-8", errors="ignore")


def _strip_html(raw: str) -> str:
    s = re.sub(r"(?is)<script.*?>.*?</script>", " ", raw)
    s = re.sub(r"(?is)<style.*?>.*?</style>", " ", s)
    s = re.sub(r"(?is)<[^>]+>", "\n", s)
    s = unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{2,}", "\n", s)
    return s.strip()


def _extract_docx(path: str) -> str:
    doc = Document(path)
    lines = [str(p.text or "").strip() for p in doc.paragraphs]
    lines = [x for x in lines if x]
    return "\n".join(lines)


def _extract_xlsx(path: str) -> str:
    text_items = []
    with zipfile.ZipFile(path, "r") as zf:
        shared = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.findall(".//{*}si"):
                parts = []
                for t in si.findall(".//{*}t"):
                    parts.append(t.text or "")
                shared.append("".join(parts))
        for name in zf.namelist():
            if not name.startswith("xl/worksheets/sheet") or not name.endswith(".xml"):
                continue
            root = ET.fromstring(zf.read(name))
            for c in root.findall(".//{*}c"):
                cell_type = c.attrib.get("t", "")
                v = c.find("{*}v")
                if v is None or v.text is None:
                    continue
                val = v.text.strip()
                if not val:
                    continue
                if cell_type == "s":
                    try:
                        idx = int(val)
                        if 0 <= idx < len(shared):
                            text_items.append(shared[idx])
                    except Exception:
                        continue
                else:
                    text_items.append(val)
    return "\n".join([x for x in text_items if str(x).strip()])


def _extract_image_with_ocr(cfg, path: str) -> str:
    try:
        pytesseract = importlib.import_module("pytesseract")
        Image = importlib.import_module("PIL.Image")
        lang = str(cfg.get("ocr_languages", "chi_sim+eng"))
        img = Image.open(path)
        return str(pytesseract.image_to_string(img, lang=lang) or "")
    except Exception:
        manager = OCREngineManager(cfg)
        lang = str(cfg.get("ocr_languages", "chi_sim+eng"))
        dpi = int(cfg.get("ocr_dpi", 220))
        text, _, _ = manager.ocr_pdf(path, lang, dpi, doc_type="image")
        return str(text or "")


def extract_regulation_text(cfg, file_path: str, file_type: str) -> tuple[str, dict]:
    ext = f".{str(file_type or '').lower().lstrip('.')}"
    meta = {
        "ext": ext,
        "ocr_used": False,
        "page_count": 0,
        "ocr_engine": "",
    }
    if ext in [".pdf"]:
        text, m = extract_text_with_config(cfg, file_path)
        meta.update(m)
        return text, meta
    if ext in [".docx"]:
        return _extract_docx(file_path), meta
    if ext in [".doc", ".txt"]:
        with open(file_path, "rb") as f:
            raw = _safe_decode(f.read())
        if "<html" in raw.lower() or "<p" in raw.lower() or "<div" in raw.lower():
            return _strip_html(raw), meta
        return raw, meta
    if ext in [".xlsx"]:
        return _extract_xlsx(file_path), meta
    if ext in [".xls"]:
        with open(file_path, "rb") as f:
            raw = _safe_decode(f.read())
        return _strip_html(raw), meta
    if ext in [".png", ".jpg", ".jpeg", ".tif", ".tiff"]:
        text = _extract_image_with_ocr(cfg, file_path)
        meta["ocr_used"] = bool(text.strip())
        return text, meta
    raise ValueError("unsupported regulation file type")


def split_tax_clauses(text: str) -> list[dict]:
    normalized = re.sub(r"\r\n", "\n", str(text or ""))
    normalized = re.sub(r"\n{2,}", "\n\n", normalized)
    english_mode = len(re.findall(
        r"[A-Za-z]", normalized)) >= max(20, len(re.findall(r"[\u4e00-\u9fff]", normalized)))
    marker_pattern = r"(?m)^\s*(第[一二三四五六七八九十百千0-9]+[章节条款项]|(?:Article|Section|Chapter|Part)\s+[0-9IVXLCM]+(?:\.[0-9]+)*)"
    chunks = re.split(marker_pattern, normalized, flags=re.IGNORECASE)
    items = []
    if len(chunks) >= 3:
        i = 1
        paragraph_no = 1
        while i < len(chunks) - 1:
            mark = chunks[i].strip()
            body = chunks[i + 1].strip()
            if body:
                items.append(
                    {
                        "article_no": mark,
                        "source_text": body[:4000],
                        "source_page": max(1, paragraph_no // 4),
                        "source_paragraph": str(paragraph_no),
                    }
                )
                paragraph_no += 1
            i += 2
    if not items:
        paras = [p.strip() for p in normalized.split("\n") if p.strip()]
        for idx, p in enumerate(paras, 1):
            items.append(
                {
                    "article_no": (f"Para {idx}" if english_mode else f"段{idx}"),
                    "source_text": p[:4000],
                    "source_page": max(1, idx // 4),
                    "source_paragraph": str(idx),
                }
            )
    return items


def _extract_first(pattern: str, text: str) -> str:
    m = re.search(pattern, text or "", flags=re.IGNORECASE)
    return m.group(1).strip() if m else ""


def extract_tax_fields(clause: dict, law_title: str = "") -> dict:
    text = str(clause.get("source_text", "") or "")
    low = text.lower()
    tax_type = ""
    for t in TAX_TYPES:
        if (t in text) or (t.lower() in low):
            tax_type = t
            break
    rule_type = "general"
    if "税率" in text or "tax rate" in low or "%" in text:
        rule_type = "tax_rate"
    elif "应当" in text or "应于" in text or "shall" in low or "must" in low:
        rule_type = "mandatory_action"
    elif "不得" in text or "禁止" in text or "shall not" in low or "must not" in low or "prohibited" in low:
        rule_type = "prohibited_action"
    elif "期限" in text or "日内" in text or "月内" in text or "within" in low or "deadline" in low:
        rule_type = "deadline"
    rate = _extract_first(r"([0-9]+(?:\.[0-9]+)?\s*%)", text)
    day_limit = _extract_first(r"([0-9]{1,3}\s*日内)", text)
    if not day_limit:
        day_limit = _extract_first(
            r"([0-9]{1,3}\s*(?:business\s*)?days?)", text)
    subject = _extract_first(
        r"(纳税人|扣缴义务人|一般纳税人|小规模纳税人|taxpayer|withholding agent|general taxpayer|small-scale taxpayer)", text)
    region = ""
    for r in REGION_TOKENS:
        if r in text:
            region = r
            break
    effective_date = _extract_first(
        r"((?:20[0-9]{2}|19[0-9]{2})[年\-/\.][0-9]{1,2}[月\-/\.][0-9]{1,2}日?)", text)
    expiry_date = _extract_first(
        r"(至(?:20[0-9]{2}|19[0-9]{2})[年\-/\.][0-9]{1,2}[月\-/\.][0-9]{1,2}日?)", text)
    if not effective_date:
        effective_date = _extract_first(
            r"(effective\s+(?:from|on)\s+(?:20[0-9]{2}|19[0-9]{2})[-/\.][0-9]{1,2}[-/\.][0-9]{1,2})", text)
    if not expiry_date:
        expiry_date = _extract_first(
            r"((?:until|through)\s+(?:20[0-9]{2}|19[0-9]{2})[-/\.][0-9]{1,2}[-/\.][0-9]{1,2})", text)
    trigger_condition = subject or (
        "发生涉税交易" if ("税" in text or "tax" in low) else "")
    required_action = text[:180] if (
        "应" in text or "shall" in low or "must" in low) else ""
    prohibited_action = text[:180] if (
        "不得" in text or "禁止" in text or "shall not" in low or "must not" in low or "prohibited" in low) else ""
    numeric_constraints = rate
    deadline_constraints = day_limit
    return {
        "law_title": law_title,
        "article_no": clause.get("article_no", ""),
        "rule_type": rule_type,
        "trigger_condition": trigger_condition,
        "required_action": required_action,
        "prohibited_action": prohibited_action,
        "numeric_constraints": numeric_constraints,
        "deadline_constraints": deadline_constraints,
        "region": region,
        "industry": "",
        "effective_date": effective_date,
        "expiry_date": expiry_date,
        "source_page": int(clause.get("source_page") or 1),
        "source_paragraph": str(clause.get("source_paragraph") or ""),
        "source_text": text,
        "tax_type": tax_type,
    }


def parse_regulation_document(cfg, document_id: str, operator_id: str = "") -> dict:
    doc = get_tax_regulation_document(cfg, document_id)
    if not doc:
        raise ValueError("regulation document not found")
    if not os.path.exists(doc["file_path"]):
        raise ValueError("regulation file not found")
    update_tax_regulation_document_status(cfg, document_id, "parsing")
    started_at = datetime.utcnow().isoformat()
    logger.info(
        "tax_regulation_parse_start document_id=%s operator=%s file_type=%s file_path=%s",
        document_id,
        operator_id,
        doc.get("file_type", ""),
        doc.get("file_path", ""),
    )
    try:
        text, meta = extract_regulation_text(
            cfg, doc["file_path"], doc.get("file_type", ""))
        if not str(text or "").strip():
            logger.warning(
                "tax_regulation_parse_empty_text document_id=%s ocr_used=%s ext=%s",
                document_id,
                bool(meta.get("ocr_used")),
                meta.get("ext", ""),
            )
        clauses = split_tax_clauses(text)
        law_title = os.path.splitext(doc.get("original_filename", ""))[0]
        rules = [extract_tax_fields(c, law_title=law_title)
                 for c in clauses if c.get("source_text")]
        rules = [r for r in rules if r.get("source_text")]
        replace_tax_rules_for_document(
            cfg, document_id, rules, created_by=operator_id)
        update_tax_regulation_document_status(cfg, document_id, "done")
        logger.info(
            "tax_regulation_parse_done document_id=%s clauses=%s rules=%s ocr_used=%s page_count=%s",
            document_id,
            len(clauses),
            len(rules),
            bool(meta.get("ocr_used")),
            int(meta.get("page_count") or 0),
        )
        return {
            "document_id": document_id,
            "parse_status": "done",
            "rule_count": len(rules),
            "ocr_used": bool(meta.get("ocr_used")),
            "started_at": started_at,
            "finished_at": datetime.utcnow().isoformat(),
        }
    except Exception:
        update_tax_regulation_document_status(cfg, document_id, "failed")
        logger.exception(
            "tax_regulation_parse_failed document_id=%s", document_id)
        raise
