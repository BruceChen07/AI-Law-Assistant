from typing import Optional, List
from pydantic import BaseModel


class SearchQuery(BaseModel):
    query: str
    language: str = "zh"
    top_k: int = 10
    date: Optional[str] = None
    region: Optional[str] = None
    industry: Optional[str] = None
    use_semantic: bool = False
    semantic_weight: float = 0.6
    bm25_weight: float = 0.4
    candidate_size: int = 50
    rerank_enabled: Optional[bool] = None
    rerank_top_n: Optional[int] = None
    rerank_mode: Optional[str] = None


class EmbeddingRequest(BaseModel):
    text: str
    is_query: bool = False
    language: str = "zh"


# Auth Schemas
class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


class UserResponse(BaseModel):
    id: str
    username: str
    email: str
    role: str


class TaxAuditRegulationImportResponse(BaseModel):
    document_id: str
    parse_status: str


class TaxAuditContractImportResponse(BaseModel):
    contract_id: str
    parse_status: str


class TaxAuditRegulationParseResponse(BaseModel):
    document_id: str
    parse_status: str
    rule_count: int
    ocr_used: bool
    started_at: str
    finished_at: str


class TaxAuditContractAnalyzeResponse(BaseModel):
    contract_id: str
    parse_status: str
    clause_count: int
    ocr_used: bool
    started_at: str
    finished_at: str


class TaxAuditClauseItem(BaseModel):
    id: str
    contract_document_id: str
    clause_path: Optional[str] = None
    page_no: Optional[int] = None
    paragraph_no: Optional[str] = None
    clause_text: str
    entities_json: Optional[str] = None
    created_at: str
    updated_at: Optional[str] = None


class TaxAuditClauseListResponse(BaseModel):
    contract_id: str
    total: int
    items: List[TaxAuditClauseItem]


class TaxAuditMatchRunResponse(BaseModel):
    contract_id: str
    total_matches: int
    compliant_count: int
    non_compliant_count: int
    not_mentioned_count: int


class TaxAuditMatchItem(BaseModel):
    id: str
    clause_id: str
    rule_id: str
    match_score: float
    match_label: str
    evidence_json: Optional[str] = None
    created_at: str
    updated_at: Optional[str] = None


class TaxAuditMatchListResponse(BaseModel):
    contract_id: str
    total: int
    items: List[TaxAuditMatchItem]


class TaxAuditIssueGenerateResponse(BaseModel):
    contract_id: str
    total: int
    high: int
    medium: int
    low: int


class TaxAuditIssueReviewRequest(BaseModel):
    reviewer_status: str
    reviewer_note: Optional[str] = ""
    risk_level: Optional[str] = None


class TaxAuditIssueReviewResponse(BaseModel):
    issue_id: str
    reviewer_status: str
    risk_level: str
    reviewer_note: Optional[str] = None


class TaxAuditTraceItem(BaseModel):
    id: str
    issue_id: str
    action_type: str
    operator: Optional[str] = None
    payload_json: Optional[str] = None
    created_at: str
    updated_at: Optional[str] = None


class TaxAuditTraceListResponse(BaseModel):
    contract_id: Optional[str] = None
    issue_id: Optional[str] = None
    total: int
    items: List[TaxAuditTraceItem]


class TaxAuditReportOverview(BaseModel):
    contract_filename: Optional[str] = None
    contract_parse_status: Optional[str] = None
    ocr_used: bool
    clause_count: int
    issue_count: int
    trace_count: int


class TaxAuditRiskSummary(BaseModel):
    total: int
    high: int
    medium: int
    low: int


class TaxAuditReviewSummary(BaseModel):
    confirmed: int
    rejected: int
    downgraded: int
    exception: int
    pending: int


class TaxAuditReportItem(BaseModel):
    issue_id: str
    risk_level: str
    issue_text: str
    suggestion: Optional[str] = None
    reviewer_status: str
    reviewer_note: Optional[str] = None
    clause: dict
    rule: dict


class TaxAuditEvidenceItem(BaseModel):
    issue_id: str
    rule_id: Optional[str] = None
    law_title: Optional[str] = None
    article_no: Optional[str] = None
    source_page: Optional[int] = None
    source_paragraph: Optional[str] = None
    source_text: Optional[str] = None
    clause_id: Optional[str] = None
    clause_path: Optional[str] = None
    clause_page_no: Optional[int] = None


class TaxAuditReportResponse(BaseModel):
    contract_id: str
    generated_at: str
    overview: TaxAuditReportOverview
    risk_summary: TaxAuditRiskSummary
    review_summary: TaxAuditReviewSummary
    risk_items: List[TaxAuditReportItem]
    evidence_items: List[TaxAuditEvidenceItem]
    review_conclusions: List[TaxAuditTraceItem]
    exception_items: List[TaxAuditReportItem]


class TaxAuditReportExportRequest(BaseModel):
    export_format: Optional[str] = "json"
    template_version: Optional[str] = "v1.0"
    locale: Optional[str] = "zh-CN"
    include_appendix: Optional[bool] = True
    brand: Optional[str] = ""


class TaxAuditReportExportResponse(BaseModel):
    export_id: Optional[str] = None
    status: Optional[str] = None
    progress: Optional[int] = 0
    contract_id: str
    export_format: str
    file_path: str
    file_name: str
    size: int
    generated_at: str
    template_version: Optional[str] = None
    locale: Optional[str] = None
    brand: Optional[str] = ""


class TaxAuditExportJobStatusResponse(BaseModel):
    export_id: str
    contract_id: str
    status: str
    progress: int
    export_format: str
    template_version: str
    locale: str
    include_appendix: bool
    file_path: Optional[str] = None
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class TaxAuditCleanupRunRequest(BaseModel):
    retention_days: Optional[int] = 30


class TaxAuditCleanupRunResponse(BaseModel):
    job_id: str
    status: str
    retention_days: int
    archived_contracts: int
    deleted_files: int
    cutoff: str


class TaxAuditCleanupJobItem(BaseModel):
    id: str
    status: str
    retention_days: int
    started_at: str
    finished_at: Optional[str] = None
    archived_contracts: int
    deleted_files: int
    details_json: Optional[str] = None
    error: Optional[str] = None
    created_by: Optional[str] = None
    created_at: str
    updated_at: Optional[str] = None


class TaxAuditCleanupJobListResponse(BaseModel):
    total: int
    items: List[TaxAuditCleanupJobItem]


class TaxAuditArchiveRecordItem(BaseModel):
    id: str
    contract_document_id: str
    archive_path: str
    archived_at: str
    archived_by: Optional[str] = None
    source_job_id: Optional[str] = None
    created_at: str
    updated_at: Optional[str] = None


class TaxAuditArchiveRecordListResponse(BaseModel):
    total: int
    items: List[TaxAuditArchiveRecordItem]


class TaxAuditIssueItem(BaseModel):
    id: str
    contract_document_id: str
    clause_id: Optional[str] = None
    rule_id: Optional[str] = None
    risk_level: str
    issue_text: str
    suggestion: Optional[str] = None
    reviewer_status: str
    reviewer_note: Optional[str] = None
    created_at: str
    updated_at: Optional[str] = None


class TaxAuditIssueListResponse(BaseModel):
    contract_id: str
    total: int
    items: List[TaxAuditIssueItem]
