from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_app_embedder, get_app_llm, get_current_user
from app.api.routers import tax_audit as tax_audit_router
from app.core.database import init_db, get_conn
from app.services.audit_capabilities import (
    create_agent_profile,
    ensure_audit_capabilities_seeded,
)
from app.services.crud import (
    create_audit_issues,
    create_clause_rule_matches,
    create_tax_contract_document,
    list_audit_trace_by_contract,
    list_evidence_anchors_by_contract,
    replace_contract_clauses,
)
from app.services.tax_pipeline_runtime import run_tax_pipeline_with_runtime
from app.services.tax_pipeline_runtime import (
    get_runtime_session,
    list_runtime_sessions,
    list_runtime_skill_runs,
    replay_runtime_session,
)


def _build_cfg(tmp_path: Path) -> dict:
    files_dir = tmp_path / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    return {
        "db_path": str(tmp_path / "app.db"),
        "files_dir": str(files_dir),
        "data_dir": str(tmp_path / "data"),
        "static_dir": str(tmp_path / "static"),
    }


def test_run_tax_pipeline_with_runtime_persists_trace_and_evidence(tmp_path, monkeypatch):
    cfg = _build_cfg(tmp_path)
    init_db(cfg)
    ensure_audit_capabilities_seeded(cfg)

    contract_id = "contract-1"
    create_tax_contract_document(
        cfg=cfg,
        document_id=contract_id,
        original_filename="contract.docx",
        file_path=str(tmp_path / "contract.docx"),
        file_type="docx",
        file_size=128,
        uploaded_by="user-1",
        parse_status="done",
        ocr_used=0,
    )
    replace_contract_clauses(
        cfg,
        contract_id,
        [
            {
                "clause_path": "1.1",
                "page_no": 1,
                "paragraph_no": "1",
                "clause_text": "甲方收到发票后45日内付款，适用税率9%。",
                "entities_json": "{}",
            }
        ],
        created_by="user-1",
    )

    clause_id = ""
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM contract_clause WHERE contract_document_id=?", (contract_id,))
    row = cur.fetchone()
    clause_id = str(row["id"])
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
            "rule-1",
            "reg-1",
            "增值税法",
            "第一条",
            "invoice",
            "",
            "应开具发票",
            "",
            "",
            "",
            "CN",
            "general",
            "2026-01-01",
            "",
            1,
            "1",
            "纳税人提供服务应依法开具发票。",
            "user-1",
            "2026-07-18T00:00:00+00:00",
            "2026-07-18T00:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()

    profile = create_agent_profile(
        cfg,
        owner_id="user-1",
        display_name="runtime-agent",
        template_id="tax_contract_audit_default",
    )

    def fake_analyze(_cfg, _services, *, contract_id: str, operator_id: str = ""):
        return {
            "contract_id": contract_id,
            "parse_status": "done",
            "language": "zh",
            "clause_count": 1,
            "ocr_used": False,
            "started_at": "2026-07-18T00:00:00+00:00",
            "finished_at": "2026-07-18T00:00:01+00:00",
        }

    def fake_match(
        _cfg,
        _services,
        *,
        contract_id: str,
        operator_id: str = "",
        top_k_per_clause: int = 5,
        rule_pack_selectors=None,
    ):
        _ = top_k_per_clause
        assert isinstance(rule_pack_selectors, list)
        create_clause_rule_matches(
            _cfg,
            [
                {
                    "clause_id": clause_id,
                    "rule_id": "rule-1",
                    "match_score": 0.92,
                    "match_label": "non_compliant",
                    "evidence_json": (
                        '{"reason":"tax mismatch","clause_excerpt":"甲方收到发票后45日内付款，适用税率9%。",'
                        '"rule_excerpt":"纳税人提供服务应依法开具发票。"}'
                    ),
                }
            ],
            created_by=operator_id,
        )
        return {
            "contract_id": contract_id,
            "total_matches": 1,
            "compliant_count": 0,
            "non_compliant_count": 1,
            "not_mentioned_count": 0,
        }

    def fake_issues(_cfg, _services, *, contract_id: str, operator_id: str = ""):
        create_audit_issues(
            _cfg,
            [
                {
                    "contract_document_id": contract_id,
                    "clause_id": clause_id,
                    "rule_id": "rule-1",
                    "risk_level": "high",
                    "issue_text": "税率与付款时限存在风险",
                    "suggestion": "补充开票与付款约束",
                    "reviewer_status": "pending",
                    "reviewer_note": "",
                }
            ],
            created_by=operator_id,
        )
        return {"contract_id": contract_id, "total": 1, "high": 1, "medium": 0, "low": 0}

    def fake_report(_cfg, *, contract_id: str):
        return {
            "contract_id": contract_id,
            "language": "zh",
            "generated_at": "2026-07-18T00:00:02+00:00",
            "overview": {
                "contract_filename": "contract.docx",
                "contract_parse_status": "done",
                "ocr_used": False,
                "clause_count": 1,
                "issue_count": 1,
                "trace_count": 1,
            },
            "risk_summary": {"total": 1, "high": 1, "medium": 0, "low": 0},
            "review_summary": {"confirmed": 0, "rejected": 0, "downgraded": 0, "exception": 0, "pending": 1},
            "risk_items": [],
            "evidence_items": [],
            "review_conclusions": [],
            "exception_items": [],
        }

    monkeypatch.setattr(
        "app.services.tax_pipeline_runtime.tax_analyze_contract", fake_analyze)
    monkeypatch.setattr(
        "app.services.tax_pipeline_runtime.tax_match_contract", fake_match)
    monkeypatch.setattr(
        "app.services.tax_pipeline_runtime.tax_generate_issues", fake_issues)
    monkeypatch.setattr(
        "app.services.tax_pipeline_runtime.tax_build_report", fake_report)

    result = run_tax_pipeline_with_runtime(
        cfg,
        services=object(),
        contract_id=contract_id,
        owner_id="user-1",
        operator_id="user-1",
        profile_id=profile["id"],
    )
    assert result["runtime"]["agent_profile_id"] == profile["id"]
    assert result["runtime"]["session_id"]
    assert "evidence_pack_skill" in result["runtime"]["planned_skill_ids"]
    assert result["runtime"]["rule_pack_pins"][0]["pack_id"] == "tax_core_default"
    assert result["runtime"]["artifact_delta"]["trace_count"] == 1
    assert result["runtime"]["artifact_delta"]["evidence_anchor_count"] == 1
    assert result["runtime"]["evidence_pack"]["summary"]["issue_count"] == 1

    session_id = result["runtime"]["session_id"]
    session = get_runtime_session(
        cfg, session_id=session_id, owner_id="user-1")
    assert session is not None
    assert session["status"] == "completed"
    assert session["runtime"]["session_id"] == session_id

    sessions = list_runtime_sessions(
        cfg, owner_id="user-1", contract_id=contract_id)
    assert len(sessions) == 1
    assert sessions[0]["id"] == session_id

    skill_runs = list_runtime_skill_runs(
        cfg, session_id=session_id, owner_id="user-1")
    assert len(skill_runs) >= 4
    assert skill_runs[0]["skill_id"] == "entity_extract_skill"
    assert any(item["status"] == "completed" for item in skill_runs)

    traces = list_audit_trace_by_contract(cfg, contract_id)
    anchors = list_evidence_anchors_by_contract(cfg, contract_id)
    assert len(traces) == 1
    assert traces[0]["action_type"] == "agent_runtime_profile"
    assert len(anchors) == 1
    assert anchors[0]["issue_id"]


def test_replay_runtime_session_creates_new_session(tmp_path, monkeypatch):
    cfg = _build_cfg(tmp_path)
    init_db(cfg)
    ensure_audit_capabilities_seeded(cfg)

    contract_id = "contract-2"
    create_tax_contract_document(
        cfg=cfg,
        document_id=contract_id,
        original_filename="contract-replay.docx",
        file_path=str(tmp_path / "contract-replay.docx"),
        file_type="docx",
        file_size=128,
        uploaded_by="user-1",
        parse_status="done",
        ocr_used=0,
    )
    replace_contract_clauses(
        cfg,
        contract_id,
        [{"clause_path": "1.1", "page_no": 1, "paragraph_no": "1",
            "clause_text": "应按发票付款。", "entities_json": "{}"}],
        created_by="user-1",
    )
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM contract_clause WHERE contract_document_id=?", (contract_id,))
    clause_id = str(cur.fetchone()["id"])
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
            "rule-replay",
            "reg-1",
            "增值税法",
            "第一条",
            "invoice",
            "",
            "应开具发票",
            "",
            "",
            "",
            "CN",
            "general",
            "2026-01-01",
            "",
            1,
            "1",
            "纳税人提供服务应依法开具发票。",
            "user-1",
            "2026-07-18T00:00:00+00:00",
            "2026-07-18T00:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()
    profile = create_agent_profile(
        cfg,
        owner_id="user-1",
        display_name="runtime-agent-replay",
        template_id="tax_contract_audit_default",
    )

    monkeypatch.setattr(
        "app.services.tax_pipeline_runtime.tax_analyze_contract",
        lambda *_args, **kwargs: {
            "contract_id": kwargs["contract_id"],
            "parse_status": "done",
            "language": "zh",
            "clause_count": 1,
            "ocr_used": False,
            "started_at": "2026-07-18T00:00:00+00:00",
            "finished_at": "2026-07-18T00:00:01+00:00",
        },
    )

    def fake_match(_cfg, _services, *, contract_id: str, operator_id: str = "", **_kwargs):
        create_clause_rule_matches(
            _cfg,
            [{
                "clause_id": clause_id,
                "rule_id": "rule-replay",
                "match_score": 0.88,
                "match_label": "non_compliant",
                "evidence_json": '{"reason":"replay","clause_excerpt":"应按发票付款。","rule_excerpt":"应依法开票"}',
            }],
            created_by=operator_id,
        )
        return {
            "contract_id": contract_id,
            "total_matches": 1,
            "compliant_count": 0,
            "non_compliant_count": 1,
            "not_mentioned_count": 0,
        }

    monkeypatch.setattr(
        "app.services.tax_pipeline_runtime.tax_match_contract", fake_match)
    monkeypatch.setattr(
        "app.services.tax_pipeline_runtime.tax_generate_issues",
        lambda _cfg, _services, *, contract_id, operator_id="": {
            "contract_id": contract_id,
            "total": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
        },
    )
    monkeypatch.setattr(
        "app.services.tax_pipeline_runtime.tax_build_report",
        lambda _cfg, *, contract_id: {
            "contract_id": contract_id,
            "language": "zh",
            "generated_at": "2026-07-18T00:00:02+00:00",
            "overview": {"contract_filename": "contract-replay.docx", "contract_parse_status": "done", "ocr_used": False, "clause_count": 1, "issue_count": 0, "trace_count": 0},
            "risk_summary": {"total": 0, "high": 0, "medium": 0, "low": 0},
            "review_summary": {"confirmed": 0, "rejected": 0, "downgraded": 0, "exception": 0, "pending": 0},
            "risk_items": [],
            "evidence_items": [],
            "review_conclusions": [],
            "exception_items": [],
        },
    )

    first = run_tax_pipeline_with_runtime(
        cfg,
        services=object(),
        contract_id=contract_id,
        owner_id="user-1",
        operator_id="user-1",
        profile_id=profile["id"],
    )
    replayed = replay_runtime_session(
        cfg,
        services=object(),
        session_id=first["runtime"]["session_id"],
        owner_id="user-1",
        operator_id="user-1",
    )
    assert replayed["runtime"]["session_id"] != first["runtime"]["session_id"]
    replay_session = get_runtime_session(
        cfg,
        session_id=replayed["runtime"]["session_id"],
        owner_id="user-1",
    )
    assert replay_session is not None
    assert replay_session["replay_of_session_id"] == first["runtime"]["session_id"]
    assert replayed["runtime"]["rule_pack_pins"] == first["runtime"]["rule_pack_pins"]


def test_tax_pipeline_route_returns_runtime_payload(tmp_path, monkeypatch):
    cfg = _build_cfg(tmp_path)
    init_db(cfg)
    ensure_audit_capabilities_seeded(cfg)

    app = FastAPI()
    app.include_router(tax_audit_router.build_router(cfg))
    app.dependency_overrides[get_current_user] = lambda: {
        "id": "user-1", "role": "user"}
    app.dependency_overrides[get_app_llm] = lambda: object()
    app.dependency_overrides[get_app_embedder] = lambda: object()
    client = TestClient(app)

    monkeypatch.setattr(
        tax_audit_router,
        "run_tax_pipeline_with_runtime",
        lambda *_args, **kwargs: {
            "contract_id": kwargs["contract_id"],
            "analyze": {
                "contract_id": kwargs["contract_id"],
                "parse_status": "done",
                "language": "zh",
                "clause_count": 1,
                "ocr_used": False,
                "started_at": "2026-07-18T00:00:00+00:00",
                "finished_at": "2026-07-18T00:00:01+00:00",
            },
            "match": {
                "contract_id": kwargs["contract_id"],
                "total_matches": 1,
                "compliant_count": 0,
                "non_compliant_count": 1,
                "not_mentioned_count": 0,
            },
            "issues": {
                "contract_id": kwargs["contract_id"],
                "total": 1,
                "high": 1,
                "medium": 0,
                "low": 0,
            },
            "report": None,
            "runtime": {
                "agent_profile_id": kwargs["profile_id"],
                "planned_skill_ids": ["entity_extract_skill", "evidence_pack_skill"],
                "rule_pack_pins": [{"pack_id": "tax_core_default", "version_no": 1}],
            },
        },
    )
    monkeypatch.setattr(
        tax_audit_router,
        "list_runtime_sessions",
        lambda *_args, **kwargs: [{
            "id": "session-1",
            "contract_document_id": kwargs["contract_id"],
            "owner_id": kwargs["owner_id"],
            "operator_id": kwargs["owner_id"],
            "agent_profile_id": "profile-1",
            "template_id": "tax_contract_audit_default",
            "replay_of_session_id": None,
            "sandbox_mode": "builtin_only",
            "status": "completed",
            "request": {"contract_id": kwargs["contract_id"]},
            "runtime": {"session_id": "session-1"},
            "result": {"contract_id": kwargs["contract_id"]},
            "error_message": None,
            "started_at": "2026-07-18T00:00:00+00:00",
            "finished_at": "2026-07-18T00:00:05+00:00",
            "created_at": "2026-07-18T00:00:00+00:00",
            "updated_at": "2026-07-18T00:00:05+00:00",
        }],
    )
    monkeypatch.setattr(
        tax_audit_router,
        "get_runtime_session",
        lambda *_args, **kwargs: {
            "id": kwargs["session_id"],
            "contract_document_id": "contract-1",
            "owner_id": kwargs["owner_id"],
            "operator_id": kwargs["owner_id"],
            "agent_profile_id": "profile-1",
            "template_id": "tax_contract_audit_default",
            "replay_of_session_id": None,
            "sandbox_mode": "builtin_only",
            "status": "completed",
            "request": {"contract_id": "contract-1"},
            "runtime": {"session_id": kwargs["session_id"]},
            "result": {"contract_id": "contract-1"},
            "error_message": None,
            "started_at": "2026-07-18T00:00:00+00:00",
            "finished_at": "2026-07-18T00:00:05+00:00",
            "created_at": "2026-07-18T00:00:00+00:00",
            "updated_at": "2026-07-18T00:00:05+00:00",
        },
    )
    monkeypatch.setattr(
        tax_audit_router,
        "list_runtime_skill_runs",
        lambda *_args, **kwargs: [{
            "id": "skill-run-1",
            "session_id": kwargs["session_id"],
            "skill_id": "entity_extract_skill",
            "stage": "analyze",
            "sandbox_mode": "builtin_only",
            "status": "completed",
            "llm_cost": 0,
            "position_no": 1,
            "reason": "",
            "input_summary": {"contract_id": "contract-1"},
            "output_summary": {"type": "dict"},
            "error_message": None,
            "started_at": "2026-07-18T00:00:00+00:00",
            "finished_at": "2026-07-18T00:00:01+00:00",
            "created_at": "2026-07-18T00:00:00+00:00",
            "updated_at": "2026-07-18T00:00:01+00:00",
        }],
    )
    monkeypatch.setattr(
        tax_audit_router,
        "replay_runtime_session",
        lambda *_args, **kwargs: {
            "contract_id": "contract-1",
            "analyze": {
                "contract_id": "contract-1",
                "parse_status": "done",
                "language": "zh",
                "clause_count": 1,
                "ocr_used": False,
                "started_at": "2026-07-18T00:01:00+00:00",
                "finished_at": "2026-07-18T00:01:01+00:00",
            },
            "match": {
                "contract_id": "contract-1",
                "total_matches": 1,
                "compliant_count": 0,
                "non_compliant_count": 1,
                "not_mentioned_count": 0,
            },
            "issues": {
                "contract_id": "contract-1",
                "total": 1,
                "high": 1,
                "medium": 0,
                "low": 0,
            },
            "report": None,
            "runtime": {"session_id": "session-2", "replay_of_session_id": kwargs["session_id"]},
        },
    )

    resp = client.post(
        "/tax-audit/contracts/contract-1/pipeline/run?profile_id=profile-1")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["contract_id"] == "contract-1"
    assert payload["runtime"]["agent_profile_id"] == "profile-1"
    assert payload["runtime"]["rule_pack_pins"][0]["pack_id"] == "tax_core_default"

    sessions_resp = client.get("/tax-audit/contracts/contract-1/sessions")
    assert sessions_resp.status_code == 200
    assert sessions_resp.json()["total"] == 1

    session_resp = client.get("/tax-audit/runtime/sessions/session-1")
    assert session_resp.status_code == 200
    assert session_resp.json()["id"] == "session-1"

    skill_runs_resp = client.get(
        "/tax-audit/runtime/sessions/session-1/skills")
    assert skill_runs_resp.status_code == 200
    assert skill_runs_resp.json(
    )["items"][0]["skill_id"] == "entity_extract_skill"

    replay_resp = client.post("/tax-audit/runtime/sessions/session-1/replay")
    assert replay_resp.status_code == 200
    assert replay_resp.json()["runtime"]["session_id"] == "session-2"
