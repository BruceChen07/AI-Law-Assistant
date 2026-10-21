"""sqlite3.Row -> dict mappers for every capability entity."""
from __future__ import annotations

from typing import Any, Dict
from app.services.audit_capabilities._normalizers import (
    _safe_json_loads,
)


def _row_to_skill(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    item["tags"] = _safe_json_loads(item.pop("tags_json", "[]"), [])
    item["input_schema"] = _safe_json_loads(
        item.pop("input_schema_json", "{}"), {})
    item["output_schema"] = _safe_json_loads(
        item.pop("output_schema_json", "{}"), {})
    item["config_schema"] = _safe_json_loads(
        item.pop("config_schema_json", "{}"), {})
    item["skill_card"] = _safe_json_loads(
        item.pop("skill_card_json", "{}"), {})
    for key in (
        "description",
        "source_url",
        "reference_summary",
        "publisher_name",
        "publisher_handle",
        "install_command",
        "skill_md_text",
        "current_version",
        "license_name",
        "security_audit_status",
    ):
        item[key] = str(item.get(key) or "")
    item["downloads_30d"] = int(item.get("downloads_30d") or 0)
    item["downloads_all_time"] = int(item.get("downloads_all_time") or 0)
    return item


def _row_to_skill_file(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    item["size_bytes"] = int(item.get("size_bytes") or 0)
    item["sort_order"] = int(item.get("sort_order") or 0)
    return item


def _row_to_skill_version(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    item["is_latest"] = bool(item.get("is_latest"))
    item["sort_order"] = int(item.get("sort_order") or 0)
    item["changelog_items"] = _safe_json_loads(
        item.pop("changelog_json", "[]"), [])
    return item


def _row_to_rule_pack(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    item["selector"] = _safe_json_loads(item.pop("selector_json", "{}"), {})
    item["rule_count"] = int(item.get("rule_count") or 0)
    return item


def _row_to_rule_pack_draft(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    item["selector"] = _safe_json_loads(item.pop("selector_json", "{}"), {})
    if item.get("published_version_no") is not None:
        item["published_version_no"] = int(
            item.get("published_version_no") or 0)
    return item


def _row_to_rule_pack_version(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    item["selector"] = _safe_json_loads(item.pop("selector_json", "{}"), {})
    item["version_no"] = int(item.get("version_no") or 0)
    return item


def _row_to_template(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    item["skill_ids"] = _safe_json_loads(item.pop("skill_ids_json", "[]"), [])
    item["rule_pack_ids"] = _safe_json_loads(
        item.pop("rule_pack_ids_json", "[]"), [])
    return item


def _row_to_agent_profile(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    item["enabled_skill_ids"] = _safe_json_loads(
        item.pop("enabled_skill_ids_json", "[]"), [])
    item["enabled_rule_pack_ids"] = _safe_json_loads(
        item.pop("enabled_rule_pack_ids_json", "[]"), [])
    return item
