import pytest

from app.core.utils import split_articles, tokenize_query, best_sentence, extract_text_with_config, OCRExtractionError


def test_split_articles():
    text = "第一条 内容1\n第二条 内容2"
    items = split_articles(text)
    assert len(items) == 2
    assert items[0] == ("第一条", "内容1")
    assert items[1] == ("第二条", "内容2")


def test_tokenize_query():
    q = "法律法规 search"
    tokens = tokenize_query(q)
    assert "法律法规" in tokens
    assert "search" in tokens


def test_best_sentence():
    text = "句子一。句子二包含关键词。"
    tokens = ["关键词"]
    s, score = best_sentence(text, tokens)
    assert s == "句子二包含关键词"
    assert score == 1


def test_tokenize_query_tax_terms():
    q = "税收征收管理法实施细则 增值税专用缴款书"
    tokens = tokenize_query(q)
    assert "税收征收管理法实施细则" in tokens
    assert "增值税专用缴款书" in tokens


def test_extract_text_with_config_raises_when_ocr_returns_empty(monkeypatch):
    monkeypatch.setattr("app.core.utils._extract_pdf_text", lambda p: ("", 1))

    class _StubManager:
        def __init__(self, cfg):
            pass

        def ocr_pdf(self, path, langs, dpi, doc_type="pdf"):
            return "", 1, "mineru"

    monkeypatch.setattr("app.core.utils.OCREngineManager", _StubManager)

    cfg = {"ocr_enabled": True, "ocr_min_text_length": 10, "ocr_languages": "chi_sim+eng", "ocr_dpi": 220}
    with pytest.raises(OCRExtractionError):
        extract_text_with_config(cfg, "dummy.pdf")


def test_extract_text_with_config_raises_when_ocr_engine_failed(monkeypatch):
    monkeypatch.setattr("app.core.utils._extract_pdf_text", lambda p: ("", 1))

    class _StubManager:
        def __init__(self, cfg):
            pass

        def ocr_pdf(self, path, langs, dpi, doc_type="pdf"):
            raise RuntimeError("No module named 'torchvision'")

    monkeypatch.setattr("app.core.utils.OCREngineManager", _StubManager)

    cfg = {"ocr_enabled": True, "ocr_min_text_length": 10, "ocr_languages": "chi_sim+eng", "ocr_dpi": 220}
    with pytest.raises(OCRExtractionError):
        extract_text_with_config(cfg, "dummy.pdf")
