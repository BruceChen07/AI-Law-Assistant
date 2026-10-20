from app.services import contract_audit as ca
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


def test_build_contract_summary_returns_formatted_text_when_enabled():
    class SummaryThenAuditLLM:
        def chat(self, messages, overrides=None):
            return (
                "合同内容简要摘要\n\n税务审核师快速阅读版\n\n共 1 份合同 / 补充协议\n\n使用说明\n\n本文件仅用于帮助审核人员迅速了解合同交易背景、核心商业安排及主要涉税关注点，不替代对合同原文、附件及实际业务流程的完整审核。\n\n1. demo\n\n合同在说什么：\n本合同约定年度供货合作安排。\n\n核心安排：\n乙方按订单供货，甲方负责验收付款并开票结算。\n\n税务审核提示：\n需核对发票处理与交易实质是否一致。\n\n来源文件：demo.docx",
                {"model": "fake"},
            )

    result = ca._build_contract_summary(
        cfg={
            "llm_config": {"model": "fake"},
            "contract_summary_enabled": True,
        },
        llm=SummaryThenAuditLLM(),
        text="第一条 合作范围 乙方按甲方订单供货。第二条 结算与开票 甲方验收后付款，乙方开具发票。第三条 违约与终止 双方约定违约责任与解约条件。",
        lang="zh",
        preview_clauses=[
            {"clause_path": "第一条", "clause_text": "乙方按甲方订单供货。"},
            {"clause_path": "第二条", "clause_text": "甲方验收后付款，乙方开具发票。"},
        ],
        page_count=3,
        audit_id="audit_test_summary_only",
        source_filename="demo.docx",
    )

    assert "合同内容简要摘要" in str(result.get("text") or "")
    assert "税务审核提示" in str(result.get("text") or "")
    assert result["enabled"] is True
    assert result["status"] == "ok"


def test_audit_contract_does_not_attach_contract_summary(monkeypatch, tmp_path):
    class AuditOnlyLLM:
        def chat(self, messages, overrides=None):
            return (
                "{\"summary\":\"识别到 0 项风险\",\"risks\":[]}",
                {
                    "model": "fake",
                    "usage": {"prompt_tokens": 100, "completion_tokens": 60, "total_tokens": 160},
                    "choices": [{"finish_reason": "stop"}],
                },
            )

    monkeypatch.setattr(
        ca,
        "extract_text_with_config",
        lambda _cfg, _file_path: (
            "第一条 合作范围 乙方按甲方订单供货。第二条 结算与开票 甲方验收后付款，乙方开具发票。",
            {"ocr_used": False, "ocr_engine": "", "page_count": 2},
        ),
    )
    monkeypatch.setattr(
        ca,
        "_retrieve_regulation_evidence",
        lambda *_args, **_kwargs: {
            "used": True,
            "queries": [],
            "query_success": 0,
            "query_failed": 0,
            "chunk_total": 0,
            "failed_chunks": [],
            "items": [],
        },
    )
    monkeypatch.setattr(
        ca,
        "build_contract_preview_manifest",
        lambda **_kwargs: {"pages": []},
    )

    result = ca.audit_contract(
        cfg={
            "data_dir": str(tmp_path / "data"),
            "files_dir": str(tmp_path / "files"),
            "llm_config": {"model": "fake"},
            "contract_summary_enabled": True,
            "memory_runtime_config": {"memory_module_enabled": False},
        },
        llm=AuditOnlyLLM(),
        file_path=str(tmp_path / "demo.docx"),
        preview_file_path=str(tmp_path / "demo.docx"),
        lang="zh",
        retrieval_options={"audit_mode": "rag"},
    )

    assert str((result.get("audit") or {}).get(
        "contract_summary") or "").strip() == ""
    assert "contract_summary_enabled" not in (result.get("meta") or {})


def test_build_classic_audit_uses_configured_max_tokens(monkeypatch):
    class RetryAwareLLM:
        def __init__(self):
            self.max_tokens_calls = []

        def chat(self, messages, overrides=None):
            current_max_tokens = int((overrides or {}).get("max_tokens") or 0)
            self.max_tokens_calls.append(current_max_tokens)
            if len(self.max_tokens_calls) == 1:
                return (
                    "not-json",
                    {
                        "model": "fake",
                        "usage": {"prompt_tokens": 120, "completion_tokens": current_max_tokens, "total_tokens": 120 + current_max_tokens},
                        "choices": [{"finish_reason": "length"}],
                    },
                )
            return (
                "{\"summary\":\"识别到 0 项风险\",\"risks\":[]}",
                {
                    "model": "fake",
                    "usage": {"prompt_tokens": 120, "completion_tokens": 80, "total_tokens": 200},
                    "choices": [{"finish_reason": "stop"}],
                },
            )

    monkeypatch.setattr(
        ca,
        "resolve_bound_prompt_text",
        lambda *_args, **_kwargs: {
            "rendered_system_text": "Return strict JSON only.",
            "rendered_text": "Review these contract clauses and return JSON only.",
            "source": "test",
            "prompt_id": "contract_audit.classic_json",
            "prompt_name": "contract_audit.classic_json",
        },
    )

    llm = RetryAwareLLM()
    result = ca._build_classic_audit(
        cfg={
            "memory_runtime_config": {
                "memory_module_enabled": False,
                "classic_audit_max_tokens": 1500,
                "classic_audit_retry_max_tokens": 2400,
                "memory_max_prompt_chars_per_clause": 1200,
            }
        },
        llm=llm,
        text="第一条 合作范围 乙方按甲方订单供货。",
        lang="zh",
        preview_clauses=[
            {
                "clause_id": "c1",
                "clause_path": "第一条",
                "clause_text": "乙方按甲方订单供货。",
                "anchor_id": "clause-c1",
                "paragraph_no": "1",
            }
        ],
        preview_manifest={"pages": []},
        evidence_items=[],
        retrieval_opts={"audit_mode": "rag"},
        audit_id="audit_configured_token_limits",
    )

    assert llm.max_tokens_calls == [1500, 2400]
    assert result["meta"]["classic_retry_used"] is True
    assert result["meta"]["classic_max_tokens"] == 2400
