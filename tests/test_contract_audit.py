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


def test_build_contract_summary_returns_structured_json_when_enabled():
    class SummaryThenAuditLLM:
        def chat(self, messages, overrides=None):
            return (
                '{"title":"demo","about":"本合同约定年度供货合作安排。","core":"乙方按订单供货，甲方负责验收付款并开票结算。","tax":"需核对发票处理与交易实质是否一致。"}',
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

    assert result["enabled"] is True
    assert result["status"] == "ok"
    assert result["title"] == "demo"
    assert "年度供货" in result["about"]
    assert "发票" in result["tax"]
    assert result["source_file"] == "demo.docx"


def test_build_contract_summary_derives_title_and_source_file_when_json_omits_title():
    class SummaryThenAuditLLM:
        def chat(self, messages, overrides=None):
            return (
                '{"about":"本合同约定年度供货合作安排。","core":"乙方按订单供货。","tax":"需核对发票口径。"}',
                {"model": "fake"},
            )

    result = ca._build_contract_summary(
        cfg={
            "llm_config": {"model": "fake"},
            "contract_summary_enabled": True,
        },
        llm=SummaryThenAuditLLM(),
        text="第一条 合作范围 乙方按甲方订单供货。",
        lang="zh",
        preview_clauses=[
            {"clause_path": "第一条", "clause_text": "乙方按甲方订单供货。"},
        ],
        page_count=1,
        audit_id="audit_test_summary_title_fallback",
        source_filename="华润万家购销合同2026年修订-可编辑文字版.docx",
    )

    assert result["enabled"] is True
    assert result["status"] == "ok"
    assert result["title"] == "华润万家购销合同2026年修订-可编辑文字版"
    assert result["source_file"] == "华润万家购销合同2026年修订-可编辑文字版.docx"


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


def test_contract_audit_context_window_uses_cloud_budget_when_runtime_is_cloud():
    cfg = {
        "memory_runtime_config": {
            "cloud_context_window_tokens": 131072,
            "local_context_window_tokens": 4096,
        },
    }
    runtime_cfg = {"provider": "deepseek"}
    context_window = ca._resolve_contract_audit_context_window_tokens(
        cfg, runtime_cfg)
    assert context_window == 131072


def test_contract_audit_context_window_uses_local_num_ctx_for_ollama():
    cfg = {
        "memory_runtime_config": {
            "cloud_context_window_tokens": 131072,
            "local_context_window_tokens": 4096,
        },
    }
    runtime_cfg = {"provider": "ollama", "num_ctx": 8192}
    context_window = ca._resolve_contract_audit_context_window_tokens(
        cfg, runtime_cfg)
    assert context_window == 8192


def test_contract_audit_runtime_ignores_env_cloud_provider_in_local_mode(monkeypatch):
    cfg = {
        "llm_config": {
            "provider": "deepseek",
            "api_base": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
        },
        "local_llm": {
            "enabled": True,
            "routing_enabled": True,
            "cloud_fallback_enabled": False,
            "main_model": {
                "provider": "ollama",
                "api_base": "http://127.0.0.1:11434/v1",
                "model": "qwen3.5:9b",
            },
        },
    }
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")

    runtime_cfg = ca._resolve_contract_audit_runtime_llm(cfg)

    assert runtime_cfg["provider"] == "ollama"
    assert runtime_cfg["model"] == "qwen3.5:9b"
    assert runtime_cfg["api_base"] == "http://127.0.0.1:11434/v1"


def test_build_contract_summary_source_text_groups_clauses_and_flags_truncation():
    preview_clauses = [
        {"clause_path": "第一条 合作范围", "clause_text": "乙方按甲方订单供货。"},
        {"clause_path": "第一条 合作范围", "clause_text": "甲方负责验收。"},
        {"clause_path": "第二条 结算与开票", "clause_text": "甲方验收后付款。"},
    ]

    out = ca._build_contract_summary_source_text(
        "ignored", preview_clauses, max_chars=2000)

    assert "【第一条 合作范围 乙方按甲方订单供货。】" in out["text"]
    assert "甲方负责验收。" in out["text"]
    assert out["clause_block_total"] == 2
    assert out["clause_block_included"] == 2
    assert out["truncated"] is False

    long_clauses = [
        {"clause_path": f"第{i}条 条款", "clause_text": "条款正文" * 100}
        for i in range(1, 6)
    ]
    long_out = ca._build_contract_summary_source_text(
        "ignored", long_clauses, max_chars=2000)

    assert long_out["truncated"] is True
    assert long_out["clause_block_included"] < long_out["clause_block_total"]
    assert long_out["source_chars"] <= 2100
    assert long_out["text"].rstrip().endswith("】")


def test_build_contract_summary_marks_partial_when_output_truncated():
    class TruncatedLLM:
        def chat(self, messages, overrides=None):
            return (
                '{"about":"本合同约定年度供货合作安排。","core":"乙方按订单供货，甲方验收后付款并开票结算。","tax":""}',
                {"model": "fake", "choices": [{"finish_reason": "length"}]},
            )

    result = ca._build_contract_summary(
        cfg={
            "llm_config": {"model": "fake"},
            "contract_summary_enabled": True,
        },
        llm=TruncatedLLM(),
        text="第一条 合作范围 乙方按甲方订单供货。第二条 结算与开票 甲方验收后付款，乙方开具发票。",
        lang="zh",
        preview_clauses=[
            {"clause_path": "第一条", "clause_text": "乙方按甲方订单供货。"},
            {"clause_path": "第二条", "clause_text": "甲方验收后付款，乙方开具发票。"},
        ],
        page_count=2,
        audit_id="audit_test_summary_partial",
        source_filename="demo.docx",
    )

    assert result["status"] == "partial"
    assert "output_truncated" in result["issues"]
    assert any(issue.startswith("missing_sections")
               for issue in result["issues"])
    assert result["about"]


def test_build_contract_summary_reuses_cached_result_for_same_document(tmp_path):
    class CountingLLM:
        def __init__(self):
            self.calls = 0

        def chat(self, messages, overrides=None):
            self.calls += 1
            return (
                '{"about":"本合同约定年度供货合作安排。","core":"乙方按订单供货，甲方验收后付款。","tax":"需核对发票处理与交易实质是否一致。"}',
                {"model": "fake"},
            )

    llm = CountingLLM()
    kwargs = {
        "cfg": {
            "data_dir": str(tmp_path / "data"),
            "llm_config": {"model": "fake"},
            "contract_summary_enabled": True,
        },
        "llm": llm,
        "text": "第一条 合作范围 乙方按甲方订单供货。",
        "lang": "zh",
        "preview_clauses": [
            {"clause_path": "第一条", "clause_text": "乙方按甲方订单供货。"},
        ],
        "page_count": 1,
        "audit_id": "audit_test_summary_cache",
        "source_filename": "demo.docx",
        "document_id": "doc-cache",
        "source_sha256": "a" * 64,
    }

    first = ca._build_contract_summary(**kwargs)
    second = ca._build_contract_summary(**kwargs)

    assert first["status"] == "ok"
    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert second["source"] == "cache"
    assert second["about"] == first["about"]
    assert llm.calls == 1


def test_build_contract_summary_uses_map_reduce_for_long_contract(monkeypatch):
    monkeypatch.setattr(
        ca,
        "_resolve_contract_audit_runtime_llm",
        lambda _cfg: {"provider": "ollama", "num_ctx": 4096},
    )
    seen_prompts = []
    map_calls = []

    class MapReduceLLM:
        def chat(self, messages, overrides=None):
            seen_prompts.append(str(messages[-1].get("content") or ""))
            return (
                '{"about":"本合同为年度购销主合同，覆盖供货、结算与开票安排。","core":"核心安排包括供货、验收、对账、付款与开票结算。","tax":"需核对税率变化与发票主体是否一致。"}',
                {"model": "fake"},
            )

    def fake_map(_cfg, _llm, chunk_text, chunk_index, chunk_total,
                 _lang, _source_name, _max_tokens, _audit_id):
        map_calls.append((chunk_index, chunk_total, len(chunk_text)))
        return {
            "pricing": [f"第{chunk_index}块含税价交易"],
            "tax": [f"第{chunk_index}块税率变化风险"],
        }

    monkeypatch.setattr(ca, "_extract_summary_chunk_facts", fake_map)

    preview_clauses = [
        {"clause_path": f"第{i}条 条款", "clause_text": "本条约定的交易与结算安排。" * 30}
        for i in range(1, 25)
    ]
    cfg = {
        "llm_config": {"model": "fake"},
        "contract_summary_enabled": True,
    }

    result = ca._build_contract_summary(
        cfg=cfg,
        llm=MapReduceLLM(),
        text="ignored",
        lang="zh",
        preview_clauses=preview_clauses,
        page_count=10,
        audit_id="audit_test_summary_map_reduce",
        source_filename="long-contract.docx",
    )

    assert result["map_reduce"] is True
    assert result["map_chunks"] >= 2
    assert len(map_calls) == result["map_chunks"]
    assert result["status"] == "ok"
    assert any(
        "第1块税率变化风险" in prompt and "第2块税率变化风险" in prompt
        for prompt in seen_prompts
    )
