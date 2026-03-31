from app.services.contract_audit_modules.result_assembler import normalize_audit_result as _normalize_audit_result
from app.services.contract_audit_modules.risk_suppression import (
    should_suppress_missing_risk as _should_suppress_missing_risk,
    build_global_tax_context as _build_global_tax_context,
    reconcile_cross_clause_conflicts as _reconcile_cross_clause_conflicts
)
from app.services.contract_audit_modules.memory_pipeline import (
    _resolve_risk_citation_id as _resolve_risk_citation_id,
    _load_llm_json_object as _load_llm_json_object,
)
from app.services.contract_audit_modules.citation_catalog import (
    build_citation_lookup as _build_citation_lookup,
)
from app.services.utils.contract_audit_utils import (
    normalize_article_no as _normalize_article_no,
    citation_match_key as _citation_match_key,
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
        {"clause_id": "c3", "clause_text": "乙方开具增值税普通发票，税率按国家政策执行",
            "clause_path": "三、合同价款及支付", "page_no": 2, "paragraph_no": "1"},
    ]
    suppress, hit = _should_suppress_missing_risk(
        risk, clauses, current_clause_id="c2")
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
    suppress, hit = _should_suppress_missing_risk(
        risk, clauses, current_clause_id="c2")
    assert suppress is False
    assert hit == {}


def test_should_suppress_missing_risk_by_global_context_first():
    clauses = [
        {"clause_id": "c2", "clause_text": "甲方联系人负责发送电子发票等事宜"},
        {"clause_id": "c3", "clause_text": "乙方开具增值税普通发票",
            "clause_path": "三、合同价款及支付", "page_no": 2, "paragraph_no": "1"},
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
        {"clause_id": "c3", "clause_text": "乙方开具增值税普通发票",
            "clause_path": "三、合同价款及支付", "page_no": 2, "paragraph_no": "1"},
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
    kept, removed = _reconcile_cross_clause_conflicts(
        risks, clauses, global_ctx)
    assert len(kept) == 1
    assert kept[0]["location"]["risk_id"] == "r2"
    assert len(removed) == 1
    assert removed[0]["risk_id"] == "r1"


def test_normalize_article_no_supports_english_article_format():
    assert _normalize_article_no("Article 470") == "第470条"
    assert _normalize_article_no("article.21") == "第21条"
    assert _normalize_article_no("470") == "第470条"
    assert _normalize_article_no("第五百条") == "第五百条"


def test_citation_match_key_aligns_english_and_chinese_law_title():
    key_en = _citation_match_key(
        "Civil Code of the People's Republic of China", "Article 470")
    key_zh = _citation_match_key("中华人民共和国民法典", "第470条")
    assert key_en == key_zh


def test_citation_match_key_aligns_vat_law_english_and_chinese_title():
    key_en = _citation_match_key(
        "Value Added Tax Law of the People's Republic of China", "Article 21")
    key_zh = _citation_match_key("中华人民共和国增值税法", "第21条")
    assert key_en == key_zh


def test_build_citation_lookup_keeps_first_when_same_law_article_appears_twice():
    evidence_items = [
        {
            "citation_id": "en:1:1:21",
            "law_title": "Value Added Tax Law of the People's Republic of China",
            "article_no": "Article 21",
            "final_score": 0.95,
        },
        {
            "citation_id": "zh:2:3:21",
            "law_title": "中华人民共和国增值税法",
            "article_no": "第21条",
            "final_score": 0.88,
        },
    ]
    lookup = _build_citation_lookup(evidence_items)
    key = _citation_match_key("中华人民共和国增值税法", "第21条")
    assert lookup[key] == "en:1:1:21"


def test_resolve_risk_citation_id_accepts_case_insensitive_id():
    out = _resolve_risk_citation_id(
        citation_id_raw="C-LAW-470",
        law_title="",
        article_no="",
        allowed_citation_ids={"c-law-470"},
        citation_lookup={},
        citation_alias_map={},
        citation_id_casefold_map={"c-law-470": "c-law-470"},
        article_citation_index={},
        evidence_by_cid={},
    )
    assert out == "c-law-470"


def test_resolve_risk_citation_id_fallbacks_by_article_and_law_title():
    out = _resolve_risk_citation_id(
        citation_id_raw="",
        law_title="Civil Code of the People's Republic of China",
        article_no="Article 470",
        allowed_citation_ids={"cid-a", "cid-b"},
        citation_lookup={},
        citation_alias_map={},
        citation_id_casefold_map={},
        article_citation_index={"第470条": ["cid-a", "cid-b"]},
        evidence_by_cid={
            "cid-a": {"law_title": "中华人民共和国民法典"},
            "cid-b": {"law_title": "中华人民共和国增值税法"},
        },
    )
    assert out == "cid-a"


def test_load_llm_json_object_accepts_markdown_fenced_json():
    raw = """```json
{
  \"summary\": \"ok\",
  \"risks\": []
}
```"""
    parsed = _load_llm_json_object(raw)
    assert parsed["summary"] == "ok"
    assert parsed["risks"] == []


def test_load_llm_json_object_accepts_wrapped_json_object():
    raw = "Result:\n{\"summary\":\"ok\",\"risks\":[]}\nThanks"
    parsed = _load_llm_json_object(raw)
    assert parsed["summary"] == "ok"
    assert parsed["risks"] == []
