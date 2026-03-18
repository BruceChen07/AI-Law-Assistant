import pytest
from app.services.audit_tax import (
    _tax_relevance_score, _is_tax_related_text, _filter_tax_audit_result
)

def test_tax_relevance_score():
    assert _tax_relevance_score({"title": "增值税法"}) > 0
    assert _tax_relevance_score({"title": "民法典"}) == 0

def test_is_tax_related_text():
    assert _is_tax_related_text("需要缴纳企业所得税") is True
    assert _is_tax_related_text("违约金100元") is False

def test_filter_tax_audit_result():
    summary = "包含税务风险"
    opinions = ["建议修改发票条款", "重新审核合同金额"]
    risks = [
        {"type": "税务风险", "issue": "发票开具不合规", "level": "high"},
        {"type": "法律风险", "issue": "违约责任不明确", "level": "low"}
    ]
    citations = [{"citation_id": "1", "title": "增值税法"}]
    
    filtered = _filter_tax_audit_result(summary, opinions, risks, citations, "zh")
    assert len(filtered["risks"]) == 1
    assert filtered["risks"][0]["type"] == "税务风险"
