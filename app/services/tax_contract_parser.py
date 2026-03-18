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


def split_contract_clauses(text: str) -> list[dict]:
    normalized = re.sub(r"\r\n", "\n", str(text or ""))
    lines = [x.strip() for x in normalized.split("\n") if x.strip()]
    clauses = []
    current_path = ""
    page_no = 1
    paragraph_no = 1
    for ln in lines:
        m = re.match(
            r"^((?:第[一二三四五六七八九十百千0-9]+[章节条款]|[0-9]+(?:\.[0-9]+){0,3}|[一二三四五六七八九十]+、))\s*(.*)$", ln)
        if m:
            current_path = m.group(1).strip()
            body = m.group(2).strip()
            text_value = body if body else ln
        else:
            text_value = ln
        clauses.append(
            {
                "clause_path": current_path or f"段{paragraph_no}",
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
    amount = _extract_first(r"([0-9]+(?:\.[0-9]+)?\s*(?:元|万元|亿元))", txt)
    tax_rate = _extract_first(r"([0-9]+(?:\.[0-9]+)?\s*%)", txt)
    invoice_type = _extract_first(r"(专用发票|普通发票|电子发票|增值税专用发票)", txt)
    invoice_time = _extract_first(r"([0-9]{1,3}\s*(?:日内|个工作日内|天内))", txt)
    withholding = "是" if ("代扣代缴" in txt or "代扣" in txt) else ""
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
