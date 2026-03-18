import pytest
from app.services.audit_utils import (
    _safe_float, _safe_int, _safe_bool, _normalize_risk_level,
    _build_excerpt, _normalize_citation_item, _chunk_contract_text
)

def test_safe_float():
    assert _safe_float("1.5", 0.0) == 1.5
    assert _safe_float("invalid", 2.0) == 2.0

def test_safe_int():
    assert _safe_int("42", 0) == 42
    assert _safe_int("invalid", 10) == 10

def test_safe_bool():
    assert _safe_bool("yes", False) is True
    assert _safe_bool("no", True) is False
    assert _safe_bool("invalid", True) is True

def test_normalize_risk_level():
    assert _normalize_risk_level("High") == "high"
    assert _normalize_risk_level("低风险") == "low"
    assert _normalize_risk_level("unknown") == "medium"

def test_build_excerpt():
    assert _build_excerpt("a" * 200, 10) == "a" * 10 + "…"
    assert _build_excerpt("short text", 100) == "short text"

def test_normalize_citation_item():
    item = {"title": "Law A", "content": "Article 1 content"}
    norm = _normalize_citation_item(item)
    assert norm["law_title"] == "Law A"
    assert norm["title"] == "Law A"
    assert norm["excerpt"] == "Article 1 content"

def test_chunk_contract_text():
    text = "Para 1\n\nPara 2\n\nPara 3"
    chunks = _chunk_contract_text(text, 10, 2)
    assert len(chunks) == 2
