from app.services import audit_retrieval as ar


class DummyTranslator:
    def __init__(self, ok=True, text=""):
        self.ok = ok
        self.text = text

    def translate_query(self, text, src_lang="en", target_lang="zh"):
        if self.ok:
            return {"ok": True, "text": self.text or f"中文:{text}"}
        return {"ok": False, "text": text, "error": "failed"}


class DummyEmbedderNoProfile:
    def get_registry_status(self):
        return {"ready": False, "default_language": "zh", "languages": [], "registry": {}}

    def get_embed_profile(self, lang):
        _ = lang
        return None

    def compute_embedding(self, text, is_query=False, lang=None):
        _ = (text, is_query, lang)
        return None


def test_retrieval_uses_translated_route_only_by_default(monkeypatch):
    calls = []

    def fake_search(_cfg, q, _embedder, _reranker=None, target_rag_lang=""):
        calls.append((q.language, q.query, target_rag_lang))
        if q.language == "zh":
            return [{
                "citation_id": "zh:1",
                "final_score": 0.9,
                "content": "中文法规",
                "title": "法规",
                "article_no": "第一条",
            }]
        return [{
            "citation_id": "en:1",
            "final_score": 0.4,
            "content": "english fallback",
            "title": "law",
            "article_no": "1",
        }]

    monkeypatch.setattr(ar, "search_regulations", fake_search)
    cfg = {
        "retrieval_regulation_language": "zh",
        "translation_config": {
            "enabled": True,
            "mode": "dual",
            "source_langs": ["en"],
            "target_lang": "zh",
        }
    }
    opts = ar._normalize_retrieval_options(
        {"audit_mode": "rag", "tax_focus": False, "contract_chunk_max": 1})
    out = ar._retrieve_regulation_evidence(
        cfg=cfg,
        text="Supplier shall issue VAT invoice within 5 days.",
        lang="en",
        retrieval_opts=opts,
        embedder=object(),
        reranker=None,
        translator=DummyTranslator(ok=True, text="供应商应在5日内开具增值税发票"),
    )

    langs = [x[0] for x in calls]
    assert "en" not in langs
    assert langs == ["zh"]
    assert calls[0][2] == "zh"
    assert out["queries"] == 1
    assert out["query_success"] == 1
    assert out["items"][0]["citation_id"] == "zh:1"


def test_retrieval_dual_can_keep_source_query_when_enabled(monkeypatch):
    calls = []

    def fake_search(_cfg, q, _embedder, _reranker=None, target_rag_lang=""):
        calls.append((q.language, q.query, target_rag_lang))
        return [{
            "citation_id": "zh:2",
            "final_score": 0.8,
            "content": "中文法规",
            "title": "法规",
            "article_no": "第二条",
        }]

    monkeypatch.setattr(ar, "search_regulations", fake_search)
    cfg = {
        "retrieval_regulation_language": "zh",
        "translation_config": {
            "enabled": True,
            "mode": "dual",
            "source_langs": ["en"],
            "target_lang": "zh",
            "cross_lang_source_query_enabled": True,
        }
    }
    opts = ar._normalize_retrieval_options(
        {"audit_mode": "rag", "tax_focus": False, "contract_chunk_max": 1})
    out = ar._retrieve_regulation_evidence(
        cfg=cfg,
        text="Payment due in 30 days.",
        lang="en",
        retrieval_opts=opts,
        embedder=object(),
        reranker=None,
        translator=DummyTranslator(ok=True, text="付款应在30日内完成"),
    )

    langs = [x[0] for x in calls]
    assert "en" in langs
    assert "zh" in langs
    assert all(x[2] == "zh" for x in calls)
    assert out["queries"] == 2


def test_retrieval_fallback_to_source_when_translation_failed(monkeypatch):
    calls = []

    def fake_search(_cfg, q, _embedder, _reranker=None, target_rag_lang=""):
        calls.append((q.language, q.query, target_rag_lang))
        return [{
            "citation_id": "en:2",
            "final_score": 0.5,
            "content": "fallback",
            "title": "law",
            "article_no": "3",
        }]

    monkeypatch.setattr(ar, "search_regulations", fake_search)
    cfg = {
        "retrieval_regulation_language": "zh",
        "translation_config": {
            "enabled": True,
            "mode": "dual",
            "source_langs": ["en"],
            "target_lang": "zh",
        }
    }
    opts = ar._normalize_retrieval_options(
        {"audit_mode": "rag", "tax_focus": False, "contract_chunk_max": 1})
    out = ar._retrieve_regulation_evidence(
        cfg=cfg,
        text="Late fee clause",
        lang="en",
        retrieval_opts=opts,
        embedder=object(),
        reranker=None,
        translator=DummyTranslator(ok=False),
    )

    assert out["queries"] == 1
    assert calls[0][0] == "en"
    assert calls[0][2] == "zh"


def test_retrieval_degrades_when_embedder_missing(monkeypatch):
    calls = []

    def fake_search(_cfg, q, _embedder, _reranker=None, target_rag_lang=""):
        calls.append((q.language, q.use_semantic, target_rag_lang))
        return [{
            "citation_id": "zh:3",
            "final_score": 0.7,
            "content": "degraded",
            "title": "法规",
            "article_no": "第三条",
        }]

    monkeypatch.setattr(ar, "search_regulations", fake_search)
    monkeypatch.setattr(ar, "_build_global_embedder_adapter",
                        lambda: DummyEmbedderNoProfile())

    cfg = {
        "retrieval_regulation_language": "zh",
    }
    opts = ar._normalize_retrieval_options(
        {"audit_mode": "rag", "tax_focus": False, "contract_chunk_max": 1, "use_semantic": True})
    out = ar._retrieve_regulation_evidence(
        cfg=cfg,
        text="付款应在30日内完成",
        lang="zh",
        retrieval_opts=opts,
        embedder=None,
        reranker=None,
        translator=None,
    )

    assert out["queries"] == 1
    assert out["query_success"] == 1
    assert out["retrieval_degraded"] is True
    assert "missing_embedder" in out["retrieval_degraded_reasons"]
    assert "semantic_disabled_no_profile:zh" in out["retrieval_degraded_reasons"]
    assert calls[0][1] is False
    assert calls[0][2] == "zh"


def test_retrieval_passes_target_rag_lang_to_search(monkeypatch):
    calls = []

    def fake_search(_cfg, q, _embedder, _reranker=None, target_rag_lang=""):
        calls.append((q.language, target_rag_lang))
        return [{
            "citation_id": f"{target_rag_lang}:1",
            "final_score": 0.6,
            "content": "law",
            "title": "law",
            "article_no": "1",
        }]

    monkeypatch.setattr(ar, "search_regulations", fake_search)
    cfg = {
        "retrieval_regulation_language": "zh",
        "rag_dual_search_enabled": True,
        "rag_search_languages": ["zh", "en"],
        "translation_config": {
            "enabled": False,
        }
    }
    opts = ar._normalize_retrieval_options(
        {"audit_mode": "rag", "tax_focus": False, "contract_chunk_max": 1})
    out = ar._retrieve_regulation_evidence(
        cfg=cfg,
        text="付款应在30日内完成",
        lang="zh",
        retrieval_opts=opts,
        embedder=DummyEmbedderNoProfile(),
        reranker=None,
        translator=None,
    )

    assert out["queries"] == 2
    assert ("zh", "zh") in calls
    assert ("zh", "en") in calls
