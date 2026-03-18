from app.services.contract_audit import _normalize_audit_result


def test_normalize_audit_result_recompute_risk_summary():
    parsed = {
        "summary": "ok",
        "risk_summary": {"high": 1, "medium": 1, "low": 1},
        "risks": [
            {"level": "high", "type": "税务", "issue": "A"},
            {"level": "high", "type": "税务", "issue": "B"},
            {"level": "low", "type": "法律", "issue": "C"},
        ],
        "citations": [],
    }
    out = _normalize_audit_result(parsed, "raw", [], "zh", tax_only=False)
    assert out["risk_summary"]["high"] == 2
    assert out["risk_summary"]["medium"] == 0
    assert out["risk_summary"]["low"] == 1
