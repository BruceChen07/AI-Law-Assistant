from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_user
from app.api.routers import agents as agents_router
from app.api.routers.agents import build_router
from app.core.database import init_db
from app.services.audit_capabilities import (
    create_agent_profile,
    ensure_audit_capabilities_seeded,
    get_agent_profile,
    list_agent_profiles,
    update_agent_profile,
)


def _build_cfg(tmp_path: Path) -> dict:
    return {
        "db_path": str(tmp_path / "app.db"),
        "data_dir": str(tmp_path / "data"),
        "files_dir": str(tmp_path / "files"),
        "static_dir": str(tmp_path / "static"),
    }


def test_create_agent_profile_from_template_defaults(tmp_path: Path):
    cfg = _build_cfg(tmp_path)
    init_db(cfg)
    ensure_audit_capabilities_seeded(cfg)

    created = create_agent_profile(
        cfg,
        owner_id="user-1",
        display_name="我的税务审计助手",
        template_id="tax_contract_audit_default",
    )
    assert created["display_name"] == "我的税务审计助手"
    assert created["scene"] == "tax_contract_audit"
    assert "final_review_skill" in created["enabled_skill_ids"]
    assert created["enabled_rule_pack_ids"] == ["tax_core_default"]

    listed = list_agent_profiles(cfg, owner_id="user-1")
    assert len(listed) == 1
    assert listed[0]["id"] == created["id"]


def test_create_agent_profile_rejects_invalid_skill(tmp_path: Path):
    cfg = _build_cfg(tmp_path)
    init_db(cfg)
    ensure_audit_capabilities_seeded(cfg)

    try:
        create_agent_profile(
            cfg,
            owner_id="user-1",
            display_name="bad-agent",
            template_id="tax_contract_audit_default",
            enabled_skill_ids=["not-exists"],
        )
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "invalid skill ids" in str(exc)


def test_update_agent_profile_changes_bindings_and_limits(tmp_path: Path):
    cfg = _build_cfg(tmp_path)
    init_db(cfg)
    ensure_audit_capabilities_seeded(cfg)

    created = create_agent_profile(
        cfg,
        owner_id="user-1",
        display_name="agent-update",
        template_id="tax_contract_audit_default",
    )

    updated = update_agent_profile(
        cfg,
        profile_id=created["id"],
        owner_id="user-1",
        display_name="agent-updated",
        description="updated desc",
        enabled_skill_ids=["entity_extract_skill", "final_review_skill"],
        enabled_rule_pack_ids=["tax_core_default"],
        max_llm_steps=3,
        max_skills_per_run=2,
    )
    assert updated["display_name"] == "agent-updated"
    assert updated["description"] == "updated desc"
    assert updated["enabled_skill_ids"] == [
        "entity_extract_skill",
        "final_review_skill",
    ]
    assert updated["enabled_rule_pack_ids"] == ["tax_core_default"]
    assert updated["max_llm_steps"] == 3
    assert updated["max_skills_per_run"] == 2


def test_update_agent_profile_rejects_invalid_runtime_combination(tmp_path: Path):
    cfg = _build_cfg(tmp_path)
    init_db(cfg)
    ensure_audit_capabilities_seeded(cfg)

    created = create_agent_profile(
        cfg,
        owner_id="user-1",
        display_name="agent-invalid-update",
        template_id="tax_contract_audit_default",
    )

    try:
        update_agent_profile(
            cfg,
            profile_id=created["id"],
            owner_id="user-1",
            enabled_skill_ids=["entity_extract_skill"],
            max_skills_per_run=2,
        )
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "cannot exceed enabled skill count" in str(exc)


def test_agents_api_create_and_get(tmp_path: Path, monkeypatch):
    cfg = _build_cfg(tmp_path)
    init_db(cfg)
    ensure_audit_capabilities_seeded(cfg)

    monkeypatch.setattr(agents_router, "get_config", lambda: cfg)

    app = FastAPI()
    app.include_router(build_router())
    app.dependency_overrides[get_current_user] = lambda: {
        "id": "user-1", "role": "user"}
    client = TestClient(app)

    create_resp = client.post(
        "/api/agents",
        json={
            "display_name": "agent-a",
            "template_id": "tax_contract_audit_default",
            "description": "for testing",
        },
    )
    assert create_resp.status_code == 200
    profile = create_resp.json()
    assert profile["display_name"] == "agent-a"
    assert profile["template_id"] == "tax_contract_audit_default"

    get_resp = client.get(f"/api/agents/{profile['id']}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == profile["id"]

    list_resp = client.get("/api/agents?scene=tax_contract_audit")
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] == 1

    loaded = get_agent_profile(cfg, profile["id"], owner_id="user-1")
    assert loaded is not None


def test_agents_api_update_and_validate(tmp_path: Path, monkeypatch):
    cfg = _build_cfg(tmp_path)
    init_db(cfg)
    ensure_audit_capabilities_seeded(cfg)

    monkeypatch.setattr(agents_router, "get_config", lambda: cfg)

    app = FastAPI()
    app.include_router(build_router())
    app.dependency_overrides[get_current_user] = lambda: {
        "id": "user-1", "role": "user"}
    client = TestClient(app)

    create_resp = client.post(
        "/api/agents",
        json={
            "display_name": "agent-b",
            "template_id": "tax_contract_audit_default",
        },
    )
    assert create_resp.status_code == 200
    profile = create_resp.json()

    update_resp = client.put(
        f"/api/agents/{profile['id']}",
        json={
            "description": "api-updated",
            "enabled_skill_ids": ["entity_extract_skill", "final_review_skill"],
            "max_skills_per_run": 2,
        },
    )
    assert update_resp.status_code == 200
    updated = update_resp.json()
    assert updated["description"] == "api-updated"
    assert updated["enabled_skill_ids"] == [
        "entity_extract_skill",
        "final_review_skill",
    ]
    assert updated["max_skills_per_run"] == 2

    bad_update_resp = client.put(
        f"/api/agents/{profile['id']}",
        json={"enabled_skill_ids": ["not-exists"]},
    )
    assert bad_update_resp.status_code == 400
    assert "invalid skill ids" in bad_update_resp.json()["detail"]
