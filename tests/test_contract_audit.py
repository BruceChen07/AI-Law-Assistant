from app.services.contract_audit_modules.result_assembler import normalize_audit_result as _normalize_audit_result
from app.services.contract_audit_modules.risk_suppression import (
    should_suppress_missing_risk as _should_suppress_missing_risk,
    build_global_tax_context as _build_global_tax_context,
    reconcile_cross_clause_conflicts as _reconcile_cross_clause_conflicts
)


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


def test_should_suppress_missing_risk_when_counter_clause_exists():
    risk = {
        "issue": "条款未明确发票类型，可能影响税务处理",
        "suggestion": "补充约定增值税发票要求",
        "evidence": "未约定开票标准",
    }
    clauses = [
        {"clause_id": "c2", "clause_text": "甲方联系人负责发送电子发票等事宜"},
        {"clause_id": "c3", "clause_text": "乙方开具增值税普通发票，税率按国家政策执行", "clause_path": "三、合同价款及支付", "page_no": 2, "paragraph_no": "1"},
    ]
    suppress, hit = _should_suppress_missing_risk(risk, clauses, current_clause_id="c2")
    assert suppress is True
    assert hit["clause_id"] == "c3"


def test_should_not_suppress_missing_risk_without_counter_clause():
    risk = {
        "issue": "未明确发票开具时点，可能带来税务争议",
        "suggestion": "建议明确开票时间",
        "evidence": "未提及开票时点",
    }
    clauses = [
        {"clause_id": "c2", "clause_text": "甲方联系人负责发送电子发票等事宜"},
        {"clause_id": "c4", "clause_text": "双方应当诚信履约"},
    ]
    suppress, hit = _should_suppress_missing_risk(risk, clauses, current_clause_id="c2")
    assert suppress is False
    assert hit == {}


def test_should_suppress_missing_risk_by_global_context_first():
    clauses = [
        {"clause_id": "c2", "clause_text": "甲方联系人负责发送电子发票等事宜"},
        {"clause_id": "c3", "clause_text": "乙方开具增值税普通发票", "clause_path": "三、合同价款及支付", "page_no": 2, "paragraph_no": "1"},
    ]
    global_ctx = _build_global_tax_context(clauses)
    risk = {
        "issue": "未明确发票类型，可能导致税务处理争议",
        "suggestion": "明确约定发票种类",
        "evidence": "未约定发票类型",
    }
    suppress, hit = _should_suppress_missing_risk(
        risk,
        clauses,
        current_clause_id="c2",
        global_tax_context=global_ctx,
    )
    assert suppress is True
    assert hit["source"] == "global_tax_context"


def test_reconcile_cross_clause_conflicts_removes_remaining_missing_risk():
    clauses = [
        {"clause_id": "c2", "clause_text": "甲方联系人负责发送电子发票等事宜"},
        {"clause_id": "c3", "clause_text": "乙方开具增值税普通发票", "clause_path": "三、合同价款及支付", "page_no": 2, "paragraph_no": "1"},
    ]
    global_ctx = _build_global_tax_context(clauses)
    risks = [
        {
            "level": "low",
            "issue": "未明确发票类型",
            "suggestion": "补充发票条款",
            "evidence": "未约定发票类型",
            "location": {"risk_id": "r1", "clause_id": "c2"},
        },
        {
            "level": "medium",
            "issue": "付款违约责任不清",
            "suggestion": "补充违约金计算方式",
            "evidence": "仅约定双方协商",
            "location": {"risk_id": "r2", "clause_id": "c4"},
        },
    ]
    kept, removed = _reconcile_cross_clause_conflicts(risks, clauses, global_ctx)
    assert len(kept) == 1
    assert kept[0]["location"]["risk_id"] == "r2"
    assert len(removed) == 1
    assert removed[0]["risk_id"] == "r1"
