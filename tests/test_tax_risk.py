import uuid
import json
from datetime import datetime, timezone
import numpy as np
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
    class FakeEmbedder:
        def compute_embedding(self, text, lang="zh"):
            vec = np.zeros(16, dtype=np.float32)
            for i, ch in enumerate(str(text or "")[:64]):
                vec[i % 16] += (ord(ch) % 19) / 19.0
            return vec

    class FakeLLM:
        def __init__(self):
            self.calls = []

        def chat_with_profile(self, messages, task_profile, overrides=None):
            self.calls.append(
                {
                    "task_profile": task_profile,
                    "messages": messages,
                    "overrides": dict(overrides or {}),
                }
            )
            prompt = messages[-1]["content"] if messages else ""
            if "Return ONLY a JSON object" in prompt:
                if 'Contract Clause: "税率按9%执行"' in prompt:
                    return ('{"label":"non_compliant","score":0.91,"reason":"税率与规则不一致"}', {})
                return ('{"label":"not_mentioned","score":0.66,"reason":"未提及关键义务"}', {})
            return ('```json\n{"issue_text":"具体风险说明","suggestion":"请补充修订建议",}\n```', {})

    db_path = tmp_path / "test.db"
    cfg = {
        "db_path": str(db_path),
        "memory_dir": str(tmp_path / "memory"),
        "local_llm": {
            "allow_small_to_main_fallback": True,
            "routing": {"high_risk_force_main": True},
            "execution": {"tax_risk_max_workers": 2},
        },
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
    now = datetime.now(timezone.utc).isoformat()
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

    llm = FakeLLM()
    match_contract_against_rules(
        cfg,
        contract_id,
        operator_id="u1",
        top_k_per_clause=1,
        llm=llm,
        embedder=FakeEmbedder(),
    )
    gen = generate_issues_from_matches(
        cfg, contract_id, operator_id="u1", llm=llm)
    assert gen["total"] == 2
    assert gen["high"] >= 1
    assert gen["medium"] >= 1
    risk_calls = [x for x in llm.calls if x["task_profile"] == "tax_risk_main"]
    assert len(risk_calls) >= 2
    assert any(x["overrides"].get("_model_role") == "main" for x in risk_calls)
    items = list_tax_audit_issues_by_contract(cfg, contract_id)
    assert len(items) == 2
    assert items[0]["issue_text"]
    assert items[0]["suggestion"]
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
