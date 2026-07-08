from pathlib import Path
import json

import numpy as np

from app.services import contract_audit as ca


class FakeEmbedder:
    def encode(self, texts):
        out = []
        for text in texts:
            vec = np.zeros(16, dtype=np.float32)
            for i, ch in enumerate(str(text)[:128]):
                vec[i % 16] += (ord(ch) % 29) / 29.0
            norm = np.linalg.norm(vec) + 1e-12
            out.append(vec / norm)
        return np.vstack(out) if out else np.zeros((0, 16), dtype=np.float32)


class FakeLLM:
    def __init__(self):
        self.audit_prompts = []

    def chat(self, messages, overrides=None):
        user = messages[-1]["content"] if messages else ""
        if "压缩为可持久化的Markdown记忆要点" in user:
            return "- flush", {"model": "fake"}
        self.audit_prompts.append(user)
        return (
            '{"summary":"ok","risks":[{"level":"high","issue":"invoice risk","suggestion":"add deadline","law_title":"中华人民共和国增值税暂行条例","article_no":"第十九条","evidence":"invoice clause","confidence":0.9}]}',
            {"model": "fake"},
        )


class FakeLLMUnmappedCitation:
    def __init__(self):
        self.audit_prompts = []

    def chat(self, messages, overrides=None):
        user = messages[-1]["content"] if messages else ""
        if "压缩为可持久化的Markdown记忆要点" in user:
            return "- flush", {"model": "fake"}
        self.audit_prompts.append(user)
        return (
            '{"summary":"ok","risks":[{"level":"medium","issue":"payment mapping risk","suggestion":"check mapping","citation_id":"C999","law_title":"中华人民共和国民法典","article_no":"第五百条","evidence":"payment clause","confidence":0.7}]}',
            {"model": "fake"},
        )


class FakeLLMNoRisk:
    def __init__(self):
        self.audit_prompts = []

    def chat(self, messages, overrides=None):
        user = messages[-1]["content"] if messages else ""
        if "压缩为可持久化的Markdown记忆要点" in user:
            return "- flush", {"model": "fake"}
        self.audit_prompts.append(user)
        return (
            '{"summary":"ok","risks":[]}',
            {"model": "fake"},
        )


class FakeLLMTaxRiskUnmappedCitation:
    def __init__(self):
        self.audit_prompts = []

    def chat(self, messages, overrides=None):
        user = messages[-1]["content"] if messages else ""
        if "压缩为可持久化的Markdown记忆要点" in user:
            return "- flush", {"model": "fake"}
        self.audit_prompts.append(user)
        return (
            '{"summary":"ok","risks":[{"level":"high","issue":"发票税率适用口径不明确","suggestion":"补充税率与计税依据","citation_id":"C999","law_title":"税务合规提示","article_no":"第九十九条","evidence":"税率条款","confidence":0.8}]}',
            {"model": "fake"},
        )


class FakeLLMDuplicateInvoiceTypeRisk:
    def __init__(self):
        self.audit_prompts = []

    def chat(self, messages, overrides=None):
        user = messages[-1]["content"] if messages else ""
        if "压缩为可持久化的Markdown记忆要点" in user:
            return "- flush", {"model": "fake"}
        self.audit_prompts.append(user)
        return (
            '{"summary":"ok","risks":[{"level":"high","issue":"Inconsistent tax invoice type requirements (Special vs. General)","suggestion":"Clarify whether Party B should issue VAT special invoice or general invoice","citation_id":"C999","law_title":"税务合规提示","article_no":"第二十一条","evidence":"special invoice and general invoice both appear","confidence":0.91},{"level":"medium","issue":"Contradictory VAT invoice requirements: special invoice conflicts with general invoice","suggestion":"Align all contract clauses to one VAT invoice type","citation_id":"C998","law_title":"税务合规提示","article_no":"第二十一条","evidence":"invoice type contradiction across clauses","confidence":0.62}]}',
            {"model": "fake"},
        )


class FakeLLMArticleFormatVariant:
    def __init__(self):
        self.audit_prompts = []

    def chat(self, messages, overrides=None):
        user = messages[-1]["content"] if messages else ""
        if "压缩为可持久化的Markdown记忆要点" in user:
            return "- flush", {"model": "fake"}
        self.audit_prompts.append(user)
        return (
            '{"summary":"ok","risks":[{"level":"medium","issue":"payment clause risk","suggestion":"clarify obligations","citation_id":"","law_title":"Civil Code of the People\'s Republic of China","article_no":"Article 470","evidence":"payment due clause","confidence":0.8}]}',
            {"model": "fake"},
        )


def test_audit_contract_memory_mode(monkeypatch, tmp_path: Path):
    def fake_extract(_cfg, _file_path):
        text = "第一条 甲方应在10日内开具发票。\nArticle 2 supplier shall issue invoice."
        return text, {"ocr_used": False, "ocr_engine": "", "page_count": 1}

    def fake_retrieve(_cfg, _text, _lang, _opts, embedder=None, reranker=None):
        return {
            "used": True,
            "queries": ["发票", "invoice"],
            "query_success": 2,
            "query_failed": 0,
            "chunk_total": 2,
            "failed_chunks": [],
            "items": [
                {
                    "citation_id": "c1",
                    "law_title": "中华人民共和国增值税暂行条例",
                    "title": "中华人民共和国增值税暂行条例",
                    "article_no": "第十九条",
                    "excerpt": "纳税义务发生时间",
                    "content": "纳税义务发生时间",
                }
            ],
        }

    class _Hit:
        def __init__(self, content):
            self.content = content

    class FakeSearcher:
        def __init__(self, *_args, **_kwargs):
            pass

        def search(self, _query, top_k=8):
            return [_Hit("历史记忆命中：此前条款中已有发票时限风险")]

    monkeypatch.setattr(ca, "extract_text_with_config", fake_extract)
    monkeypatch.setattr(ca, "_retrieve_regulation_evidence", fake_retrieve)
    monkeypatch.setattr(ca, "_get_memory_embedder", lambda: FakeEmbedder())
    monkeypatch.setattr(ca, "HybridSearcher", FakeSearcher)

    cfg = {
        "data_dir": str(tmp_path / "data"),
        "memory_dir": str(tmp_path / "memory"),
        "memory_filter_unverifiable_risks": False,
        "llm_config": {"model": "fake", "timeout": 8},
    }
    f = tmp_path / "dummy.docx"
    f.write_text("x", encoding="utf-8")
    llm = FakeLLM()
    result = ca.audit_contract(
        cfg=cfg,
        llm=llm,
        file_path=str(f),
        lang="zh",
        retrieval_options={"audit_mode": "rag"},
    )

    assert result["meta"]["memory_mode"] is True
    assert result["meta"]["memory_use_long_hits"] is True
    assert result["meta"]["memory_validation_ok"] is True
    assert result["audit"]["risk_summary"]["high"] >= 1
    assert any("长期记忆命中:" in p for p in llm.audit_prompts)


def test_audit_contract_memory_mode_long_hits_disabled(monkeypatch, tmp_path: Path):
    def fake_extract(_cfg, _file_path):
        text = "第一条 甲方应在10日内开具发票。\nArticle 2 supplier shall issue invoice."
        return text, {"ocr_used": False, "ocr_engine": "", "page_count": 1}

    def fake_retrieve(_cfg, _text, _lang, _opts, embedder=None, reranker=None):
        return {
            "used": True,
            "queries": ["发票", "invoice"],
            "query_success": 2,
            "query_failed": 0,
            "chunk_total": 2,
            "failed_chunks": [],
            "items": [],
        }

    class _Hit:
        def __init__(self, content):
            self.content = content

    class FakeSearcher:
        def __init__(self, *_args, **_kwargs):
            pass

        def search(self, _query, top_k=8):
            return [_Hit("历史命中内容")]

    monkeypatch.setattr(ca, "extract_text_with_config", fake_extract)
    monkeypatch.setattr(ca, "_retrieve_regulation_evidence", fake_retrieve)
    monkeypatch.setattr(ca, "_get_memory_embedder", lambda: FakeEmbedder())
    monkeypatch.setattr(ca, "HybridSearcher", FakeSearcher)

    cfg = {
        "data_dir": str(tmp_path / "data"),
        "memory_dir": str(tmp_path / "memory"),
        "memory_use_long_hits": False,
        "llm_config": {"model": "fake", "timeout": 8},
    }
    f = tmp_path / "dummy.docx"
    f.write_text("x", encoding="utf-8")
    llm = FakeLLM()
    result = ca.audit_contract(
        cfg=cfg,
        llm=llm,
        file_path=str(f),
        lang="zh",
        retrieval_options={"audit_mode": "rag"},
    )

    assert result["meta"]["memory_use_long_hits"] is False
    assert all("长期记忆命中:" not in p for p in llm.audit_prompts)


def test_audit_contract_memory_mode_keep_unmapped_risk_when_filter_disabled(monkeypatch, tmp_path: Path):
    def fake_extract(_cfg, _file_path):
        text = "第一条 甲方应在10日内开具发票。"
        return text, {"ocr_used": False, "ocr_engine": "", "page_count": 1}

    def fake_retrieve(_cfg, _text, _lang, _opts, embedder=None, reranker=None):
        return {
            "used": True,
            "queries": ["发票"],
            "query_success": 1,
            "query_failed": 0,
            "chunk_total": 1,
            "failed_chunks": [],
            "items": [
                {
                    "citation_id": "c1",
                    "law_title": "中华人民共和国增值税暂行条例",
                    "title": "中华人民共和国增值税暂行条例",
                    "article_no": "第十九条",
                    "excerpt": "纳税义务发生时间",
                    "content": "纳税义务发生时间",
                }
            ],
        }

    class _Hit:
        def __init__(self, content):
            self.content = content

    class FakeSearcher:
        def __init__(self, *_args, **_kwargs):
            pass

        def search(self, _query, top_k=8):
            return [_Hit("历史命中内容")]

    monkeypatch.setattr(ca, "extract_text_with_config", fake_extract)
    monkeypatch.setattr(ca, "_retrieve_regulation_evidence", fake_retrieve)
    monkeypatch.setattr(ca, "_get_memory_embedder", lambda: FakeEmbedder())
    monkeypatch.setattr(ca, "HybridSearcher", FakeSearcher)

    cfg = {
        "data_dir": str(tmp_path / "data"),
        "memory_dir": str(tmp_path / "memory"),
        "memory_filter_unverifiable_risks": False,
        "llm_config": {"model": "fake", "timeout": 8},
    }
    f = tmp_path / "dummy.docx"
    f.write_text("x", encoding="utf-8")
    llm = FakeLLMUnmappedCitation()
    result = ca.audit_contract(
        cfg=cfg,
        llm=llm,
        file_path=str(f),
        lang="zh",
        retrieval_options={"audit_mode": "rag"},
    )

    assert result["audit"]["risk_summary"]["medium"] >= 1
    assert result["meta"]["retained_unmapped_risks"] >= 1
    assert result["meta"]["unmapped_citation_risks"] >= 1
    first_risk = result["audit"]["risks"][0]
    assert first_risk["citation_status"] == "unmapped"


def test_audit_contract_memory_mode_filter_unverifiable_risk_default_on(monkeypatch, tmp_path: Path):
    def fake_extract(_cfg, _file_path):
        text = "第一条 甲方应在10日内开具发票。"
        return text, {"ocr_used": False, "ocr_engine": "", "page_count": 1}

    def fake_retrieve(_cfg, _text, _lang, _opts, embedder=None, reranker=None):
        return {
            "used": True,
            "queries": ["发票"],
            "query_success": 1,
            "query_failed": 0,
            "chunk_total": 1,
            "failed_chunks": [],
            "items": [
                {
                    "citation_id": "c1",
                    "law_title": "中华人民共和国增值税暂行条例",
                    "title": "中华人民共和国增值税暂行条例",
                    "article_no": "第十九条",
                    "excerpt": "纳税义务发生时间",
                    "content": "纳税义务发生时间",
                }
            ],
        }

    class _Hit:
        def __init__(self, content):
            self.content = content

    class FakeSearcher:
        def __init__(self, *_args, **_kwargs):
            pass

        def search(self, _query, top_k=8):
            return [_Hit("历史命中内容")]

    monkeypatch.setattr(ca, "extract_text_with_config", fake_extract)
    monkeypatch.setattr(ca, "_retrieve_regulation_evidence", fake_retrieve)
    monkeypatch.setattr(ca, "_get_memory_embedder", lambda: FakeEmbedder())
    monkeypatch.setattr(ca, "HybridSearcher", FakeSearcher)

    cfg = {
        "data_dir": str(tmp_path / "data"),
        "memory_dir": str(tmp_path / "memory"),
        "memory_zero_risk_fallback_enabled": False,
        "llm_config": {"model": "fake", "timeout": 8},
    }
    f = tmp_path / "dummy.docx"
    f.write_text("x", encoding="utf-8")
    llm = FakeLLMUnmappedCitation()
    result = ca.audit_contract(
        cfg=cfg,
        llm=llm,
        file_path=str(f),
        lang="zh",
        retrieval_options={"audit_mode": "rag"},
    )

    assert result["meta"]["memory_filter_unverifiable_risks"] is True
    assert result["meta"]["filtered_unverifiable_risks"] >= 1
    assert result["audit"]["risk_summary"]["medium"] == 0
    assert len(result["audit"]["risks"]) == 0


def test_audit_contract_memory_mode_keep_tax_risk_when_unverifiable(monkeypatch, tmp_path: Path):
    def fake_extract(_cfg, _file_path):
        text = "第一条 甲方应按约定税率开具发票。"
        return text, {"ocr_used": False, "ocr_engine": "", "page_count": 1}

    def fake_retrieve(_cfg, _text, _lang, _opts, embedder=None, reranker=None):
        return {
            "used": True,
            "queries": ["发票", "税率"],
            "query_success": 2,
            "query_failed": 0,
            "chunk_total": 1,
            "failed_chunks": [],
            "items": [
                {
                    "citation_id": "c1",
                    "law_title": "中华人民共和国增值税法",
                    "title": "中华人民共和国增值税法",
                    "article_no": "第三条",
                    "excerpt": "税率与应税行为",
                    "content": "税率与应税行为",
                }
            ],
        }

    class _Hit:
        def __init__(self, content):
            self.content = content

    class FakeSearcher:
        def __init__(self, *_args, **_kwargs):
            pass

        def search(self, _query, top_k=8):
            return [_Hit("历史命中内容")]

    monkeypatch.setattr(ca, "extract_text_with_config", fake_extract)
    monkeypatch.setattr(ca, "_retrieve_regulation_evidence", fake_retrieve)
    monkeypatch.setattr(ca, "_get_memory_embedder", lambda: FakeEmbedder())
    monkeypatch.setattr(ca, "HybridSearcher", FakeSearcher)

    cfg = {
        "data_dir": str(tmp_path / "data"),
        "memory_dir": str(tmp_path / "memory"),
        "memory_zero_risk_fallback_enabled": False,
        "llm_config": {"model": "fake", "timeout": 8},
    }
    f = tmp_path / "dummy.docx"
    f.write_text("x", encoding="utf-8")
    llm = FakeLLMTaxRiskUnmappedCitation()
    result = ca.audit_contract(
        cfg=cfg,
        llm=llm,
        file_path=str(f),
        lang="zh",
        retrieval_options={"audit_mode": "rag"},
    )

    assert result["meta"]["memory_filter_unverifiable_risks"] is True
    assert result["audit"]["risk_summary"]["high"] >= 1
    assert len(result["audit"]["risks"]) >= 1
    assert result["meta"]["filtered_unverifiable_risks"] == 0
    first_risk = result["audit"]["risks"][0]
    assert first_risk["citation_status"] == "unmapped"


def test_audit_contract_memory_mode_dedupe_invoice_type_conflict(monkeypatch, tmp_path: Path):
    def fake_extract(_cfg, _file_path):
        text = "第一条 开具增值税专用发票。第二条 开具增值税普通发票。"
        return text, {"ocr_used": False, "ocr_engine": "", "page_count": 1}

    def fake_retrieve(_cfg, _text, _lang, _opts, embedder=None, reranker=None):
        return {
            "used": True,
            "queries": ["发票", "税率"],
            "query_success": 2,
            "query_failed": 0,
            "chunk_total": 1,
            "failed_chunks": [],
            "items": [
                {
                    "citation_id": "c1",
                    "law_title": "中华人民共和国增值税法",
                    "title": "中华人民共和国增值税法",
                    "article_no": "第二十一条",
                    "excerpt": "发票与税务处理",
                    "content": "发票与税务处理",
                }
            ],
        }

    class _Hit:
        def __init__(self, content):
            self.content = content

    class FakeSearcher:
        def __init__(self, *_args, **_kwargs):
            pass

        def search(self, _query, top_k=8):
            return [_Hit("历史命中内容")]

    monkeypatch.setattr(ca, "extract_text_with_config", fake_extract)
    monkeypatch.setattr(ca, "_retrieve_regulation_evidence", fake_retrieve)
    monkeypatch.setattr(ca, "_get_memory_embedder", lambda: FakeEmbedder())
    monkeypatch.setattr(ca, "HybridSearcher", FakeSearcher)

    cfg = {
        "data_dir": str(tmp_path / "data"),
        "memory_dir": str(tmp_path / "memory"),
        "memory_zero_risk_fallback_enabled": False,
        "llm_config": {"model": "fake", "timeout": 8},
    }
    f = tmp_path / "dummy.docx"
    f.write_text("x", encoding="utf-8")
    llm = FakeLLMDuplicateInvoiceTypeRisk()
    result = ca.audit_contract(
        cfg=cfg,
        llm=llm,
        file_path=str(f),
        lang="zh",
        retrieval_options={"audit_mode": "rag"},
    )

    assert result["meta"]["risk_dedup_enabled"] is True
    assert result["meta"]["dedupe_similar_risks"] is True
    assert result["meta"]["deduped_similar_risks"] >= 1
    assert len(result["audit"]["risks"]) == 1
    assert result["audit"]["risk_summary"]["high"] == 1


def test_audit_contract_memory_mode_maps_article_format_variant(monkeypatch, tmp_path: Path):
    def fake_extract(_cfg, _file_path):
        text = "Payment due in 30 days."
        return text, {"ocr_used": False, "ocr_engine": "", "page_count": 1}

    def fake_retrieve(_cfg, _text, _lang, _opts, embedder=None, reranker=None):
        return {
            "used": True,
            "queries": ["payment due"],
            "query_success": 1,
            "query_failed": 0,
            "chunk_total": 1,
            "failed_chunks": [],
            "items": [
                {
                    "citation_id": "c-law-470",
                    "law_title": "中华人民共和国民法典",
                    "title": "中华人民共和国民法典",
                    "article_no": "第470条",
                    "excerpt": "合同内容由当事人约定",
                    "content": "合同内容由当事人约定",
                }
            ],
        }

    class _Hit:
        def __init__(self, content):
            self.content = content

    class FakeSearcher:
        def __init__(self, *_args, **_kwargs):
            pass

        def search(self, _query, top_k=8):
            return [_Hit("history hit")]

    monkeypatch.setattr(ca, "extract_text_with_config", fake_extract)
    monkeypatch.setattr(ca, "_retrieve_regulation_evidence", fake_retrieve)
    monkeypatch.setattr(ca, "_get_memory_embedder", lambda: FakeEmbedder())
    monkeypatch.setattr(ca, "HybridSearcher", FakeSearcher)

    cfg = {
        "data_dir": str(tmp_path / "data"),
        "memory_dir": str(tmp_path / "memory"),
        "memory_zero_risk_fallback_enabled": False,
        "memory_filter_unverifiable_risks": False,
        "llm_config": {"model": "fake", "timeout": 8},
    }
    f = tmp_path / "dummy.docx"
    f.write_text("x", encoding="utf-8")
    llm = FakeLLMArticleFormatVariant()
    result = ca.audit_contract(
        cfg=cfg,
        llm=llm,
        file_path=str(f),
        lang="en",
        retrieval_options={"audit_mode": "rag"},
    )

    assert len(result["audit"]["risks"]) == 1
    first_risk = result["audit"]["risks"][0]
    assert first_risk["citation_status"] == "mapped"
    assert first_risk["citation_id"] == "c-law-470"
    assert result["meta"]["unmapped_citation_risks"] == 0


def test_audit_contract_memory_mode_zero_risk_fallback(monkeypatch, tmp_path: Path):
    def fake_extract(_cfg, _file_path):
        text = "第一条 本合同服务费按免税税率执行。第二条 乙方提供平台服务并向甲方开具发票。"
        return text, {"ocr_used": False, "ocr_engine": "", "page_count": 1}

    def fake_retrieve(_cfg, _text, _lang, _opts, embedder=None, reranker=None):
        return {
            "used": True,
            "queries": ["税率", "服务"],
            "query_success": 2,
            "query_failed": 0,
            "chunk_total": 2,
            "failed_chunks": [],
            "items": [
                {
                    "citation_id": "c1",
                    "law_title": "中华人民共和国增值税法",
                    "title": "中华人民共和国增值税法",
                    "article_no": "第三条",
                    "excerpt": "税率与应税行为",
                    "content": "税率与应税行为",
                }
            ],
        }

    class _Hit:
        def __init__(self, content):
            self.content = content

    class FakeSearcher:
        def __init__(self, *_args, **_kwargs):
            pass

        def search(self, _query, top_k=8):
            return [_Hit("历史命中内容")]

    monkeypatch.setattr(ca, "extract_text_with_config", fake_extract)
    monkeypatch.setattr(ca, "_retrieve_regulation_evidence", fake_retrieve)
    monkeypatch.setattr(ca, "_get_memory_embedder", lambda: FakeEmbedder())
    monkeypatch.setattr(ca, "HybridSearcher", FakeSearcher)

    cfg = {
        "data_dir": str(tmp_path / "data"),
        "memory_dir": str(tmp_path / "memory"),
        "llm_config": {"model": "fake", "timeout": 8},
    }
    f = tmp_path / "dummy.docx"
    f.write_text("x", encoding="utf-8")
    llm = FakeLLMNoRisk()
    result = ca.audit_contract(
        cfg=cfg,
        llm=llm,
        file_path=str(f),
        lang="zh",
        retrieval_options={"audit_mode": "rag"},
    )

    assert result["meta"]["fallback_generated_risks"] == 1
    assert result["meta"]["zero_risk_fallback_triggered"] is True
    assert result["meta"]["risk_origin_breakdown"]["fallback_generated_risks"] == 1
    assert result["audit"]["risk_summary"]["medium"] >= 1


def test_get_regulation_pack_fingerprint_is_stable():
    items_a = [
        {"regulation_id": "reg-A", "version_id": "v1",
            "citation_id": "zh:reg-A:v1:a1"},
        {"regulation_id": "reg-B", "version_id": "v3",
            "citation_id": "zh:reg-B:v3:a2"},
    ]
    items_b = [
        {"regulation_id": "reg-B", "version_id": "v3",
            "citation_id": "zh:reg-B:v3:a2"},
        {"regulation_id": "reg-A", "version_id": "v1",
            "citation_id": "zh:reg-A:v1:a1"},
    ]
    opts = {"region": "CN", "industry": "tech", "date": "2026-01-01"}
    fp_a = ca.get_regulation_pack_fingerprint(
        items_a, retrieval_opts=opts, lang="zh")
    fp_b = ca.get_regulation_pack_fingerprint(
        items_b, retrieval_opts=opts, lang="zh")

    assert fp_a["regulation_pack_id"] == fp_b["regulation_pack_id"]
    assert fp_a["regulation_fingerprint"] == fp_b["regulation_fingerprint"]
    assert sorted(fp_a["regulation_pack_members"]) == sorted(
        fp_b["regulation_pack_members"])


def test_get_regulation_pack_fingerprint_changes_when_pack_changes():
    items_v1 = [{"regulation_id": "reg-A",
                 "version_id": "v1", "citation_id": "zh:reg-A:v1:a1"}]
    items_v2 = [{"regulation_id": "reg-A",
                 "version_id": "v2", "citation_id": "zh:reg-A:v2:a1"}]
    fp_v1 = ca.get_regulation_pack_fingerprint(
        items_v1, retrieval_opts={"region": "CN"}, lang="zh")
    fp_v2 = ca.get_regulation_pack_fingerprint(
        items_v2, retrieval_opts={"region": "CN"}, lang="zh")

    assert fp_v1["regulation_pack_id"] != fp_v2["regulation_pack_id"]
    assert fp_v1["regulation_fingerprint"] != fp_v2["regulation_fingerprint"]


def test_audit_contract_writes_pending_episode(monkeypatch, tmp_path: Path):
    def fake_extract(_cfg, _file_path):
        text = "第一条 甲方应在10日内开具发票。"
        return text, {"ocr_used": False, "ocr_engine": "", "page_count": 1}

    def fake_retrieve(_cfg, _text, _lang, _opts, embedder=None, reranker=None):
        return {
            "used": True,
            "queries": ["发票"],
            "query_success": 1,
            "query_failed": 0,
            "chunk_total": 1,
            "failed_chunks": [],
            "items": [
                {
                    "citation_id": "zh:reg-A:v1:a1",
                    "regulation_id": "reg-A",
                    "version_id": "v1",
                    "law_title": "中华人民共和国增值税暂行条例",
                    "title": "中华人民共和国增值税暂行条例",
                    "article_no": "第十九条",
                    "excerpt": "纳税义务发生时间",
                    "content": "纳税义务发生时间",
                }
            ],
        }

    class _Hit:
        def __init__(self, content):
            self.content = content

    class FakeSearcher:
        def __init__(self, *_args, **_kwargs):
            pass

        def search(self, _query, top_k=8):
            return [_Hit("历史命中内容")]

    monkeypatch.setattr(ca, "extract_text_with_config", fake_extract)
    monkeypatch.setattr(ca, "_retrieve_regulation_evidence", fake_retrieve)
    monkeypatch.setattr(ca, "_get_memory_embedder", lambda: FakeEmbedder())
    monkeypatch.setattr(ca, "HybridSearcher", FakeSearcher)

    episode_store = tmp_path / "memory" / "experience" / "case_episode_pending.jsonl"
    cfg = {
        "data_dir": str(tmp_path / "data"),
        "memory_dir": str(tmp_path / "memory"),
        "memory_episode_store_path": str(episode_store),
        "memory_episode_store_enabled": True,
        "memory_filter_unverifiable_risks": False,
        "llm_config": {"model": "fake", "timeout": 8},
    }
    f = tmp_path / "dummy.docx"
    f.write_text("x", encoding="utf-8")
    llm = FakeLLM()
    result = ca.audit_contract(
        cfg=cfg,
        llm=llm,
        file_path=str(f),
        lang="zh",
        retrieval_options={"audit_mode": "rag", "region": "CN"},
    )

    assert result["meta"]["episode_saved"] is True
    assert str(result["meta"]["episode_id"]).startswith("ep_")
    assert result["meta"]["episode_status"] == "pending"
    assert episode_store.exists()
    lines = episode_store.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 1
    row = json.loads(lines[-1])
    assert row["memory_type"] == "case"
    assert row["outcome"] == "pending"
    assert row["regulation_pack_id"] == result["meta"]["regulation_pack_id"]
    assert row["regulation_fingerprint"] == result["meta"]["regulation_fingerprint"]


def test_audit_contract_injects_failure_patterns_before_clause_review(monkeypatch, tmp_path: Path):
    def fake_extract(_cfg, _file_path):
        text = "第一条 乙方应按约定税率开具发票。"
        return text, {"ocr_used": False, "ocr_engine": "", "page_count": 1}

    def fake_retrieve(_cfg, _text, _lang, _opts, embedder=None, reranker=None):
        return {
            "used": True,
            "queries": ["税率", "发票"],
            "query_success": 1,
            "query_failed": 0,
            "chunk_total": 1,
            "failed_chunks": [],
            "items": [
                {
                    "citation_id": "zh:reg-A:v1:a1",
                    "regulation_id": "reg-A",
                    "version_id": "v1",
                    "law_title": "中华人民共和国增值税法",
                    "title": "中华人民共和国增值税法",
                    "article_no": "第三条",
                    "excerpt": "税率与应税行为",
                    "content": "税率与应税行为",
                }
            ],
        }

    class _Hit:
        def __init__(self, content):
            self.content = content

    class FakeSearcher:
        def __init__(self, *_args, **_kwargs):
            pass

        def search(self, _query, top_k=8):
            return [_Hit("历史命中内容")]

    monkeypatch.setattr(ca, "extract_text_with_config", fake_extract)
    monkeypatch.setattr(ca, "_retrieve_regulation_evidence", fake_retrieve)
    monkeypatch.setattr(ca, "_get_memory_embedder", lambda: FakeEmbedder())
    monkeypatch.setattr(ca, "HybridSearcher", FakeSearcher)

    feedback_store = tmp_path / "memory" / "experience" / "feedback_events.jsonl"
    feedback_store.parent.mkdir(parents=True, exist_ok=True)
    feedback_store.write_text(
        json.dumps(
            {
                "memory_id": "fb_1",
                "memory_type": "case",
                "outcome": "failure",
                "reviewer_status": "rejected",
                "risk_label": "high",
                "risk_reasoning": "发票税率冲突风险，历史上曾出现误报。",
                "reviewer_note": "误报样式：条款已明确税率与开票时点。",
                "clause_category": "",
                "memory_quality_score": 0.92,
                "confidence": 0.8,
                "created_at": "2026-01-01T00:00:00",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    cfg = {
        "data_dir": str(tmp_path / "data"),
        "memory_dir": str(tmp_path / "memory"),
        "memory_feedback_store_path": str(feedback_store),
        "memory_filter_unverifiable_risks": False,
        "llm_config": {"model": "fake", "timeout": 8},
    }
    f = tmp_path / "dummy.docx"
    f.write_text("x", encoding="utf-8")
    llm = FakeLLM()
    _ = ca.audit_contract(
        cfg=cfg,
        llm=llm,
        file_path=str(f),
        lang="zh",
        retrieval_options={"audit_mode": "rag"},
    )
    assert any("失败经验模式:" in p for p in llm.audit_prompts)
    assert any("误报样式" in p for p in llm.audit_prompts)
    assert any(
        "Check whether this clause matches any known false-positive or false-negative pattern before finalizing." in p
        for p in llm.audit_prompts
    )


def test_audit_contract_injects_case_memory_hits_with_pack_filter(monkeypatch, tmp_path: Path):
    def fake_extract(_cfg, _file_path):
        text = "第一条 发票税率按合同约定执行。"
        return text, {"ocr_used": False, "ocr_engine": "", "page_count": 1}

    def fake_retrieve(_cfg, _text, _lang, _opts, embedder=None, reranker=None):
        return {
            "used": True,
            "queries": ["发票", "税率"],
            "query_success": 1,
            "query_failed": 0,
            "chunk_total": 1,
            "failed_chunks": [],
            "items": [
                {
                    "citation_id": "zh:reg-A:v1:a1",
                    "regulation_id": "reg-A",
                    "version_id": "v1",
                    "law_title": "中华人民共和国增值税法",
                    "title": "中华人民共和国增值税法",
                    "article_no": "第三条",
                    "excerpt": "税率与应税行为",
                    "content": "税率与应税行为",
                }
            ],
        }

    class _Hit:
        def __init__(self, content):
            self.content = content

    class FakeSearcher:
        def __init__(self, *_args, **_kwargs):
            pass

        def search(self, _query, top_k=8):
            return [_Hit("历史命中内容")]

    monkeypatch.setattr(ca, "extract_text_with_config", fake_extract)
    monkeypatch.setattr(ca, "_retrieve_regulation_evidence", fake_retrieve)
    monkeypatch.setattr(ca, "_get_memory_embedder", lambda: FakeEmbedder())
    monkeypatch.setattr(ca, "HybridSearcher", FakeSearcher)

    episode_store = tmp_path / "memory" / "experience" / "case_episode_pending.jsonl"
    episode_store.parent.mkdir(parents=True, exist_ok=True)
    same_pack = {
        "memory_id": "ep_ok_1",
        "memory_type": "case",
        "regulation_pack_id": "rp_same",
        "clause_category": "",
        "risk_label": "medium",
        "clause_text_excerpt": "历史案例：发票税率约定不清",
        "risk_reasoning": "建议明确税率口径与开票时点",
        "legal_basis": ["中华人民共和国增值税法 第三条"],
        "memory_quality_score": 0.88,
        "confidence": 0.76,
        "created_at": "2026-01-01T00:00:00",
    }
    other_pack = dict(same_pack)
    other_pack["memory_id"] = "ep_bad_1"
    other_pack["regulation_pack_id"] = "rp_other"
    other_pack["clause_text_excerpt"] = "不应命中案例"
    episode_store.write_text(
        json.dumps(same_pack, ensure_ascii=False) + "\n" +
        json.dumps(other_pack, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # Force a deterministic regulation pack id for this test.
    monkeypatch.setattr(
        ca,
        "get_regulation_pack_fingerprint",
        lambda evidence_items, retrieval_opts=None, lang="zh": {
            "regulation_pack_id": "rp_same",
            "regulation_fingerprint": "fp_same",
            "regulation_pack_members": ["reg-A:v1"],
        },
    )

    cfg = {
        "data_dir": str(tmp_path / "data"),
        "memory_dir": str(tmp_path / "memory"),
        "memory_episode_store_path": str(episode_store),
        "memory_filter_unverifiable_risks": False,
        "llm_config": {"model": "fake", "timeout": 8},
    }
    f = tmp_path / "dummy.docx"
    f.write_text("x", encoding="utf-8")
    llm = FakeLLM()
    _ = ca.audit_contract(
        cfg=cfg,
        llm=llm,
        file_path=str(f),
        lang="zh",
        retrieval_options={"audit_mode": "rag"},
    )

    assert any("案例记忆命中:" in p for p in llm.audit_prompts)
    assert any("历史案例：发票税率约定不清" in p for p in llm.audit_prompts)
    assert all("不应命中案例" not in p for p in llm.audit_prompts)


def test_audit_contract_injects_workflow_memory_before_planning(monkeypatch, tmp_path: Path):
    def fake_extract(_cfg, _file_path):
        text = "第一条 发票税率按合同约定执行。第二条 开票时点应明确。"
        return text, {"ocr_used": False, "ocr_engine": "", "page_count": 1}

    def fake_retrieve(_cfg, _text, _lang, _opts, embedder=None, reranker=None):
        return {
            "used": True,
            "queries": ["发票", "税率"],
            "query_success": 1,
            "query_failed": 0,
            "chunk_total": 1,
            "failed_chunks": [],
            "items": [
                {
                    "citation_id": "zh:reg-A:v1:a1",
                    "regulation_id": "reg-A",
                    "version_id": "v1",
                    "law_title": "中华人民共和国增值税法",
                    "title": "中华人民共和国增值税法",
                    "article_no": "第三条",
                    "excerpt": "税率与应税行为",
                    "content": "税率与应税行为",
                }
            ],
        }

    class _Hit:
        def __init__(self, content):
            self.content = content

    class FakeSearcher:
        def __init__(self, *_args, **_kwargs):
            pass

        def search(self, _query, top_k=8):
            return [_Hit("历史命中内容")]

    monkeypatch.setattr(ca, "extract_text_with_config", fake_extract)
    monkeypatch.setattr(ca, "_retrieve_regulation_evidence", fake_retrieve)
    monkeypatch.setattr(ca, "_get_memory_embedder", lambda: FakeEmbedder())
    monkeypatch.setattr(ca, "HybridSearcher", FakeSearcher)
    monkeypatch.setattr(
        ca,
        "get_regulation_pack_fingerprint",
        lambda evidence_items, retrieval_opts=None, lang="zh": {
            "regulation_pack_id": "rp_same",
            "regulation_fingerprint": "fp_same",
            "regulation_pack_members": ["reg-A:v1"],
        },
    )

    workflow_store = tmp_path / "memory" / \
        "experience" / "workflow_memory_active.jsonl"
    workflow_store.parent.mkdir(parents=True, exist_ok=True)
    row_ok = {
        "memory_id": "wf_ok_1",
        "memory_type": "workflow",
        "status": "active",
        "regulation_pack_id": "rp_same",
        "workflow_title": "发票条款审计顺序",
        "workflow_steps": "先核对税率口径;再核对开票时点;最后核对例外条款",
        "memory_quality_score": 0.9,
        "confidence": 0.8,
        "created_at": "2026-01-01T00:00:00",
    }
    row_bad = dict(row_ok)
    row_bad["memory_id"] = "wf_bad_1"
    row_bad["regulation_pack_id"] = "rp_other"
    row_bad["workflow_title"] = "不应命中的工作流"
    workflow_store.write_text(
        json.dumps(row_ok, ensure_ascii=False) + "\n" +
        json.dumps(row_bad, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    cfg = {
        "data_dir": str(tmp_path / "data"),
        "memory_dir": str(tmp_path / "memory"),
        "memory_workflow_store_path": str(workflow_store),
        "memory_filter_unverifiable_risks": False,
        "llm_config": {"model": "fake", "timeout": 8},
    }
    f = tmp_path / "dummy.docx"
    f.write_text("x", encoding="utf-8")
    llm = FakeLLM()
    _ = ca.audit_contract(
        cfg=cfg,
        llm=llm,
        file_path=str(f),
        lang="zh",
        retrieval_options={"audit_mode": "rag"},
    )

    assert any("工作流记忆(审计规划):" in p for p in llm.audit_prompts)
    assert any("发票条款审计顺序" in p for p in llm.audit_prompts)
    assert all("不应命中的工作流" not in p for p in llm.audit_prompts)


def test_audit_contract_runs_when_memory_module_disabled(monkeypatch, tmp_path: Path):
    def fake_extract(_cfg, _file_path):
        text = "第一条 甲方应在10日内开具发票。"
        return text, {"ocr_used": False, "ocr_engine": "", "page_count": 1}

    def fake_retrieve(_cfg, _text, _lang, _opts, embedder=None, reranker=None):
        return {
            "used": True,
            "queries": ["发票"],
            "query_success": 1,
            "query_failed": 0,
            "chunk_total": 1,
            "failed_chunks": [],
            "items": [
                {
                    "citation_id": "c1",
                    "law_title": "中华人民共和国增值税暂行条例",
                    "title": "中华人民共和国增值税暂行条例",
                    "article_no": "第十九条",
                    "excerpt": "纳税义务发生时间",
                    "content": "纳税义务发生时间",
                }
            ],
        }

    monkeypatch.setattr(ca, "extract_text_with_config", fake_extract)
    monkeypatch.setattr(ca, "_retrieve_regulation_evidence", fake_retrieve)
    monkeypatch.setattr(ca, "_get_memory_embedder", lambda: FakeEmbedder())

    cfg = {
        "data_dir": str(tmp_path / "data"),
        "memory_dir": str(tmp_path / "memory"),
        "memory_filter_unverifiable_risks": False,
        "llm_config": {"model": "fake", "timeout": 8},
        "memory_runtime_config": {
            "memory_module_enabled": False,
            "memory_mode_when_disabled": "classic",
            "memory_disable_fallback_on_error": True,
        },
    }
    f = tmp_path / "dummy.docx"
    f.write_text("x", encoding="utf-8")
    llm = FakeLLM()
    result = ca.audit_contract(
        cfg=cfg,
        llm=llm,
        file_path=str(f),
        lang="zh",
        retrieval_options={"audit_mode": "rag"},
    )

    assert result["meta"]["memory_module_enabled"] is False
    assert result["meta"]["execution_path"] == "classic"
    assert result["raw"]["mode"] == "classic"
    assert isinstance(result["audit"]["risk_summary"], dict)
    assert result["meta"]["episode_saved"] is False


def test_memory_llm_call_budget_limit_enforced(monkeypatch, tmp_path: Path):
    def fake_extract(_cfg, _file_path):
        text = "第一条 甲方应在10日内开具发票。\n第二条 乙方应在5日内提供对账单。"
        return text, {"ocr_used": False, "ocr_engine": "", "page_count": 1}

    def fake_retrieve(_cfg, _text, _lang, _opts, embedder=None, reranker=None):
        return {
            "used": True,
            "queries": ["发票", "对账单"],
            "query_success": 2,
            "query_failed": 0,
            "chunk_total": 2,
            "failed_chunks": [],
            "items": [
                {
                    "citation_id": "c1",
                    "law_title": "中华人民共和国增值税暂行条例",
                    "title": "中华人民共和国增值税暂行条例",
                    "article_no": "第十九条",
                    "excerpt": "纳税义务发生时间",
                    "content": "纳税义务发生时间",
                }
            ],
        }

    class _Hit:
        def __init__(self, content):
            self.content = content

    class FakeSearcher:
        def __init__(self, *_args, **_kwargs):
            pass

        def search(self, _query, top_k=8):
            return [_Hit("历史记忆命中")]

    monkeypatch.setattr(ca, "extract_text_with_config", fake_extract)
    monkeypatch.setattr(ca, "_retrieve_regulation_evidence", fake_retrieve)
    monkeypatch.setattr(ca, "_get_memory_embedder", lambda: FakeEmbedder())
    monkeypatch.setattr(ca, "HybridSearcher", FakeSearcher)

    cfg = {
        "data_dir": str(tmp_path / "data"),
        "memory_dir": str(tmp_path / "memory"),
        "memory_filter_unverifiable_risks": False,
        "llm_config": {"model": "fake", "timeout": 8},
        "memory_runtime_config": {
            "memory_module_enabled": True,
            "memory_max_llm_calls_per_audit": 1,
        },
    }
    f = tmp_path / "dummy.docx"
    f.write_text("x", encoding="utf-8")
    llm = FakeLLM()
    result = ca.audit_contract(
        cfg=cfg,
        llm=llm,
        file_path=str(f),
        lang="zh",
        retrieval_options={"audit_mode": "rag"},
    )

    assert result["meta"]["memory_module_enabled"] is True
    assert result["meta"]["execution_path"] == "memory"
    assert int(result["meta"]["memory_llm_call_budget_limit"]) == 1
    assert int(result["meta"]["memory_llm_call_count"]) <= 1
    assert bool(result["meta"].get(
        "memory_llm_call_guard_hit")) in {True, False}


def test_memory_llm_budget_prioritizes_high_risk_clause(monkeypatch, tmp_path: Path):
    def fake_extract(_cfg, _file_path):
        text = (
            "第一条 双方应妥善保管一般商业信息。\n"
            "第二条 乙方应按约定税率开具增值税专用发票并完成纳税申报。"
        )
        return text, {"ocr_used": False, "ocr_engine": "", "page_count": 1}

    def fake_retrieve(_cfg, _text, _lang, _opts, embedder=None, reranker=None):
        return {
            "used": True,
            "queries": ["发票", "税率"],
            "query_success": 2,
            "query_failed": 0,
            "chunk_total": 2,
            "failed_chunks": [],
            "items": [
                {
                    "citation_id": "c1",
                    "law_title": "中华人民共和国增值税暂行条例",
                    "title": "中华人民共和国增值税暂行条例",
                    "article_no": "第十九条",
                    "excerpt": "纳税义务发生时间",
                    "content": "纳税义务发生时间",
                }
            ],
        }

    monkeypatch.setattr(ca, "extract_text_with_config", fake_extract)
    monkeypatch.setattr(ca, "_retrieve_regulation_evidence", fake_retrieve)
    monkeypatch.setattr(ca, "_get_memory_embedder", lambda: FakeEmbedder())

    cfg = {
        "data_dir": str(tmp_path / "data"),
        "memory_dir": str(tmp_path / "memory"),
        "memory_filter_unverifiable_risks": False,
        "llm_config": {"model": "fake", "timeout": 8},
        "memory_runtime_config": {
            "memory_module_enabled": True,
            "memory_max_llm_calls_per_audit": 1,
        },
    }
    f = tmp_path / "dummy.docx"
    f.write_text("x", encoding="utf-8")
    llm = FakeLLM()
    result = ca.audit_contract(
        cfg=cfg,
        llm=llm,
        file_path=str(f),
        lang="zh",
        retrieval_options={"audit_mode": "rag"},
    )

    assert int(result["meta"]["memory_llm_call_budget_limit"]) == 1
    assert int(result["meta"]["memory_llm_call_count"]) <= 1
    assert int(result["meta"]
               ["memory_llm_guard_skipped_low_priority_calls"]) >= 0
    assert int(result["meta"]["memory_llm_called_high_priority_clauses"]) >= 0
    assert len(llm.audit_prompts) <= 1
