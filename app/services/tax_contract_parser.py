import os
import re
import json
import logging
from datetime import datetime
from app.services.crud import (
    get_tax_contract_document,
    update_tax_contract_document_status,
    replace_contract_clauses,
)
from app.services.tax_parser import extract_regulation_text

logger = logging.getLogger("law_assistant")


def _is_english_text(text: str) -> bool:
    s = str(text or "")
    latin = len(re.findall(r"[A-Za-z]", s))
    cjk = len(re.findall(r"[\u4e00-\u9fff]", s))
    if latin <= 0:
        return False
    return latin >= max(30, int(cjk * 1.2))


def detect_text_language(text: str, default: str = "zh") -> str:
    return "en" if _is_english_text(text) else str(default or "zh")


def split_contract_clauses(text: str) -> list[dict]:
    normalized = re.sub(r"\r\n", "\n", str(text or ""))
    lines = [x.strip() for x in normalized.split("\n") if x.strip()]
    clauses = []
    current_path = ""
    page_no = 1
    paragraph_no = 1
    english_mode = _is_english_text(normalized)
    heading_pattern = (
        r"^((?:"
        r"第[一二三四五六七八九十百千0-9]+[章节条款]"
        r"|[0-9]+(?:\.[0-9]+){0,3}"
        r"|[一二三四五六七八九十]+、"
        r"|(?:Article|Section|Chapter|Part)\s+[0-9IVXLCM]+(?:\.[0-9]+)*"
        r"|(?:Clause)\s+[0-9]+(?:\.[0-9]+)*"
        r"|(?:\([a-zA-Z]\)|[a-zA-Z][\.\)])"
        r"))\s*(.*)$"
    )
    for ln in lines:
        m = re.match(heading_pattern, ln, flags=re.IGNORECASE)
        if m:
            current_path = m.group(1).strip()
            body = m.group(2).strip()
            text_value = body if body else ln
        else:
            text_value = ln
        clauses.append(
            {
                "clause_path": current_path or (f"Para {paragraph_no}" if english_mode else f"段{paragraph_no}"),
                "page_no": page_no,
                "paragraph_no": str(paragraph_no),
                "clause_text": text_value[:4000],
            }
        )
        paragraph_no += 1
        if paragraph_no % 35 == 0:
            page_no += 1
    return clauses


def _extract_first(pattern: str, text: str) -> str:
    m = re.search(pattern, text or "", flags=re.IGNORECASE)
    return m.group(1).strip() if m else ""


def extract_clause_entities(clause_text: str) -> dict:
    txt = str(clause_text or "")
    low = txt.lower()
    english_mode = _is_english_text(txt)
    amount = _extract_first(r"([0-9]+(?:\.[0-9]+)?\s*(?:元|万元|亿元))", txt)
    if not amount:
        amount = _extract_first(
            r"((?:rmb|cny|usd|\$)\s*[0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)", txt)
    tax_rate = _extract_first(r"([0-9]+(?:\.[0-9]+)?\s*%)", txt)
    invoice_type = _extract_first(
        r"(专用发票|普通发票|电子发票|增值税专用发票|vat invoice|special vat invoice|ordinary invoice|electronic invoice)", txt)
    invoice_time = _extract_first(r"([0-9]{1,3}\s*(?:日内|个工作日内|天内))", txt)
    if not invoice_time:
        invoice_time = _extract_first(
            r"(within\s*[0-9]{1,3}\s*(?:business\s*)?days?)", txt)
    withholding = ("yes" if english_mode else "是") if (
        "代扣代缴" in txt or "代扣" in txt or "withholding" in low or "withhold" in low) else ""
    entities = {
        "amount": amount,
        "tax_rate": tax_rate,
        "invoice_type": invoice_type,
        "invoice_time": invoice_time,
        "withholding_obligation": withholding,
    }
    return entities


def enrich_contract_clauses(clauses: list[dict]) -> list[dict]:
    result = []
    for c in clauses:
        entities = extract_clause_entities(c.get("clause_text", ""))
        item = dict(c)
        item["entities_json"] = json.dumps(entities, ensure_ascii=False)
        result.append(item)
    return result


def analyze_contract_document(cfg, contract_id: str, operator_id: str = "") -> dict:
    doc = get_tax_contract_document(cfg, contract_id)
    if not doc:
        raise ValueError("contract document not found")
    path = doc.get("file_path", "")
    if not path or not os.path.exists(path):
        raise ValueError("contract file not found")
    update_tax_contract_document_status(cfg, contract_id, "parsing")
    started_at = datetime.utcnow().isoformat()
    logger.info(
        "tax_contract_analyze_start contract_id=%s operator=%s file_type=%s file_path=%s",
        contract_id,
        operator_id,
        doc.get("file_type", ""),
        path,
    )
    try:
        text, meta = extract_regulation_text(
            cfg, path, doc.get("file_type", ""))
        if not str(text or "").strip():
            logger.warning(
                "tax_contract_analyze_empty_text contract_id=%s ocr_used=%s ext=%s",
                contract_id,
                bool(meta.get("ocr_used")),
                meta.get("ext", ""),
            )
        clauses = split_contract_clauses(text)
        clauses = enrich_contract_clauses(clauses)
        replace_contract_clauses(
            cfg, contract_id, clauses, created_by=operator_id)
        update_tax_contract_document_status(
            cfg, contract_id, "done", ocr_used=1 if meta.get("ocr_used") else 0)
        logger.info(
            "tax_contract_analyze_done contract_id=%s clauses=%s ocr_used=%s page_count=%s",
            contract_id,
            len(clauses),
            bool(meta.get("ocr_used")),
            int(meta.get("page_count") or 0),
        )
        return {
            "contract_id": contract_id,
            "parse_status": "done",
            "language": detect_text_language(text, default="zh"),
            "clause_count": len(clauses),
            "ocr_used": bool(meta.get("ocr_used")),
            "started_at": started_at,
            "finished_at": datetime.utcnow().isoformat(),
        }
    except Exception:
        update_tax_contract_document_status(cfg, contract_id, "failed")
        logger.exception(
            "tax_contract_analyze_failed contract_id=%s", contract_id)
        raise
