from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class PageSpan:
    page_no: Optional[int]
    start: int
    end: int


@dataclass(frozen=True)
class SourceRef:
    start: int
    end: int
    page_no: Optional[int]
    preview_clause_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceDocument:
    document_id: str
    source_hash: str
    parser_version: str
    text: str
    pages: tuple[PageSpan, ...]
    extraction_warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class AuditUnit:
    unit_id: str
    ordinal: int
    text: str
    source_refs: tuple[SourceRef, ...]
    section_path: str = ""
    parent_unit_id: Optional[str] = None


@dataclass(frozen=True)
class CoverageManifest:
    source_nonspace_chars: int
    planned_nonspace_chars: int
    audited_nonspace_chars: int
    missing_ranges: tuple[tuple[int, int], ...] = ()
    duplicate_primary_ranges: tuple[tuple[int, int], ...] = ()
    extraction_warnings: tuple[str, ...] = ()

    @property
    def planned_ratio(self) -> float:
        if self.source_nonspace_chars == 0:
            return 1.0
        return self.planned_nonspace_chars / self.source_nonspace_chars

    @property
    def audited_ratio(self) -> float:
        if self.source_nonspace_chars == 0:
            return 1.0
        return self.audited_nonspace_chars / self.source_nonspace_chars


@dataclass(frozen=True)
class PromptBundle:
    messages: tuple[Dict[str, Any], ...]
    prompt_hash: str
    prefix_hash: str
    input_tokens: int
    output_tokens: int
    safety_tokens: int
    context_tokens: int
    primary_unit_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    template_version: str


@dataclass(frozen=True)
class ChunkPlan:
    chunk_id: str
    ordinal: int
    units: tuple[AuditUnit, ...]
    bundle: PromptBundle
    parent_chunk_id: Optional[str] = None
    kind: str = "chunk"


@dataclass
class ChunkResult:
    chunk_id: str
    attempt_no: int
    risks: List[Dict[str, Any]] = field(default_factory=list)
    valid: bool = False
    usage: Dict[str, Any] = field(default_factory=dict)
    warnings: List[Dict[str, str]] = field(default_factory=list)
    error_code: Optional[str] = None
