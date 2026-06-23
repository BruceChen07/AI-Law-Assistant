import uuid
from datetime import datetime, timezone
import json
import numpy as np
from app.core.database import init_db, get_conn
from app.services.crud import (
    create_tax_regulation_document,
    create_tax_contract_document,
    replace_contract_clauses,
    list_clause_rule_matches_by_contract,
)
from app.services.tax_matcher import (
    evaluate_clause_rule_match,
    evaluate_clause_rule_match_llm,
    match_contract_against_rules,
)


def test_evaluate_clause_rule_match_three_labels():
    rule = {
        "id": "r1",
        "rule_type": "tax_rate",
        "numeric_constraints": "13%",
        "source_text": "增值税税率13%",
        "article_no": "第一条",
    }
    c1 = {"id": "c1", "clause_text": "税率按13%执行"}
    c2 = {"id": "c2", "clause_text": "税率按9%执行"}
    c3 = {"id": "c3", "clause_text": "双方应依法纳税"}
    m1 = evaluate_clause_rule_match(c1, rule)
    m2 = evaluate_clause_rule_match(c2, rule)
    m3 = evaluate_clause_rule_match(c3, rule)
    assert m1["match_label"] == "compliant"
    assert m2["match_label"] == "non_compliant"
    assert m3["match_label"] == "not_mentioned"


def test_evaluate_clause_rule_match_llm_uses_small_task_profile():
    calls = []

    class FakeLLM:
        def chat_with_profile(self, messages, task_profile, overrides=None):
            calls.append(
                {
                    "messages": messages,
                    "task_profile": task_profile,
                    "overrides": dict(overrides or {}),
                }
            )
            return (
                '{"label":"non_compliant","score":0.92,"reason":"税率不一致"}',
                {"_route": {"selected_role": "small"}},
            )

    clause = {"id": "c1", "clause_text": "税率按9%执行"}
    rule = {
        "id": "r1",
        "source_text": "增值税税率13%",
        "required_action": "",
        "prohibited_action": "",
        "numeric_constraints": "13%",
        "deadline_constraints": "",
        "rule_type": "tax_rate",
        "article_no": "第一条",
    }
    out = evaluate_clause_rule_match_llm(clause, rule, cfg={}, llm=FakeLLM())
    assert out["match_label"] == "non_compliant"
    assert out["match_score"] == 0.92
    assert calls[0]["task_profile"] == "tax_match_small"


def test_evaluate_clause_rule_match_llm_high_risk_uses_cloud_review():
    calls = []

    class FakeLLM:
        def chat_with_profile(self, messages, task_profile, overrides=None):
            payload = {
                "task_profile": task_profile,
                "overrides": dict(overrides or {}),
            }
            calls.append(payload)
            if payload["overrides"].get("_model_role") == "cloud_fallback":
                return ('{"label":"non_compliant","score":0.96,"reason":"cloud review confirmed"}', {"_route": {"selected_role": "cloud_fallback"}})
            return ('{"label":"non_compliant","score":0.72,"reason":"local review"}', {"_route": {"selected_role": "small"}})

    cfg = {
        "local_llm": {
            "cloud_fallback_enabled": True,
            "routing": {"high_risk_force_cloud": True},
            "execution": {"tax_match_cloud_review_labels": ["non_compliant"]},
        }
    }
    clause = {"id": "c1", "clause_text": "税率按9%执行"}
    rule = {
        "id": "r1",
        "source_text": "增值税税率13%",
        "required_action": "",
        "prohibited_action": "",
        "numeric_constraints": "13%",
        "deadline_constraints": "",
        "rule_type": "tax_rate",
        "article_no": "第一条",
    }
    out = evaluate_clause_rule_match_llm(clause, rule, cfg=cfg, llm=FakeLLM())
    evidence = json.loads(out["evidence_json"])
    assert out["match_label"] == "non_compliant"
    assert out["match_score"] == 0.96
    assert evidence["fallback_used"] is True
    assert evidence["fallback_reason"] == "high_risk_review"
    assert any(item["overrides"].get("_model_role")
               == "cloud_fallback" for item in calls)


def test_match_contract_against_rules_end_to_end(tmp_path):
    class FakeEmbedder:
        def compute_embedding(self, text, lang="zh"):
            vec = np.zeros(16, dtype=np.float32)
            for i, ch in enumerate(str(text or "")[:64]):
                vec[i % 16] += (ord(ch) % 17) / 17.0
            return vec

    class FakeLLM:
        def chat(self, messages, overrides=None):
            return ('{"label":"not_mentioned","score":0.2,"reason":"fallback"}', {})

    db_path = tmp_path / "test.db"
    cfg = {"db_path": str(db_path)}
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
                "clause_text": "税率13%", "entities_json": "{}"},
            {"clause_path": "1.2", "page_no": 1, "paragraph_no": "2",
                "clause_text": "税率9%", "entities_json": "{}"},
            {"clause_path": "1.3", "page_no": 1, "paragraph_no": "3",
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

    result = match_contract_against_rules(
        cfg,
        contract_id,
        operator_id="u1",
        top_k_per_clause=1,
        llm=FakeLLM(),
        embedder=FakeEmbedder(),
    )
    assert result["total_matches"] == 3
    assert result["compliant_count"] >= 1
    assert result["non_compliant_count"] >= 1
    assert result["not_mentioned_count"] >= 1
    items = list_clause_rule_matches_by_contract(cfg, contract_id, limit=20)
    labels = {x["match_label"] for x in items}
    assert "compliant" in labels
    assert "non_compliant" in labels
    assert "not_mentioned" in labels
