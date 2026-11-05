"""文本抽取缓存：审计与摘要共用一次 OCR。"""

from app.core import utils


def _fake_extract(calls):
    def _impl(cfg, path):
        calls.append(path)
        return "合同正文", {
            "ext": ".pdf",
            "ocr_used": True,
            "page_count": 7,
            "ocr_engine": "mineru",
        }

    return _impl


def test_extraction_cached_by_file_content(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(utils, "_extract_text_with_config_uncached", _fake_extract(calls))
    cfg = {"data_dir": str(tmp_path)}
    pdf = tmp_path / "contract.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake content")

    first_text, first_meta = utils.extract_text_with_config(cfg, str(pdf))
    second_text, second_meta = utils.extract_text_with_config(cfg, str(pdf))

    assert first_text == second_text == "合同正文"
    assert first_meta == second_meta
    assert len(calls) == 1, "同一文件不应重复抽取"


def test_different_content_is_extracted_separately(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(utils, "_extract_text_with_config_uncached", _fake_extract(calls))
    cfg = {"data_dir": str(tmp_path)}
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    a.write_bytes(b"%PDF-1.4 a")
    b.write_bytes(b"%PDF-1.4 b")

    utils.extract_text_with_config(cfg, str(a))
    utils.extract_text_with_config(cfg, str(b))

    assert len(calls) == 2


def test_txt_bypasses_cache(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(utils, "_extract_text_with_config_uncached", _fake_extract(calls))
    cfg = {"data_dir": str(tmp_path)}
    txt = tmp_path / "note.txt"
    txt.write_text("hello", encoding="utf-8")

    utils.extract_text_with_config(cfg, str(txt))
    utils.extract_text_with_config(cfg, str(txt))

    assert len(calls) == 2, "txt 不进入哈希缓存，每次都走原有路径"
