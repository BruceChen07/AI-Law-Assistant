"""Shared tax audit helpers for relevance checks and LLM JSON parsing."""

from typing import Any, Dict
import json
from app.services.audit_utils import is_tax_related_text as _is_tax_related_text


def is_tax_related_text(value: Any) -> bool:
    """Compatibility wrapper for the unified audit_utils implementation."""
    return _is_tax_related_text(value)


def parse_llm_json_object(raw_text: Any) -> Dict[str, Any]:
    """Parse a model response into a JSON object with code-fence tolerance."""
    s = str(raw_text or "").strip()
    if not s:
        return {}

    candidates = []
    fenced = re.sub(r"^\s*```(?:json)?\s*", "", s,
                    count=1, flags=re.IGNORECASE)
    fenced = re.sub(r"\s*```\s*$", "", fenced, count=1,
                    flags=re.IGNORECASE).strip()
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
