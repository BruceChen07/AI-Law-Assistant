import json

import pytest

from app.services.audit_validation import validate_model_json


@pytest.fixture
def evidence():
    return [
        {
            "citation_id": "law-1",
            "law_title": "中华人民共和国增值税法",
            "article_no": "第一条",
        }
    ]


@pytest.fixture
def clauses():
    return [{"clause_id": "c1"}]


def _risk(**overrides):
    risk = {
        "level": "high",
        "issue": "税率约定存在适用风险",
        "suggestion": "核对交易类型与适用税率",
        "citation_id": "law-1",
        "law_title": "中华人民共和国增值税法",
        "article_no": "第一条",
        "evidence": "合同约定税率为13%",
        "confidence": 0.9,
        "clause_id": "c1",
    }
    risk.update(overrides)
    return risk


def test_accepts_valid_empty_risk_result(evidence, clauses):
    result = validate_model_json(
        '{"summary":"未检出风险","risks":[]}',
        evidence,
        clauses,
    )

    assert result["valid"] is True
    assert result["accepted_risks"] == []


@pytest.mark.parametrize(
    "text",
    [
        "prefix {\"summary\":\"x\",\"risks\":[]}",
        "```json\n{\"summary\":\"x\",\"risks\":[]}\n```",
        "[]",
        '{"summary":[],"risks":[]}',
        '{"summary":"x","risks":{}}',
    ],
)
def test_rejects_non_strict_json_or_invalid_top_level_schema(
    text, evidence, clauses
):
    result = validate_model_json(text, evidence, clauses)

    assert result["valid"] is False
    assert result["errors"]


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"citation_id": "unknown"}, "unknown_citation_id"),
        ({"clause_id": "unknown"}, "unknown_clause_id"),
        ({"law_title": "其他法律"}, "law_title_mismatch"),
        ({"article_no": "第二条"}, "article_no_mismatch"),
    ],
)
def test_rejects_unverifiable_risk_without_failing_whole_chunk(
    overrides, reason, evidence, clauses
):
    text = json.dumps(
        {"summary": "发现候选风险", "risks": [_risk(**overrides)]},
        ensure_ascii=False,
    )

    result = validate_model_json(text, evidence, clauses)

    assert result["valid"] is True
    assert result["accepted_risks"] == []
    assert result["rejected_risks"][0]["reason"] == reason
    assert result["rejected_risks"][0]["verification_status"] == "rejected"


def test_rejects_risk_with_missing_or_wrong_typed_fields(evidence, clauses):
    missing = _risk()
    missing.pop("suggestion")
    wrong_type = _risk(confidence="high")
    text = json.dumps(
        {"summary": "invalid", "risks": [missing, wrong_type]},
        ensure_ascii=False,
    )

    result = validate_model_json(text, evidence, clauses)

    assert result["valid"] is False
    assert result["accepted_risks"] == []
    assert result["errors"] == [
        "risk_schema_invalid:0",
        "risk_type_invalid:1",
    ]
