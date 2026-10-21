"""Admin API.

Split from a single 2009-line module: one sub-router per admin domain,
Pydantic models in `schemas.py`. `router` keeps the `/api/admin` prefix and
the original registration order, so `app_factory` is unchanged.
"""
from __future__ import annotations

from fastapi import APIRouter

from . import prompts, documents, users, skills, vector_store, stats, llm_config, ui_config, memory_config, token_usage, memory_rules  # noqa: F401

from app.api.routers.admin.prompts import (  # noqa: F401
    _normalize_prompt_status,
    _validate_prompt_name,
    _validate_prompt_template,
    _validate_prompt_system_text,
    _validate_prompt_key,
    _normalize_prompt_sample_input,
    _normalize_prompt_payload,
    _render_prompt_preview,
    _build_prompt_preview_context,
    _prompt_row_to_summary,
    _prompt_row_to_detail,
    _build_prompt_binding_response,
    _default_scene_keys_by_prompt_id,
    _combined_prompt_binding_map,
    _ensure_prompt_key_unique,
    list_prompts,
    preview_prompt,
    get_prompt,
    create_prompt,
    update_prompt,
    update_prompt_status,
    delete_prompt,
    restore_prompt_default,
    list_prompt_binding_configs,
    update_prompt_binding,
)
from app.api.routers.admin.documents import (  # noqa: F401
    list_documents,
    delete_document,
)
from app.api.routers.admin.users import (  # noqa: F401
    list_users,
    update_role,
    delete_user,
)
from app.api.routers.admin.skills import (  # noqa: F401
    admin_get_skills,
    admin_get_skill_detail,
    admin_create_skill,
    admin_update_skill,
    admin_set_skill_status,
    admin_delete_skill,
)
from app.api.routers.admin.vector_store import (  # noqa: F401
    get_vector_store_config,
    update_vector_store_config,
    cleanup_old_vector_data,
)
from app.api.routers.admin.stats import (  # noqa: F401
    get_stats,
)
from app.api.routers.admin.llm_config import (  # noqa: F401
    _normalize_llm_payload,
    _validate_llm_config,
    get_llm_config,
    update_llm_config,
    clear_llm_api_key,
    test_llm,
)
from app.api.routers.admin.ui_config import (  # noqa: F401
    _get_ui_config,
    get_ui_config,
    update_ui_config,
)
from app.api.routers.admin.memory_config import (  # noqa: F401
    _get_memory_runtime_config,
    get_memory_runtime_config,
    update_memory_runtime_config,
)
from app.api.routers.admin.token_usage import (  # noqa: F401
    get_token_usage,
    export_token_usage_csv,
)
from app.api.routers.admin.memory_rules import (  # noqa: F401
    run_memory_promotion,
    get_pending_memory_rules,
    review_pending_memory_rule,
)
from app.api.routers.admin._common import (  # noqa: F401
    _clean_text,
    _normalize_theme,
    _clean_optional_text,
    _parse_dt,
    _bucket_key,
    _llm_trace_dir,
    _iter_trace_rows,
)
from app.api.routers.admin.schemas import *  # noqa: F401,F403

router = APIRouter(prefix="/api/admin", tags=["admin"])
router.include_router(prompts.router)
router.include_router(documents.router)
router.include_router(users.router)
router.include_router(skills.router)
router.include_router(vector_store.router)
router.include_router(stats.router)
router.include_router(llm_config.router)
router.include_router(ui_config.router)
router.include_router(memory_config.router)
router.include_router(token_usage.router)
router.include_router(memory_rules.router)
