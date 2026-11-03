import os
import uuid
from app.core.database import init_db
from app.services.crud import (
    create_tax_regulation_document,
    count_tax_rules_by_document,
    get_tax_regulation_document,
)
from app.services.tax_parser import (
    split_tax_clauses,
    extract_tax_fields,
    parse_regulation_document,
    extract_regulation_text,
)


def test_split_tax_clauses():
    text = "第一条 一般纳税人适用增值税税率13%。\n第二条 纳税人应当在30日内申报。"
    items = split_tax_clauses(text)
    assert len(items) >= 2
    assert items[0]["article_no"].startswith("第一")


def test_extract_tax_fields():
    clause = {
        "article_no": "第一条",
        "source_text": "一般纳税人适用增值税税率13%，并应当在30日内申报。",
        "source_page": 1,
        "source_paragraph": "1",
    }
    rule = extract_tax_fields(clause, law_title="测试法规")
    assert rule["rule_type"] in ["tax_rate", "mandatory_action", "deadline"]
    assert "13%" in rule["numeric_constraints"]
    assert "30日内" in rule["deadline_constraints"]


def test_parse_regulation_document_end_to_end(tmp_path):
    db_path = tmp_path / "test.db"
    file_path = tmp_path / "sample.doc"
    html = """
    <html><body>
    <h3>测试财税法规</h3>
    <p>第一条 一般纳税人适用增值税税率13%。</p>
    <p>第二条 纳税人应当在30日内完成申报，不得虚开发票。</p>
    </body></html>
    """
    file_path.write_text(html, encoding="utf-8")
    cfg = {
        "db_path": str(db_path),
        "ocr_languages": "chi_sim+eng",
        "ocr_dpi": 220,
    }
    init_db(cfg)
    document_id = str(uuid.uuid4())
    create_tax_regulation_document(
        cfg=cfg,
        document_id=document_id,
        original_filename="sample.doc",
        file_path=str(file_path),
        file_type="doc",
        file_size=os.path.getsize(file_path),
        uploaded_by="u1",
        checksum="x",
        parse_status="pending",
    )
    result = parse_regulation_document(cfg, document_id, operator_id="u1")
    assert result["parse_status"] == "done"
    assert result["rule_count"] >= 2
    assert count_tax_rules_by_document(cfg, document_id) >= 2
    doc = get_tax_regulation_document(cfg, document_id)
    assert doc["parse_status"] == "done"


def test_extract_regulation_text_image_uses_ocr_manager(monkeypatch, tmp_path):
    image_path = tmp_path / "demo.png"
    image_path.write_bytes(b"fake-image")

    class _StubManager:
        def __init__(self, cfg):
            self.cfg = cfg

        def ocr_document(self, path, lang, dpi, doc_type="pdf"):
            assert path == str(image_path)
            assert doc_type == "image"
            return "识别文本", 1, "mineru"

    monkeypatch.setattr("app.services.tax_parser.OCREngineManager", _StubManager)
    text, meta = extract_regulation_text(
        {"ocr_languages": "chi_sim+eng", "ocr_dpi": 220},
        str(image_path),
        "png",
    )
    assert text == "识别文本"
    assert meta["ocr_used"] is True
