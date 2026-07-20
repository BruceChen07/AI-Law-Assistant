from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_user
from app.api.routers import capabilities as capabilities_router
from app.api.routers.capabilities import build_router
from app.core.database import get_conn, init_db
from app.services.audit_capabilities import (
    create_rule_pack_draft,
    ensure_audit_capabilities_seeded,
    get_rule_pack_detail,
    list_rule_packs,
    list_rule_pack_drafts,
    list_rule_pack_versions,
    list_rules,
    list_templates,
    list_visible_skills,
    review_rule_pack_draft,
    submit_rule_pack_draft,
    update_rule_pack_draft,
)


def _build_cfg(tmp_path: Path) -> dict:
    data_dir = tmp_path / "data"
    files_dir = tmp_path / "files"
    static_dir = tmp_path / "static"
    return {
        "db_path": str(tmp_path / "app.db"),
        "data_dir": str(data_dir),
        "files_dir": str(files_dir),
        "static_dir": str(static_dir),
    }


def test_audit_capabilities_seed_and_query(tmp_path: Path):
    cfg = _build_cfg(tmp_path)
    init_db(cfg)
    ensure_audit_capabilities_seeded(cfg)

    skills = list_visible_skills(cfg, user_id="u1", scene="tax_contract_audit")
    assert any(item["id"] == "china_tax_law_knowledge" for item in skills)
    assert any(item["id"] == "final_review_skill" for item in skills)

    packs = list_rule_packs(cfg, user_id="u1", scene="tax_contract_audit")
    assert any(item["id"] == "tax_core_default" for item in packs)

    templates = list_templates(cfg, user_id="u1", scene="tax_contract_audit")
    assert templates[0]["id"] == "tax_contract_audit_default"
    assert "final_review_skill" in templates[0]["skill_ids"]


def test_audit_capabilities_rules_endpoint_data(tmp_path: Path):
    cfg = _build_cfg(tmp_path)
    init_db(cfg)
    ensure_audit_capabilities_seeded(cfg)

    conn = get_conn(cfg)
    cur = conn.cursor()
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
            "reg-doc-1",
            "增值税法",
            "第1条",
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
            "tester",
            "2026-07-18T00:00:00+00:00",
            "2026-07-18T00:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()

    rules = list_rules(cfg, pack_id="tax_core_default")
    assert rules["total"] == 1
    assert rules["items"][0]["id"] == "rule-1"
    assert rules["items"][0]["rule_type"] == "invoice"


def test_capabilities_api_readonly(tmp_path: Path, monkeypatch):
    cfg = _build_cfg(tmp_path)
    init_db(cfg)
    ensure_audit_capabilities_seeded(cfg)

    conn = get_conn(cfg)
    cur = conn.cursor()
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
            "rule-2",
            "reg-doc-2",
            "企业所得税法",
            "第8条",
            "tax_rate",
            "",
            "应明确税率",
            "",
            "",
            "",
            "CN",
            "general",
            "2026-01-01",
            "",
            1,
            "1",
            "企业应按适用税率计算企业所得税。",
            "tester",
            "2026-07-18T00:00:00+00:00",
            "2026-07-18T00:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(capabilities_router, "get_config", lambda: cfg)

    app = FastAPI()
    app.include_router(build_router())
    app.dependency_overrides[get_current_user] = lambda: {
        "id": "u1", "role": "user"}
    client = TestClient(app)

    skills_resp = client.get("/api/capabilities/skills")
    assert skills_resp.status_code == 200
    assert skills_resp.json()["total"] >= 3

    packs_resp = client.get(
        "/api/capabilities/rule-packs?scene=tax_contract_audit")
    assert packs_resp.status_code == 200
    assert packs_resp.json()["items"][0]["id"] == "tax_core_default"

    rules_resp = client.get("/api/capabilities/rules?pack_id=tax_core_default")
    assert rules_resp.status_code == 200
    assert rules_resp.json()["total"] == 1

    templates_resp = client.get(
        "/api/capabilities/templates?scene=tax_contract_audit")
    assert templates_resp.status_code == 200
    assert templates_resp.json(
    )["items"][0]["id"] == "tax_contract_audit_default"


def test_rule_pack_draft_service_flow(tmp_path: Path):
    cfg = _build_cfg(tmp_path)
    init_db(cfg)
    ensure_audit_capabilities_seeded(cfg)

    created = create_rule_pack_draft(
        cfg,
        owner_id="u1",
        base_pack_id="tax_core_default",
        display_name="税审规则包草案",
        selector={"rule_types": ["invoice"]},
        change_summary="缩小为发票类规则",
    )
    assert created["status"] == "draft"
    assert created["selector"]["rule_types"] == ["invoice"]

    updated = update_rule_pack_draft(
        cfg,
        draft_id=created["id"],
        owner_id="u1",
        description="仅保留发票类规则",
    )
    assert updated["description"] == "仅保留发票类规则"

    submitted = submit_rule_pack_draft(cfg, created["id"], owner_id="u1")
    assert submitted["status"] == "pending_review"

    approved = review_rule_pack_draft(
        cfg,
        draft_id=created["id"],
        reviewer_id="admin-1",
        action="approve",
        review_comment="looks good",
    )
    assert approved["status"] == "approved"
    assert approved["published_version_no"] == 1

    pack = get_rule_pack_detail(cfg, "tax_core_default", user_id="u1")
    assert pack is not None
    assert pack["display_name"] == "税审规则包草案"
    assert pack["selector"]["rule_types"] == ["invoice"]

    versions = list_rule_pack_versions(cfg, "tax_core_default", user_id="u1")
    assert len(versions) == 1
    assert versions[0]["version_no"] == 1
    assert versions[0]["change_summary"] == "缩小为发票类规则"

    drafts = list_rule_pack_drafts(cfg, owner_id="u1")
    assert len(drafts) == 1
    assert drafts[0]["id"] == created["id"]


def test_capabilities_api_rule_pack_draft_flow(tmp_path: Path, monkeypatch):
    cfg = _build_cfg(tmp_path)
    init_db(cfg)
    ensure_audit_capabilities_seeded(cfg)

    monkeypatch.setattr(capabilities_router, "get_config", lambda: cfg)

    app = FastAPI()
    app.include_router(build_router())
    app.dependency_overrides[get_current_user] = lambda: {
        "id": "u1", "role": "user"}
    client = TestClient(app)

    create_resp = client.post(
        "/api/capabilities/rule-packs/tax_core_default/drafts",
        json={
            "display_name": "发票规则草案",
            "selector": {"rule_types": ["invoice"]},
            "change_summary": "只保留 invoice",
        },
    )
    assert create_resp.status_code == 200
    draft = create_resp.json()
    assert draft["status"] == "draft"

    update_resp = client.put(
        f"/api/capabilities/rule-pack-drafts/{draft['id']}",
        json={"description": "api draft"},
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["description"] == "api draft"

    submit_resp = client.post(
        f"/api/capabilities/rule-pack-drafts/{draft['id']}/submit"
    )
    assert submit_resp.status_code == 200
    assert submit_resp.json()["status"] == "pending_review"

    app.dependency_overrides[get_current_user] = lambda: {
        "id": "admin-1", "role": "admin"}

    review_resp = client.post(
        f"/api/capabilities/rule-pack-drafts/{draft['id']}/review",
        json={"action": "approve", "review_comment": "approved"},
    )
    assert review_resp.status_code == 200
    assert review_resp.json()["status"] == "approved"
    assert review_resp.json()["published_version_no"] == 1

    versions_resp = client.get(
        "/api/capabilities/rule-packs/tax_core_default/versions")
    assert versions_resp.status_code == 200
    assert versions_resp.json()["total"] == 1
    assert versions_resp.json()["items"][0]["version_no"] == 1
