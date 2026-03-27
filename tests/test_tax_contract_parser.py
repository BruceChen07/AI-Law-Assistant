import os
import uuid
from app.core.database import init_db
from app.services.crud import (
    create_tax_contract_document,
    count_contract_clauses,
    get_tax_contract_document,
    list_contract_clauses,
)
from app.services.tax_contract_parser import (
    split_contract_clauses,
    extract_clause_entities,
    analyze_contract_document,
)


def test_split_contract_clauses():
    text = "1.1 甲方应在30日内开具增值税专用发票。\n1.2 如逾期，乙方可暂停付款。"
    clauses = split_contract_clauses(text)
    assert len(clauses) >= 2
    assert clauses[0]["clause_path"] == "1.1"


def test_extract_clause_entities():
    entities = extract_clause_entities("甲方应在30日内开具增值税专用发票，税率13%，金额100万元。")
    assert entities["invoice_type"] in ["专用发票", "增值税专用发票"]
    assert entities["tax_rate"] == "13%"
    assert entities["invoice_time"] == "30日内"
    assert entities["amount"] == "100万元"


def test_analyze_contract_document_end_to_end(tmp_path):
    db_path = tmp_path / "test.db"
    file_path = tmp_path / "contract.txt"
    file_path.write_text(
        "第一条 甲方应在30日内开具增值税专用发票，税率13%。\n第二条 乙方付款金额100万元。",
        encoding="utf-8",
    )
    cfg = {
        "db_path": str(db_path),
        "ocr_languages": "chi_sim+eng",
        "ocr_dpi": 220,
    }
    init_db(cfg)
    contract_id = str(uuid.uuid4())
    create_tax_contract_document(
        cfg=cfg,
        document_id=contract_id,
        original_filename="contract.txt",
        file_path=str(file_path),
        file_type="txt",
        file_size=os.path.getsize(file_path),
        uploaded_by="u1",
        parse_status="pending",
        ocr_used=0,
    )
    result = analyze_contract_document(cfg, contract_id, operator_id="u1")
    assert result["parse_status"] == "done"
    assert result["language"] == "zh"
    assert result["clause_count"] >= 2
    assert count_contract_clauses(cfg, contract_id) >= 2
    clauses = list_contract_clauses(cfg, contract_id, limit=20)
    assert len(clauses) >= 2
    doc = get_tax_contract_document(cfg, contract_id)
    assert doc["parse_status"] == "done"
