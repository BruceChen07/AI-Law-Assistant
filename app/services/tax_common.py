"""Shared tax audit helpers for relevance checks and LLM JSON parsing."""

from typing import Any, Dict
import json
import re

_TAX_KEYWORDS = [
    "税",
    "税务",
    "税收",
    "增值税",
    "所得税",
    "企业所得税",
    "个人所得税",
    "印花税",
    "发票",
    "进项",
    "销项",
    "代扣代缴",
    "纳税",
    "完税",
    "vat",
    "cit",
    "pit",
    "tax",
    "invoice",
    "withholding",
    "withhold",
    "deduct",
    "remit",
    "levy",
]


def is_tax_related_text(value: Any) -> bool:
    """Return True if text looks tax-related by keywords or numeric tax cues."""
    text = str(value or "").strip()
    if not text:
        return False
    low = text.lower()
    if any(k in text for k in _TAX_KEYWORDS if re.search(r"[\u4e00-\u9fff]", k)):
        return True
    if any(k in low for k in _TAX_KEYWORDS if not re.search(r"[\u4e00-\u9fff]", k)):
        return True
    if re.search(r"[0-9]+(?:\.[0-9]+)?\s*%", text):
        return True
    if re.search(r"[0-9]+(?:\.[0-9]+)?\s*(?:元|万元|亿元)", text):
        return True
    if re.search(r"(?:rmb|cny|usd|\$)\s*[0-9]+", low):
        return True
    return False


def parse_llm_json_object(raw_text: Any) -> Dict[str, Any]:
    """Parse a model response into a JSON object with code-fence tolerance."""
    s = str(raw_text or "").strip()
    if not s:
        return {}

    candidates = []
    fenced = re.sub(r"^\s*```(?:json)?\s*", "", s, count=1, flags=re.IGNORECASE)
    fenced = re.sub(r"\s*```\s*$", "", fenced, count=1, flags=re.IGNORECASE).strip()
    if fenced:
        candidates.append(fenced)
        left = fenced.find("{")
        right = fenced.rfind("}")
        if left >= 0 and right > left:
            obj_text = fenced[left:right + 1].strip()
            if obj_text and obj_text != fenced:
                candidates.append(obj_text)
    if s not in candidates:
        candidates.append(s)

    for item in candidates:
        try:
            parsed = json.loads(item)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue
    return {}
