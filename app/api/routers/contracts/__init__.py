import os
import uuid
import json
import logging
import threading
import hashlib
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends, Query, Body, Request
from fastapi.responses import FileResponse
from app.api.dependencies import (
    get_current_user,
    get_app_llm,
    get_app_embedder,
    get_app_reranker,
    get_app_translator,
)
from app.services.crud import (
    insert_document,
    insert_contract_audit,
    get_document_by_id_for_user,
    get_latest_contract_audit_by_document,
    get_latest_successful_document_digitization_by_document,
)
from app.services.audit_orchestrator import AuditServices, run_contract_pipeline_bundle
from app.services.contract_preview_assets import build_contract_preview_manifest, find_preview_page
from app.services.contract_audit import _build_contract_summary, _build_preview_clauses
from app.services.docx_renderer import render_tax_audit_docx
from app.services.pdf_pipeline import build_pdf_pipeline_summary, get_pdf_pipeline_config, resolve_pdf_audit_source
from app.services.contract_audit_stage_log import (
    clear_contract_audit_log_context,
    register_contract_audit_log_context,
    write_contract_audit_stage_log,
)
from app.services.audit_utils import _normalize_lang
from app.core.utils import extract_text_with_config
from app.core.config import get_config

logger = logging.getLogger("law_assistant")

from app.api.routers.contracts._error_specs import (  # noqa: F401  (re-export)
    _normalize_theme,
    _AUDIT_ERROR_SPECS,
    _audit_locale,
    _match_audit_error_spec,
    _build_audit_error_payload,
)
from app.api.routers.contracts._progress import (  # noqa: F401  (re-export)
    _audit_progress,
    _audit_progress_lock,
    _AUDIT_STAGE_TITLES,
    _set_audit_progress,
    _get_audit_progress,
    _make_audit_progress_writer,
)
from app.api.routers.contracts._support import (  # noqa: F401  (re-export)
    _build_digitization_meta,
    _resolve_request_ip,
    _build_retrieval_options,
)


from ._cfg import set_active_config
from . import audit, export, preview, preview2, source, summary  # noqa: F401

from .audit import (  # noqa: F401
    audit_contract_file,
    rerun_contract_pipeline,
    get_contract_audit_progress,
)
from .summary import (  # noqa: F401
    get_contract_ui_config,
    get_contract_pdf_pipeline_summary,
    get_contract_summary,
)
from .preview import (  # noqa: F401
    get_contract_preview_manifest,
    get_contract_preview_page_image,
)
from .source import (  # noqa: F401
    download_contract_source,
)
from .preview2 import (  # noqa: F401
    preview_contract_document,
)
from .export import (  # noqa: F401
    export_contract_report,
)

def build_router(cfg):
    """Assemble the contract routes.

    Handlers live in one submodule per resource so each is independently
    testable. The factory signature is unchanged for app_factory.
    """
    set_active_config(cfg)
    router = APIRouter()
    router.include_router(audit.router)
    router.include_router(summary.router)
    router.include_router(preview.router)
    router.include_router(source.router)
    router.include_router(preview2.router)
    router.include_router(export.router)
    return router
