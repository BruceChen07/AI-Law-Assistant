import uuid
import json
from datetime import datetime
from app.core.database import init_db, get_conn
from app.services.crud import (
    create_tax_regulation_document,
    create_tax_contract_document,
    replace_contract_clauses,
    list_tax_audit_issues_by_contract,
    list_audit_trace_by_issue,
)
from app.services.tax_matcher import match_contract_against_rules
from app.services.tax_risk import generate_issues_from_matches, review_audit_issue


def test_generate_and_review_tax_audit_issues(tmp_path):
    db_path = tmp_path / "test.db"
    cfg = {
        "db_path": str(db_path),
        "memory_dir": str(tmp_path / "memory"),
    }
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
            {"clause_path": "1.1", "page_no": 1, "paragraph_no": "1",
                "clause_text": "税率按9%执行", "entities_json": "{}"},
            {"clause_path": "1.2", "page_no": 1, "paragraph_no": "2",
                "clause_text": "双方按法规办理", "entities_json": "{}"},
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

    match_contract_against_rules(
        cfg, contract_id, operator_id="u1", top_k_per_clause=1)
    gen = generate_issues_from_matches(cfg, contract_id, operator_id="u1")
    assert gen["total"] == 2
    assert gen["high"] >= 1
    assert gen["medium"] >= 1
    items = list_tax_audit_issues_by_contract(cfg, contract_id)
    assert len(items) == 2
    issue_id = items[0]["id"]
    reviewed = review_audit_issue(
        cfg,
        issue_id=issue_id,
        reviewer_status="confirmed",
        reviewer_note="确认风险",
        operator_id="reviewer1",
    )
    assert reviewed["reviewer_status"] == "confirmed"
    traces = list_audit_trace_by_issue(cfg, issue_id, limit=20)
    assert len(traces) == 1
    assert traces[0]["action_type"] == "reviewer_confirm"
    feedback_file = tmp_path / "memory" / "experience" / "feedback_events.jsonl"
    assert feedback_file.exists()
    rows = [json.loads(x) for x in feedback_file.read_text(
        encoding="utf-8").splitlines() if x.strip()]
    assert len(rows) >= 1
    row = rows[-1]
    assert row["issue_id"] == issue_id
    assert row["outcome"] == "success"
    assert row["feedback_source"] == "user_confirmed"
    assert float(row["memory_quality_score"]) > 0.5
