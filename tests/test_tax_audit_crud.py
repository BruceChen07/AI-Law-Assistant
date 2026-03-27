import uuid
from datetime import datetime
from app.core.database import init_db, get_conn
from app.services.crud import (
    create_tax_regulation_document,
    create_tax_contract_document,
    list_tax_audit_issues_by_contract,
)


def test_tax_audit_tables_and_crud(tmp_path):
    db_path = tmp_path / "test.db"
    cfg = {"db_path": str(db_path)}
    init_db(cfg)

    regulation_id = str(uuid.uuid4())
    create_tax_regulation_document(
        cfg=cfg,
        document_id=regulation_id,
        original_filename="regulation.pdf",
        file_path=str(tmp_path / "regulation.pdf"),
        file_type="pdf",
        file_size=123,
        uploaded_by="u1",
        checksum="abc",
        parse_status="pending",
    )

    contract_id = str(uuid.uuid4())
    create_tax_contract_document(
        cfg=cfg,
        document_id=contract_id,
        original_filename="contract.docx",
        file_path=str(tmp_path / "contract.docx"),
        file_type="docx",
        file_size=456,
        uploaded_by="u1",
        parse_status="pending",
        ocr_used=0,
    )

    conn = get_conn(cfg)
    cur = conn.cursor()
    rule_id = str(uuid.uuid4())
    clause_id = str(uuid.uuid4())
    issue_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    cur.execute(
        """
        INSERT INTO tax_rule(
            id, regulation_document_id, law_title, article_no, rule_type,
            trigger_condition, required_action, prohibited_action,
            numeric_constraints, deadline_constraints, region, industry,
            effective_date, expiry_date, source_page, source_paragraph,
            source_text, created_by, created_at, updated_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            rule_id,
            regulation_id,
            "示例财税法规",
            "第一条",
            "tax_rate",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            1,
            "1",
            "文本",
            "u1",
            now,
            now,
        ),
    )
    cur.execute(
        """
        INSERT INTO contract_clause(
            id, contract_document_id, clause_path, page_no, paragraph_no,
            clause_text, entities_json, created_by, created_at, updated_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        (
            clause_id,
            contract_id,
            "1.1",
            2,
            "3",
            "合同条款示例",
            "{}",
            "u1",
            now,
            now,
        ),
    )
    cur.execute(
        """
        INSERT INTO audit_issue(
            id, contract_document_id, clause_id, rule_id, risk_level, issue_text,
            suggestion, reviewer_status, reviewer_note, created_by, created_at, updated_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            issue_id,
            contract_id,
            clause_id,
            rule_id,
            "high",
            "风险项",
            "建议",
            "pending",
            "",
            "u1",
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()

    items = list_tax_audit_issues_by_contract(cfg, contract_id)
    assert len(items) == 1
    assert items[0]["id"] == issue_id
    assert items[0]["risk_level"] == "high"
