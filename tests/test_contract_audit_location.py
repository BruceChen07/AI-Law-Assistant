from app.services.contract_audit import _build_preview_clauses, _attach_risk_locations


def test_build_preview_clauses_generates_anchor_fields():
    text = "第一条 甲方应在10日内开具增值税专用发票\n第二条 乙方承担代扣代缴义务"
    clauses = _build_preview_clauses(text)
    assert len(clauses) == 2
    assert clauses[0]["clause_id"] == "c1"
    assert clauses[0]["anchor_id"] == "clause-c1"
    assert clauses[1]["clause_id"] == "c2"
    assert clauses[1]["anchor_id"] == "clause-c2"


def test_attach_risk_locations_matches_clause_by_evidence():
    audit = {
        "summary": "",
        "executive_opinion": [],
        "risk_summary": {"high": 1, "medium": 0, "low": 0},
        "risks": [
            {
                "level": "high",
                "type": "tax",
                "issue": "发票义务不明确",
                "evidence": "甲方应在10日内开具增值税专用发票",
                "suggestion": "补充开票时间",
                "law_reference": "",
                "citation_id": "",
            }
        ],
        "citations": [],
    }
    clauses = _build_preview_clauses(
        "第一条 甲方应在10日内开具增值税专用发票\n第二条 双方按月结算"
    )
    out = _attach_risk_locations(audit, clauses)
    location = out["risks"][0]["location"]
    assert location["clause_id"] == "c1"
    assert location["anchor_id"] == "clause-c1"
    assert location["page_no"] == 1
    assert location["paragraph_no"] == "1"
