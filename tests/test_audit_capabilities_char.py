"""Characterization tests for app/services/audit_capabilities.py.

This module had **zero** test coverage before the P4 split. These tests pin the
observable behaviour of the 6 functions that `app/api/routers/admin.py` actually
consumes, plus the seeding hook that every public function calls lazily, so the
split into a package can be proven behaviour-neutral.

They assert *structure and invariants*, not timestamps or uuids, so they stay
stable across runs.
"""
from __future__ import annotations

import pytest

from app.core.database import init_db
from app.services import audit_capabilities as ac

NEW_SKILL = {
    "id": "char-demo-skill",
    "display_name": "Char Demo Skill",
    "category": "tax",
    "scene": "contract",
    "description": "characterization fixture",
    "visibility": "public",
    "status": "active",
}


@pytest.fixture()
def cfg(tmp_path):
    c = {"db_path": str(tmp_path / "ac.db")}
    init_db(c)
    return c


# --------------------------------------------------------------- seeding hook
def test_seed_is_idempotent(cfg):
    ac.ensure_audit_capabilities_seeded(cfg)
    first = ac.list_admin_skills(cfg, page=1, page_size=200)
    ac.ensure_audit_capabilities_seeded(cfg)
    second = ac.list_admin_skills(cfg, page=1, page_size=200)

    assert first["total"] == second["total"]
    assert first["total"] > 0, "builtin seeds should create rows"
    # every public entry point re-runs the seed lazily; totals must not drift
    assert [s["id"] for s in first["items"]] == [s["id"] for s in second["items"]]


def test_seed_keys_are_stable(cfg):
    seeded = ac.list_admin_skills(cfg, page=1, page_size=200)
    ids = [s["id"] for s in seeded["items"]]
    assert len(ids) == len(set(ids)), "seed must not duplicate ids"
    for item in seeded["items"]:
        assert isinstance(item["id"], str) and item["id"]
        assert isinstance(item["display_name"], str) and item["display_name"]
        assert item["status"] in {"active", "disabled", "deleted"}


# --------------------------------------------------------------------- CRUD
def test_create_read_update_delete_roundtrip(cfg):
    base_total = ac.list_admin_skills(cfg, page_size=200)["total"]

    created = ac.create_skill(cfg, "owner-1", dict(NEW_SKILL))
    assert created["id"] == "char-demo-skill"
    assert created["display_name"] == "Char Demo Skill"
    assert created["owner_type"] == "admin"
    assert created["owner_id"] == "owner-1"

    assert ac.list_admin_skills(cfg, page_size=200)["total"] == base_total + 1

    detail = ac.get_admin_skill_detail(cfg, "char-demo-skill")
    assert detail is not None
    assert detail["category"] == "tax"
    assert detail["scene"] == "contract"

    updated = ac.update_skill(cfg, "char-demo-skill", {"display_name": "Renamed Skill"})
    assert updated["display_name"] == "Renamed Skill"
    assert ac.get_admin_skill_detail(cfg, "char-demo-skill")["display_name"] == "Renamed Skill"

    disabled = ac.set_skill_status(cfg, "char-demo-skill", "disabled")
    assert disabled["status"] == "disabled"

    deleted = ac.delete_skill(cfg, "char-demo-skill")
    assert deleted["status"] == "deleted"


def test_get_detail_returns_none_for_unknown(cfg):
    assert ac.get_admin_skill_detail(cfg, "no-such-skill") is None


def test_create_duplicate_raises(cfg):
    ac.create_skill(cfg, "owner-1", dict(NEW_SKILL))
    with pytest.raises(ValueError):
        ac.create_skill(cfg, "owner-1", dict(NEW_SKILL))


def test_update_unknown_raises(cfg):
    with pytest.raises(ValueError):
        ac.update_skill(cfg, "no-such-skill", {"display_name": "x"})


def test_delete_unknown_raises(cfg):
    with pytest.raises(ValueError):
        ac.delete_skill(cfg, "no-such-skill")


def test_set_status_rejects_invalid_value(cfg):
    ac.create_skill(cfg, "owner-1", dict(NEW_SKILL))
    with pytest.raises(ValueError):
        ac.set_skill_status(cfg, "char-demo-skill", "nonsense")


# -------------------------------------------------------------- list filters
def test_list_pagination_and_filters(cfg):
    ac.create_skill(cfg, "owner-1", dict(NEW_SKILL))

    page1 = ac.list_admin_skills(cfg, page=1, page_size=5)
    assert page1["page"] == 1
    assert page1["page_size"] == 5
    assert len(page1["items"]) <= 5

    by_scene = ac.list_admin_skills(cfg, page_size=200, scene="contract")
    assert all(i["scene"] == "contract" for i in by_scene["items"])
    assert any(i["id"] == "char-demo-skill" for i in by_scene["items"])

    by_category = ac.list_admin_skills(cfg, page_size=200, category="tax")
    assert all(i["category"] == "tax" for i in by_category["items"])

    searched = ac.list_admin_skills(cfg, page_size=200, search="Char Demo")
    assert any(i["id"] == "char-demo-skill" for i in searched["items"])


def test_list_ordering_is_deterministic(cfg):
    first = [i["id"] for i in ac.list_admin_skills(cfg, page_size=200)["items"]]
    second = [i["id"] for i in ac.list_admin_skills(cfg, page_size=200)["items"]]
    assert first == second


# ------------------------------------------- internal-only domains still work
def test_rule_packs_and_templates_and_agents_are_callable(cfg):
    """These are internal-only (no router references them) but must keep working."""
    packs = ac.list_rule_packs(cfg)
    assert isinstance(packs, list)

    templates = ac.list_templates(cfg)
    assert isinstance(templates, list)

    profiles = ac.list_agent_profiles(cfg, owner_id="owner-1")
    assert isinstance(profiles, list)
