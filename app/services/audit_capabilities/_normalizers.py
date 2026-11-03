"""Pure normalisation / validation helpers and value constants."""
from __future__ import annotations

import json
import re

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _safe_json_loads(value: Any, default: Any):
    if value in {None, ""}:
        return default
    try:
        return json.loads(str(value))
    except Exception:
        return default


def _normalize_string_list(value: Any, field_name: str) -> Optional[List[str]]:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    items: List[str] = []
    seen = set()
    for raw_item in value:
        item = str(raw_item or "").strip()
        if not item:
            raise ValueError(f"{field_name} contains empty item")
        if item in seen:
            continue
        seen.add(item)
        items.append(item)
    return items


def _slugify_skill_id(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "").strip().lower())
    text = re.sub(r"-{2,}", "-", text).strip("-_")
    return text[:64]


def _normalize_json_schema(value: Any, field_name: str) -> Dict[str, Any]:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object")
    return value


def _normalize_optional_int(value: Any, field_name: str, default: int = 0) -> int:
    if value in (None, ""):
        return int(default)
    try:
        return int(value)
    except Exception as exc:
        raise ValueError(f"{field_name} must be an integer") from exc


def _normalize_changelog_items(raw_item: Dict[str, Any]) -> List[str]:
    changelog_items = raw_item.get("changelog_items")
    if changelog_items is None:
        text = str(raw_item.get("changelog_text") or "")
        lines = []
        for line in text.splitlines():
            cleaned = line.strip()
            if not cleaned:
                continue
            if cleaned.startswith("- "):
                cleaned = cleaned[2:].strip()
            lines.append(cleaned)
        return lines
    if not isinstance(changelog_items, list):
        raise ValueError("versions.changelog_items must be a list")
    items: List[str] = []
    for value in changelog_items:
        text = str(value or "").strip()
        if text:
            items.append(text)
    return items


def _validate_agent_limits(max_llm_steps: Optional[int], max_skills_per_run: Optional[int]) -> None:
    if max_llm_steps is not None:
        steps = int(max_llm_steps)
        if steps < 1 or steps > 10:
            raise ValueError("max_llm_steps must be between 1 and 10")
    if max_skills_per_run is not None:
        skills = int(max_skills_per_run)
        if skills < 1 or skills > 20:
            raise ValueError("max_skills_per_run must be between 1 and 20")


def _validate_agent_runtime_combination(
    enabled_skill_ids: List[str],
    max_skills_per_run: Optional[int],
) -> None:
    if not enabled_skill_ids:
        raise ValueError("enabled_skill_ids must not be empty")
    if max_skills_per_run is not None and int(max_skills_per_run) > len(enabled_skill_ids):
        raise ValueError(
            "max_skills_per_run cannot exceed enabled skill count")


def _normalize_rule_selector(selector: Any) -> Dict[str, Any]:
    if selector in (None, ""):
        return {}
    if not isinstance(selector, dict):
        raise ValueError("selector must be an object")
    normalized = dict(selector)
    if "rule_types" in normalized:
        normalized["rule_types"] = _normalize_string_list(
            normalized.get("rule_types") or [], "selector.rule_types"
        ) or []
    return normalized


_SKILL_STATUS_VALUES = {"active", "disabled", "deleted"}


_SKILL_VISIBILITY_VALUES = {"public", "private"}


_SKILL_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")
