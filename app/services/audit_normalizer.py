from __future__ import annotations

import hashlib
import re
from typing import Iterable, Sequence

from app.services.audit_types import (
    AuditUnit,
    CoverageManifest,
    SourceDocument,
    SourceRef,
)


NORMALIZER_VERSION = "audit-normalizer-v2"
_HEADING_RE = re.compile(
    r"^\s*(?:"
    r"第[一二三四五六七八九十百千0-9]+[章节条款]"
    r"|[0-9]+(?:\.[0-9]+){0,3}[.)、]?"
    r"|[一二三四五六七八九十]+、"
    r"|(?:Article|Section|Chapter|Part|Clause)\s+[0-9IVXLCM]+"
    r")",
    flags=re.IGNORECASE,
)


class SourceMappingError(RuntimeError):
    code = "SOURCE_MAPPING_INVALID"


def _preview_offsets(
    source: SourceDocument,
    preview_clauses: Sequence[dict],
) -> list[tuple[int, int, str]]:
    offsets: list[tuple[int, int, str]] = []
    cursor = 0
    for clause in preview_clauses or []:
        if not isinstance(clause, dict):
            continue
        clause_id = str(clause.get("clause_id") or "").strip()
        body = str(clause.get("clause_text") or clause.get("text") or "")
        if not clause_id or not body:
            continue
        start = source.text.find(body, cursor)
        if start < 0:
            start = source.text.find(body)
        if start < 0:
            continue
        end = start + len(body)
        offsets.append((start, end, clause_id))
        cursor = end
    return offsets


def _overlapping_clause_ids(
    start: int,
    end: int,
    offsets: Iterable[tuple[int, int, str]],
) -> tuple[str, ...]:
    return tuple(
        clause_id
        for clause_start, clause_end, clause_id in offsets
        if clause_start < end and clause_end > start
    )


def _make_unit_id(
    source: SourceDocument,
    refs: Sequence[SourceRef],
    *,
    parent_unit_id: str | None = None,
) -> str:
    spans = ";".join(f"{ref.start}:{ref.end}" for ref in refs)
    payload = (
        f"{source.source_hash}|{source.parser_version}|{NORMALIZER_VERSION}|"
        f"{parent_unit_id or ''}|{spans}"
    )
    return "u" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _line_ranges(text: str, start: int, end: int) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    cursor = start
    while cursor < end:
        newline = text.find("\n", cursor, end)
        next_cursor = end if newline < 0 else newline + 1
        if any(not char.isspace() for char in text[cursor:next_cursor]):
            ranges.append((cursor, next_cursor))
        cursor = next_cursor
    return ranges


def normalize_units(
    source: SourceDocument,
    preview_clauses: Sequence[dict] = (),
    *,
    target_chars: int = 1800,
) -> list[AuditUnit]:
    if target_chars < 128:
        raise ValueError("target_chars must be at least 128")
    preview_offsets = _preview_offsets(source, preview_clauses)
    units: list[AuditUnit] = []
    ordinal = 0
    for page in source.pages:
        ranges = _line_ranges(source.text, page.start, page.end)
        current_start: int | None = None
        current_end: int | None = None
        current_heading = ""
        for line_start, line_end in ranges:
            line_text = source.text[line_start:line_end]
            starts_heading = bool(_HEADING_RE.match(line_text))
            would_exceed = (
                current_start is not None
                and line_end - current_start > target_chars
            )
            if current_start is not None and (starts_heading or would_exceed):
                ordinal += 1
                ref = SourceRef(
                    start=current_start,
                    end=current_end or current_start,
                    page_no=page.page_no,
                    preview_clause_ids=_overlapping_clause_ids(
                        current_start, current_end or current_start, preview_offsets
                    ),
                )
                units.append(
                    AuditUnit(
                        unit_id=_make_unit_id(source, [ref]),
                        ordinal=ordinal,
                        text=source.text[ref.start:ref.end],
                        source_refs=(ref,),
                        section_path=current_heading,
                    )
                )
                current_start = None
                current_end = None
                current_heading = ""
            if current_start is None:
                current_start = line_start
                current_heading = line_text.strip() if starts_heading else ""
            current_end = line_end
        if current_start is not None:
            ordinal += 1
            ref = SourceRef(
                start=current_start,
                end=current_end or current_start,
                page_no=page.page_no,
                preview_clause_ids=_overlapping_clause_ids(
                    current_start, current_end or current_start, preview_offsets
                ),
            )
            units.append(
                AuditUnit(
                    unit_id=_make_unit_id(source, [ref]),
                    ordinal=ordinal,
                    text=source.text[ref.start:ref.end],
                    source_refs=(ref,),
                    section_path=current_heading,
                )
            )

    manifest = build_coverage_manifest(source, units)
    if manifest.missing_ranges or manifest.duplicate_primary_ranges:
        raise SourceMappingError(
            "SOURCE_MAPPING_INVALID: normalized units do not preserve source text"
        )
    return units


def _ranges_from_counts(
    source: SourceDocument,
    counts: list[int],
    predicate,
) -> tuple[tuple[int, int], ...]:
    ranges: list[tuple[int, int]] = []
    start: int | None = None
    for index, char in enumerate(source.text):
        selected = not char.isspace() and predicate(counts[index])
        if selected and start is None:
            start = index
        elif not selected and start is not None:
            ranges.append((start, index))
            start = None
    if start is not None:
        ranges.append((start, len(source.text)))
    return tuple(ranges)


def build_coverage_manifest(
    source: SourceDocument,
    units: Sequence[AuditUnit],
    *,
    audited_unit_ids: Iterable[str] = (),
) -> CoverageManifest:
    counts = [0] * len(source.text)
    audited_counts = [0] * len(source.text)
    audited = set(str(item) for item in audited_unit_ids)
    for unit in units:
        for ref in unit.source_refs:
            if ref.start < 0 or ref.end > len(source.text) or ref.end <= ref.start:
                raise SourceMappingError(
                    f"SOURCE_MAPPING_INVALID: invalid range {ref.start}:{ref.end}"
                )
            for index in range(ref.start, ref.end):
                if source.text[index].isspace():
                    continue
                counts[index] += 1
                if unit.unit_id in audited:
                    audited_counts[index] += 1
    source_chars = sum(1 for char in source.text if not char.isspace())
    planned_chars = sum(
        1
        for index, char in enumerate(source.text)
        if not char.isspace() and counts[index] > 0
    )
    audited_chars = sum(
        1
        for index, char in enumerate(source.text)
        if not char.isspace() and audited_counts[index] > 0
    )
    return CoverageManifest(
        source_nonspace_chars=source_chars,
        planned_nonspace_chars=planned_chars,
        audited_nonspace_chars=audited_chars,
        missing_ranges=_ranges_from_counts(source, counts, lambda count: count == 0),
        duplicate_primary_ranges=_ranges_from_counts(
            source, counts, lambda count: count > 1
        ),
        extraction_warnings=source.extraction_warnings,
    )
