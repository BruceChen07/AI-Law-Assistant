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


def test_attach_risk_locations_prefers_preview_manifest_real_page_no():
    audit = {
        "summary": "",
        "executive_opinion": [],
        "risk_summary": {"high": 1, "medium": 0, "low": 0},
        "risks": [
            {
                "level": "high",
                "type": "invoice",
                "issue": "保证金抵扣条款存在开票风险",
                "evidence": "华润万家有权从应付供应商的货款中扣除费用",
                "suggestion": "要求按实际结算金额开票",
                "law_reference": "",
                "citation_id": "",
            }
        ],
        "citations": [],
    }
    clauses = [
        {
            "clause_id": "c74",
            "anchor_id": "clause-c74",
            "clause_path": "9.2条",
            "page_no": 3,
            "paragraph_no": "74",
            "clause_text": "华润万家有权从应付供应商的货款中扣除费用",
        }
    ]
    preview_manifest = {
        "pages": [
            {
                "page_no": 3,
                "blocks": [{"text": "促销员必须统一着装"}],
            },
            {
                "page_no": 14,
                "blocks": [{"text": "履约保证金作为履约担保使用。华润万家有权从应付供应商的货款中扣除费用。"}],
            },
        ]
    }

    out = _attach_risk_locations(audit, clauses, preview_manifest=preview_manifest)
    location = out["risks"][0]["location"]
    assert location["clause_id"] == "c74"
    assert location["anchor_id"] == "clause-c74"
    assert location["paragraph_no"] == "74"
    assert location["page_no"] == 14
