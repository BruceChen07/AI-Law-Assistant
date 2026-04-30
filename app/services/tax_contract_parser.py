import os
import re
import json
import logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from app.services.crud import (
    get_tax_contract_document,
    update_tax_contract_document_status,
    replace_contract_clauses,
)
from app.services.tax_parser import extract_regulation_text
from app.services.tax_common import is_tax_related_text, parse_llm_json_object

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


def extract_clause_entities(clause_text: str, cfg: dict = None, llm=None) -> dict:
    """
    Extract business entities from a contract clause.
    Upgraded to use LLM for structured extraction if cfg is provided,
    otherwise falls back to regex.
    """
    txt = str(clause_text or "").strip()
    if not txt:
        return {}

    if cfg and llm is not None and is_tax_related_text(txt):
        prompt = f"""
        你是一个合同实体抽取助手。请从以下合同条款中提取财税相关实体，并以JSON格式返回。
        
        需要提取的字段及格式（如果条款中没有对应信息，请返回null）：
        - taxpayer_type: 纳税人类型（如 "一般纳税人", "小规模纳税人"）
        - tax_category: 税种（如 "VAT", "CIT", "PIT"，如果中文写了"增值税"则转为"VAT"）
        - tax_rate: 税率（如 0.13, 0.09, 0.06，将百分比转为小数）
        - invoice_type: 发票类型（如 "专用发票", "普通发票", "电子发票"）
        - transaction_type: 交易类型/业务类型（如 "销售货物", "提供劳务", "租赁"）
        - amount: 金额（如 "100万元"）
        - invoice_time: 开票时点/期限（如 "30日内", "付款前"）
        - withholding_obligation: 是否有代扣代缴义务（"是" 或 "否"）
        
        合同条款：
        {txt}
        
        请仅返回纯JSON对象，不要有任何其他说明：
        """
        try:
            response, _ = llm.chat([{"role": "user", "content": prompt}], overrides={
                                   "temperature": 0.1})
            result = parse_llm_json_object(response)
            if result:
                return result
        except Exception as e:
            logger.warning(
                f"LLM entity extraction failed: {e}, falling back to regex")

    # Fallback regex extraction
    low = txt.lower()
    english_mode = _is_english_text(txt)
    amount = _extract_first(r"([0-9]+(?:\.[0-9]+)?\s*(?:元|万元|亿元))", txt)
    if not amount:
        amount = _extract_first(
            r"((?:rmb|cny|usd|\$)\s*[0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)", txt)

    # Try to convert regex tax rate to decimal to match DSL format
    tax_rate = _extract_first(r"([0-9]+(?:\.[0-9]+)?\s*%)", txt)

    invoice_type = _extract_first(
        r"(专用发票|普通发票|电子发票|增值税专用发票|vat invoice|special vat invoice|ordinary invoice|electronic invoice)", txt)
    invoice_time = _extract_first(r"([0-9]{1,3}\s*(?:日内|个工作日内|天内))", txt)
    if not invoice_time:
        invoice_time = _extract_first(
            r"(within\s*[0-9]{1,3}\s*(?:business\s*)?days?)", txt)
    withholding = ("yes" if english_mode else "是") if (
        "代扣代缴" in txt or "代扣" in txt or "withholding" in low or "withhold" in low) else ""

    tax_category = None
    if "增值税" in txt or "vat" in low:
        tax_category = "VAT"
    elif "企业所得税" in txt or "cit" in low:
        tax_category = "CIT"
    elif "个人所得税" in txt or "pit" in low:
        tax_category = "PIT"

    taxpayer_type = None
    if "一般纳税人" in txt:
        taxpayer_type = "一般纳税人"
    elif "小规模纳税人" in txt:
        taxpayer_type = "小规模纳税人"

    entities = {
        "taxpayer_type": taxpayer_type,
        "tax_category": tax_category,
        "tax_rate": tax_rate,
        "invoice_type": invoice_type,
        "amount": amount,
        "invoice_time": invoice_time,
        "withholding_obligation": withholding,
    }
    return {k: v for k, v in entities.items() if v is not None}


def enrich_contract_clauses(clauses: list[dict], cfg: dict = None, llm=None) -> list[dict]:
    def _build_item(c: dict) -> dict:
        entities = extract_clause_entities(
            c.get("clause_text", ""), cfg, llm=llm)
        item = dict(c)
        item["entities_json"] = json.dumps(entities, ensure_ascii=False)
        return item

    if not clauses:
        return []
    max_workers = 1
    if cfg:
        try:
            max_workers = int(cfg.get("tax_entity_extract_max_workers", 8))
        except Exception:
            max_workers = 8
    max_workers = max(1, min(max_workers, len(clauses)))
    if max_workers == 1:
        return [_build_item(c) for c in clauses]
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        return list(executor.map(_build_item, clauses))


def analyze_contract_document(cfg, contract_id: str, operator_id: str = "", llm=None) -> dict:
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
        clauses = enrich_contract_clauses(clauses, cfg, llm=llm)
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
