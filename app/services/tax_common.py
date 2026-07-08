"""Shared tax audit helpers for relevance checks and LLM JSON parsing."""

from typing import Any, Dict
from app.services.audit_utils import is_tax_related_text as _is_tax_related_text
from app.services.json_guard import parse_json_object


def is_tax_related_text(value: Any) -> bool:
    """Compatibility wrapper for the unified audit_utils implementation."""
    return _is_tax_related_text(value)


def parse_llm_json_object(raw_text: Any) -> Dict[str, Any]:
    """Parse a model response into a JSON object with tolerant repair."""
    return parse_json_object(raw_text)
