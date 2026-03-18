from pathlib import Path

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
