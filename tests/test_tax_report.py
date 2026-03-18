import os
import uuid
import logging
from datetime import datetime
from app.core.database import init_db, get_conn
from app.services.crud import (
    create_tax_regulation_document,
    create_tax_contract_document,
    replace_contract_clauses,
)
from app.services.tax_matcher import match_contract_against_rules
from app.services.tax_risk import generate_issues_from_matches, review_audit_issue
from app.services.tax_report import build_tax_audit_report, export_tax_audit_report


def test_build_and_export_tax_audit_report(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="law_assistant")
    db_path = tmp_path / "test.db"
    files_dir = tmp_path / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    cfg = {"db_path": str(db_path), "files_dir": str(files_dir)}
    init_db(cfg)

    reg_id = str(uuid.uuid4())
    create_tax_regulation_document(
        cfg=cfg,
        document_id=reg_id,
        original_filename="reg.pdf",
        file_path=str(tmp_path / "reg.pdf"),
        file_type="pdf",
        file_size=100,
        uploaded_by="u1",
        checksum="x1",
        parse_status="done",
    )

    contract_id = str(uuid.uuid4())
    create_tax_contract_document(
        cfg=cfg,
        document_id=contract_id,
        original_filename="contract.docx",
        file_path=str(tmp_path / "contract.docx"),
        file_type="docx",
        file_size=200,
        uploaded_by="u1",
        parse_status="done",
        ocr_used=0,
    )
    replace_contract_clauses(
        cfg,
        contract_id,
        [
            {"clause_path": "1.1", "page_no": 1, "paragraph_no": "1", "clause_text": "税率按9%执行", "entities_json": "{}"},
            {"clause_path": "1.2", "page_no": 1, "paragraph_no": "2", "clause_text": "双方按法规办理", "entities_json": "{}"},
        ],
        created_by="u1",
    )

    conn = get_conn(cfg)
    cur = conn.cursor()
    now = datetime.utcnow().isoformat()
    cur.execute(
        """
        INSERT INTO tax_rule(
            id, regulation_document_id, law_title, article_no, rule_type,
            trigger_condition, required_action, prohibited_action, numeric_constraints,
            deadline_constraints, region, industry, effective_date, expiry_date,
            source_page, source_paragraph, source_text, created_by, created_at, updated_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            str(uuid.uuid4()),
            reg_id,
            "示例法规",
            "第一条",
            "tax_rate",
            "",
            "",
            "",
            "13%",
            "",
            "",
            "",
            "",
            "",
            1,
            "1",
            "增值税税率13%",
            "u1",
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()

    match_contract_against_rules(cfg, contract_id, operator_id="u1", top_k_per_clause=1)
    generate_issues_from_matches(cfg, contract_id, operator_id="u1")
    report = build_tax_audit_report(cfg, contract_id)
    assert report["contract_id"] == contract_id
    assert report["risk_summary"]["total"] == 2
    assert len(report["risk_items"]) == 2

    issue_id = report["risk_items"][0]["issue_id"]
    review_audit_issue(
        cfg,
        issue_id=issue_id,
        reviewer_status="exception",
        reviewer_note="业务例外",
        operator_id="reviewer1",
    )
    reviewed_report = build_tax_audit_report(cfg, contract_id)
    assert reviewed_report["review_summary"]["exception"] == 1
    assert len(reviewed_report["review_conclusions"]) >= 1
    assert len(reviewed_report["exception_items"]) == 1

    exported = export_tax_audit_report(cfg, contract_id, export_format="json")
    assert exported["export_format"] == "json"
    assert os.path.exists(exported["file_path"])
    assert exported["size"] > 0
    messages = "\n".join([r.getMessage() for r in caplog.records])
    assert "tax_match_start" in messages
    assert "tax_risk_generate_done" in messages
    assert "tax_report_build_done" in messages
    assert "tax_report_export_done" in messages
