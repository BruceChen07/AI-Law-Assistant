"""Audit capability management (skills / rule packs / templates / agents).

Split from a single 2696-line module. `__init__` re-exports the whole old
surface so existing imports keep working unchanged.
"""
from __future__ import annotations

from app.services.audit_capabilities.skills import create_skill  # noqa: F401
from app.services.audit_capabilities.skills import delete_skill  # noqa: F401
from app.services.audit_capabilities.skills import get_admin_skill_detail  # noqa: F401
from app.services.audit_capabilities.skills import list_admin_skills  # noqa: F401
from app.services.audit_capabilities.skills import set_skill_status  # noqa: F401
from app.services.audit_capabilities.skills import update_skill  # noqa: F401
from app.services.audit_capabilities.skills import ensure_audit_capabilities_seeded  # noqa: F401
from app.services.audit_capabilities.skills import list_visible_skills  # noqa: F401
from app.services.audit_capabilities.skills import get_skill_detail  # noqa: F401

from app.services.audit_capabilities import _normalizers  # noqa: F401
from app.services.audit_capabilities import _row_mappers  # noqa: F401
from app.services.audit_capabilities import _seed_data  # noqa: F401
from app.services.audit_capabilities import skills  # noqa: F401
from app.services.audit_capabilities import rule_packs  # noqa: F401
from app.services.audit_capabilities import templates  # noqa: F401
from app.services.audit_capabilities import agents  # noqa: F401

# full back-compat surface: everything the old module exposed
from app.services.audit_capabilities._normalizers import (  # noqa: F401
    _utc_now_iso,
    _json_text,
    _safe_json_loads,
    _normalize_string_list,
    _slugify_skill_id,
    _normalize_json_schema,
    _normalize_optional_int,
    _normalize_changelog_items,
    _validate_agent_limits,
    _validate_agent_runtime_combination,
    _normalize_rule_selector,
    _SKILL_STATUS_VALUES,
    _SKILL_VISIBILITY_VALUES,
    _SKILL_ID_PATTERN,
)
from app.services.audit_capabilities._row_mappers import (  # noqa: F401
    _row_to_skill,
    _row_to_skill_file,
    _row_to_skill_version,
    _row_to_rule_pack,
    _row_to_rule_pack_draft,
    _row_to_rule_pack_version,
    _row_to_template,
    _row_to_agent_profile,
)
from app.services.audit_capabilities._seed_data import (  # noqa: F401
    _skill_file,
    _skill_version,
    _skill_card,
    _build_default_skill_md,
    _build_default_skill_detail,
    _get_skill_seed_detail,
    TAX_DIGITAL_LOCALIZATION_SKILL_MD,
    TAX_DIGITAL_LOCALIZATION_SKILL_CARD,
    TAX_DIGITAL_LOCALIZATION_FILES,
    TAX_DIGITAL_LOCALIZATION_VERSIONS,
    BUILTIN_SKILL_DETAIL_SEEDS,
    BUILTIN_SKILL_SEEDS,
    BUILTIN_RULE_PACK_SEEDS,
    BUILTIN_TEMPLATE_SEEDS,
)
from app.services.audit_capabilities.skills import (  # noqa: F401
    _save_skill_detail,
    _attach_skill_detail_meta,
    _normalize_skill_id,
    _normalize_skill_files,
    _normalize_skill_versions,
    _upsert_detail_file,
    _normalize_skill_detail_payload,
    _normalize_skill_payload,
    _get_skill_reference_counts,
    _attach_skill_reference_meta,
)
from app.services.audit_capabilities.rule_packs import (  # noqa: F401
    list_rule_packs,
    get_rule_pack_detail,
    list_rule_pack_drafts,
    get_rule_pack_draft,
    create_rule_pack_draft,
    update_rule_pack_draft,
    submit_rule_pack_draft,
    review_rule_pack_draft,
    list_rule_pack_versions,
    list_rules,
)
from app.services.audit_capabilities.templates import (  # noqa: F401
    list_templates,
    get_template_detail,
)
from app.services.audit_capabilities.agents import (  # noqa: F401
    _validate_agent_bindings,
    list_agent_profiles,
    get_agent_profile,
    create_agent_profile,
    update_agent_profile,
)
