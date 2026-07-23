from app.api.routers.contracts import _extract_report_risk_items


def test_extract_report_risk_items_returns_items_from_final_report():
    report = {
        "contract_id": "doc-1",
        "risk_items": [
            {"issue_id": "r1", "issue_text": "付款条款风险"},
            "invalid-item",
        ],
    }

    items = _extract_report_risk_items(report)

    assert items == [{"issue_id": "r1", "issue_text": "付款条款风险"}]


def test_extract_report_risk_items_falls_back_to_empty_list():
    assert _extract_report_risk_items({}) == []
    assert _extract_report_risk_items({"risk_items": None}) == []
