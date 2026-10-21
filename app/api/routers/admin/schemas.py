"""Pydantic request/response models for the admin API."""
from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Any, Dict, List, Literal, Optional

class DocumentResponse(BaseModel):
    id: str
    filename: str
    original_filename: str
    file_size: int
    mime_type: Optional[str]
    user_id: str
    username: Optional[str]
    title: Optional[str]
    category: Optional[str]
    status: str
    created_at: str


class DocumentListResponse(BaseModel):
    items: List[DocumentResponse]
    total: int
    page: int
    page_size: int


class DeleteResponse(BaseModel):
    message: str
    deleted_id: str


class PromptSummaryResponse(BaseModel):
    id: str
    prompt_key: str
    name: str
    description: str
    status: Literal["enabled", "disabled"]
    created_by: Optional[str] = None
    created_at: str
    updated_at: str
    binding_scene_keys: List[str] = []
    source: Literal["system", "custom"] = "custom"
    readonly: bool = False
    source_module: Optional[str] = None
    managed_scope: Literal["system", "custom"] = "custom"


class PromptDetailResponse(PromptSummaryResponse):
    editor_lang: Literal["zh", "en"] = "zh"
    display_system_text: str = ""
    display_focus_text: str = ""
    display_template_text: str = ""
    system_text: str
    template_text: str
    sample_input_json: str


class PromptListResponse(BaseModel):
    items: List[PromptSummaryResponse]


class SkillResponse(BaseModel):
    id: str
    display_name: str
    category: str
    scene: str
    description: str = ""
    owner_type: str
    owner_id: Optional[str] = None
    visibility: str
    status: str
    source_url: str = ""
    reference_summary: str = ""
    input_schema: Dict[str, Any] = {}
    output_schema: Dict[str, Any] = {}
    config_schema: Dict[str, Any] = {}
    tags: List[str] = []
    publisher_name: str = ""
    publisher_handle: str = ""
    install_command: str = ""
    skill_md_text: str = ""
    skill_card: Dict[str, Any] = {}
    current_version: str = ""
    license_name: str = ""
    security_audit_status: str = ""
    downloads_30d: int = 0
    downloads_all_time: int = 0
    last_published_at: Optional[str] = None
    files: List[Dict[str, Any]] = []
    versions: List[Dict[str, Any]] = []
    sort_order: int
    template_ref_count: int = 0
    agent_ref_count: int = 0
    in_use: bool = False
    created_at: str
    updated_at: Optional[str] = None


class SkillListResponse(BaseModel):
    items: List[SkillResponse]
    total: int
    page: int
    page_size: int


class PromptUpsertRequest(BaseModel):
    name: str
    description: Optional[str] = ""
    editor_lang: Optional[str] = "zh"
    display_system_text: Optional[str] = ""
    display_focus_text: Optional[str] = ""
    display_template_text: Optional[str] = ""
    system_text: Optional[str] = ""
    template_text: Optional[str] = ""
    sample_input_json: Optional[str] = "{}"
    prompt_key: Optional[str] = ""


class PromptStatusUpdateRequest(BaseModel):
    enabled: bool


class PromptPreviewRequest(BaseModel):
    prompt_id: Optional[str] = ""
    prompt_key: Optional[str] = ""
    editor_lang: Optional[str] = "zh"
    display_system_text: Optional[str] = ""
    display_focus_text: Optional[str] = ""
    display_template_text: Optional[str] = ""
    system_text: Optional[str] = ""
    template_text: Optional[str] = ""
    sample_input_json: Optional[str] = "{}"


class PromptPreviewResponse(BaseModel):
    rendered_system_text: str
    rendered_text: str
    normalized_context: dict


class PromptBindingPromptSummary(BaseModel):
    id: str
    name: str
    description: str
    status: Literal["enabled", "disabled"]
    updated_at: str


class PromptBindingSceneResponse(BaseModel):
    scene_key: str
    label: str
    description: str
    source_module: str
    active_source: Literal["bound_prompt", "system_default"]
    updated_by: Optional[str] = None
    updated_at: Optional[str] = None
    active_prompt: Optional[PromptBindingPromptSummary] = None


class PromptBindingListResponse(BaseModel):
    items: List[PromptBindingSceneResponse]


class PromptBindingUpdateRequest(BaseModel):
    prompt_id: Optional[str] = None


class SkillCreateRequest(BaseModel):
    id: Optional[str] = Field(default=None, max_length=64)
    display_name: str = Field(..., min_length=1, max_length=120)
    category: str = Field(..., min_length=1, max_length=60)
    scene: str = Field(..., min_length=1, max_length=80)
    description: str = ""
    visibility: str = "public"
    status: str = "active"
    source_url: str = ""
    reference_summary: str = ""
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    output_schema: Dict[str, Any] = Field(default_factory=dict)
    config_schema: Dict[str, Any] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)
    sort_order: int = 100
    publisher_name: str = ""
    publisher_handle: str = ""
    install_command: str = ""
    skill_md_text: str = ""
    skill_card: Dict[str, Any] = Field(default_factory=dict)
    current_version: str = ""
    license_name: str = ""
    security_audit_status: str = ""
    downloads_30d: int = 0
    downloads_all_time: int = 0
    last_published_at: str = ""
    files: List[Dict[str, Any]] = Field(default_factory=list)
    versions: List[Dict[str, Any]] = Field(default_factory=list)


class SkillUpdateRequest(BaseModel):
    display_name: Optional[str] = Field(
        default=None, min_length=1, max_length=120)
    category: Optional[str] = Field(default=None, min_length=1, max_length=60)
    scene: Optional[str] = Field(default=None, min_length=1, max_length=80)
    description: Optional[str] = None
    visibility: Optional[str] = None
    status: Optional[str] = None
    source_url: Optional[str] = None
    reference_summary: Optional[str] = None
    input_schema: Optional[Dict[str, Any]] = None
    output_schema: Optional[Dict[str, Any]] = None
    config_schema: Optional[Dict[str, Any]] = None
    tags: Optional[List[str]] = None
    sort_order: Optional[int] = None
    publisher_name: Optional[str] = None
    publisher_handle: Optional[str] = None
    install_command: Optional[str] = None
    skill_md_text: Optional[str] = None
    skill_card: Optional[Dict[str, Any]] = None
    current_version: Optional[str] = None
    license_name: Optional[str] = None
    security_audit_status: Optional[str] = None
    downloads_30d: Optional[int] = None
    downloads_all_time: Optional[int] = None
    last_published_at: Optional[str] = None
    files: Optional[List[Dict[str, Any]]] = None
    versions: Optional[List[Dict[str, Any]]] = None


class SkillStatusUpdateRequest(BaseModel):
    status: Literal["active", "disabled"]


class StatsResponse(BaseModel):
    total_documents: int
    total_users: int
    total_size: int
    documents_by_category: dict
    documents_by_user: dict


class LLMConfigResponse(BaseModel):
    provider: str
    provider_label: str
    api_base: str
    model: str
    temperature: float
    max_tokens: int
    timeout: int
    headers: dict
    env_managed: bool
    supported_env_vars: List[str]
    required_env_vars: List[str]
    missing_env_vars: List[str]
    env_sources: dict
    security_issues: List[str]
    route: dict


class LLMConfigUpdate(BaseModel):
    provider: str
    api_base: str
    model: str
    temperature: float
    max_tokens: int
    timeout: int
    headers: dict


class UIConfigResponse(BaseModel):
    show_citation_source: bool
    default_theme: Literal["dark", "light"]


class UIConfigUpdate(BaseModel):
    show_citation_source: bool
    default_theme: Optional[str] = None


class MemoryRuntimeConfigResponse(BaseModel):
    memory_module_enabled: bool
    memory_mode_when_disabled: str
    memory_disable_fallback_on_error: bool
    memory_token_guard_enabled: bool
    memory_max_llm_calls_per_audit: int
    memory_max_prompt_chars_per_clause: int
    risk_notice: str


class MemoryRuntimeConfigUpdate(BaseModel):
    memory_module_enabled: Optional[bool] = None
    memory_mode_when_disabled: Optional[str] = None
    memory_disable_fallback_on_error: Optional[bool] = None
    memory_token_guard_enabled: Optional[bool] = None
    memory_max_llm_calls_per_audit: Optional[int] = None
    memory_max_prompt_chars_per_clause: Optional[int] = None


class LLMTestRequest(BaseModel):
    prompt: Optional[str] = None


class LLMTestResponse(BaseModel):
    ok: bool
    prompt: str
    answer: str


class TokenUsageTotals(BaseModel):
    input_tokens: int
    output_tokens: int
    total_tokens: int
    request_count: int


class TokenUsageSeriesItem(BaseModel):
    bucket: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    request_count: int


class TokenUsageRankingItem(BaseModel):
    key: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    request_count: int


class TokenUsageAlert(BaseModel):
    level: str
    reason: str
    value: int
    threshold: int
    bucket: Optional[str] = None
    meta: Optional[dict] = None


class TokenUsageResponse(BaseModel):
    range_start: str
    range_end: str
    granularity: str
    rank_by: str
    totals: TokenUsageTotals
    series: List[TokenUsageSeriesItem]
    rankings: List[TokenUsageRankingItem]
    alerts: List[TokenUsageAlert]
    last_updated: str


class MemoryPromotionRunRequest(BaseModel):
    episode_id: Optional[str] = None
    min_support_count: Optional[int] = None
    min_quality_score: Optional[float] = None
    high_impact_threshold: Optional[float] = None


class MemoryRuleReviewRequest(BaseModel):
    action: Literal["approve", "reject"]
    note: Optional[str] = None


class VectorStoreConfigUpdate(BaseModel):
    engine: str
