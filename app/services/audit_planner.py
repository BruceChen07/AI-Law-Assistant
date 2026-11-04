from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import Any, Dict, Sequence

from app.services.audit_normalizer import build_coverage_manifest
from app.services.audit_prompt_v2 import MessageTokenCounter, build_prompt
from app.services.audit_types import (
    AuditUnit,
    ChunkPlan,
    CoverageManifest,
    SourceDocument,
    SourceRef,
)


class AuditPolicyError(ValueError):
    code = "INVALID_POLICY"


class TokenBudgetExceededError(RuntimeError):
    code = "TOKEN_BUDGET_EXCEEDED"


class CoverageIncompleteError(RuntimeError):
    code = "COVERAGE_INCOMPLETE"


@dataclass(frozen=True)
class AuditPolicy:
    context_tokens: int = 16384
    safety_tokens: int = 1024
    output_tokens: int = 1536
    input_target_tokens: int = 7000
    max_chunks: int = 64

    @classmethod
    def from_config(cls, cfg: Dict[str, Any]) -> "AuditPolicy":
        raw = cfg.get("audit_optimization")
        source = raw if isinstance(raw, dict) else cfg

        def integer(name: str, default: int) -> int:
            value = source[name] if name in source else default
            if isinstance(value, bool):
                raise AuditPolicyError(f"INVALID_POLICY: {name} must be an integer")
            try:
                parsed = int(value)
            except (TypeError, ValueError) as exc:
                raise AuditPolicyError(
                    f"INVALID_POLICY: {name} must be an integer"
                ) from exc
            return parsed

        policy = cls(
            context_tokens=integer("context_tokens", cls.context_tokens),
            safety_tokens=integer("safety_tokens", cls.safety_tokens),
            output_tokens=integer("output_tokens", cls.output_tokens),
            input_target_tokens=integer(
                "input_target_tokens", cls.input_target_tokens
            ),
            max_chunks=integer("max_chunks", cls.max_chunks),
        )
        if policy.context_tokens < 2048:
            raise AuditPolicyError("INVALID_POLICY: context_tokens must be >= 2048")
        if policy.safety_tokens < 256:
            raise AuditPolicyError("INVALID_POLICY: safety_tokens must be >= 256")
        if not 512 <= policy.output_tokens <= 4096:
            raise AuditPolicyError(
                "INVALID_POLICY: output_tokens must be between 512 and 4096"
            )
        hard_input = (
            policy.context_tokens - policy.output_tokens - policy.safety_tokens
        )
        if hard_input <= 0:
            raise AuditPolicyError(
                "INVALID_POLICY: output and safety reserves exceed context"
            )
        if not 1 <= policy.input_target_tokens <= hard_input:
            raise AuditPolicyError(
                "INVALID_POLICY: input_target_tokens exceeds available input"
            )
        if not 1 <= policy.max_chunks <= 256:
            raise AuditPolicyError(
                "INVALID_POLICY: max_chunks must be between 1 and 256"
            )
        return policy

    def as_prompt_policy(self) -> Dict[str, int]:
        return {
            "context_tokens": self.context_tokens,
            "safety_tokens": self.safety_tokens,
            "output_tokens": self.output_tokens,
        }


@dataclass(frozen=True)
class AuditPlan:
    chunks: tuple[ChunkPlan, ...]
    units: tuple[AuditUnit, ...]
    coverage: CoverageManifest
    plan_version: int = 1


def _unit_id(
    source: SourceDocument,
    ref: SourceRef,
    parent_unit_id: str,
) -> str:
    payload = (
        f"{source.source_hash}|split-v2|{parent_unit_id}|"
        f"{ref.start}:{ref.end}"
    )
    return "u" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _choose_split(text: str) -> int:
    if len(text) < 2:
        return 0
    midpoint = len(text) // 2
    punctuation = set("\n。！？；;.!?")
    for distance in range(0, min(512, midpoint)):
        for candidate in (midpoint + distance, midpoint - distance):
            if 0 < candidate < len(text) and text[candidate - 1] in punctuation:
                return candidate
    return midpoint


def _split_unit_once(
    source: SourceDocument,
    unit: AuditUnit,
) -> tuple[AuditUnit, AuditUnit]:
    if len(unit.source_refs) != 1:
        raise TokenBudgetExceededError(
            "TOKEN_BUDGET_EXCEEDED: multi-range unit cannot be split safely"
        )
    ref = unit.source_refs[0]
    raw = source.text[ref.start:ref.end]
    split_at = _choose_split(raw)
    if split_at <= 0 or split_at >= len(raw):
        raise TokenBudgetExceededError(
            "TOKEN_BUDGET_EXCEEDED: unit cannot be split further"
        )
    absolute = ref.start + split_at
    left_ref = SourceRef(
        start=ref.start,
        end=absolute,
        page_no=ref.page_no,
        preview_clause_ids=ref.preview_clause_ids,
    )
    right_ref = SourceRef(
        start=absolute,
        end=ref.end,
        page_no=ref.page_no,
        preview_clause_ids=ref.preview_clause_ids,
    )
    parent = unit.parent_unit_id or unit.unit_id
    return (
        AuditUnit(
            unit_id=_unit_id(source, left_ref, parent),
            ordinal=unit.ordinal,
            text=source.text[left_ref.start:left_ref.end],
            source_refs=(left_ref,),
            section_path=unit.section_path,
            parent_unit_id=parent,
        ),
        AuditUnit(
            unit_id=_unit_id(source, right_ref, parent),
            ordinal=unit.ordinal + 1,
            text=source.text[right_ref.start:right_ref.end],
            source_refs=(right_ref,),
            section_path=unit.section_path,
            parent_unit_id=parent,
        ),
    )


def plan_audit(
    source: SourceDocument,
    units: Sequence[AuditUnit],
    evidence_snapshot: Sequence[Dict[str, Any]],
    policy: AuditPolicy,
    token_counter: MessageTokenCounter,
    *,
    common_context: str = "",
    language: str = "zh",
    coverage_scope_units: Sequence[AuditUnit] | None = None,
    plan_version: int = 1,
) -> AuditPlan:
    if plan_version < 1:
        raise ValueError("plan_version must be positive")
    pending = list(units)
    final_units: list[AuditUnit] = []
    chunks: list[ChunkPlan] = []
    current: list[AuditUnit] = []
    current_bundle = None

    def render(candidate: Sequence[AuditUnit]):
        return build_prompt(
            candidate,
            common_context,
            evidence_snapshot,
            policy.as_prompt_policy(),
            token_counter,
            language=language,
        )

    def finish_current() -> None:
        nonlocal current, current_bundle
        if not current or current_bundle is None:
            return
        if len(chunks) >= policy.max_chunks:
            raise TokenBudgetExceededError(
                "TOKEN_BUDGET_EXCEEDED: plan exceeds max_chunks"
            )
        ordinal = len(chunks) + 1
        normalized = tuple(
            replace(unit, ordinal=len(final_units) + index + 1)
            for index, unit in enumerate(current)
        )
        final_units.extend(normalized)
        chunks.append(
            ChunkPlan(
                chunk_id=f"chunk-{ordinal:03d}",
                ordinal=ordinal,
                units=normalized,
                bundle=current_bundle,
            )
        )
        current = []
        current_bundle = None

    while pending:
        unit = pending.pop(0)
        candidate = [*current, unit]
        try:
            candidate_bundle = render(candidate)
        except ValueError as exc:
            if "TOKEN_BUDGET_EXCEEDED" not in str(exc):
                raise
            if current:
                finish_current()
                pending.insert(0, unit)
                continue
            left, right = _split_unit_once(source, unit)
            pending[0:0] = [left, right]
            continue

        if (
            current
            and candidate_bundle.input_tokens > policy.input_target_tokens
        ):
            finish_current()
            pending.insert(0, unit)
            continue
        current = candidate
        current_bundle = candidate_bundle

    finish_current()
    if not chunks and any(not char.isspace() for char in source.text):
        raise CoverageIncompleteError("COVERAGE_INCOMPLETE: no audit chunks planned")
    coverage = build_coverage_manifest(source, final_units)
    expected_coverage = (
        build_coverage_manifest(source, coverage_scope_units)
        if coverage_scope_units is not None
        else None
    )
    invalid_scope = (
        expected_coverage is not None
        and (
            coverage.missing_ranges != expected_coverage.missing_ranges
            or coverage.planned_nonspace_chars
            != expected_coverage.planned_nonspace_chars
        )
    )
    if (
        coverage.duplicate_primary_ranges
        or invalid_scope
        or (expected_coverage is None and coverage.missing_ranges)
    ):
        raise CoverageIncompleteError(
            "COVERAGE_INCOMPLETE: planned source ranges are not exact"
        )
    for chunk in chunks:
        bundle = chunk.bundle
        if (
            bundle.input_tokens
            + bundle.output_tokens
            + bundle.safety_tokens
            > bundle.context_tokens
        ):
            raise TokenBudgetExceededError(
                f"TOKEN_BUDGET_EXCEEDED: {chunk.chunk_id} violates hard budget"
            )
    return AuditPlan(
        chunks=tuple(chunks),
        units=tuple(final_units),
        coverage=coverage,
        plan_version=plan_version,
    )


def replan_incomplete_chunk(
    source: SourceDocument,
    chunk: ChunkPlan,
    evidence_snapshot: Sequence[Dict[str, Any]],
    policy: AuditPolicy,
    token_counter: MessageTokenCounter,
    *,
    common_context: str = "",
    language: str = "zh",
    plan_version: int,
) -> AuditPlan:
    if plan_version <= 1:
        raise ValueError("replanned chunk requires plan_version greater than 1")
    if not chunk.units:
        raise CoverageIncompleteError(
            "COVERAGE_INCOMPLETE: cannot replan an empty chunk"
        )

    reduced_target = max(
        1,
        min(
            policy.input_target_tokens,
            max(1, chunk.bundle.input_tokens // 2),
        ),
    )
    recovery_context = policy.context_tokens
    if len(chunk.units) == 1:
        minimum_context = policy.output_tokens + policy.safety_tokens + 512
        recovery_context = max(
            2048,
            minimum_context,
            policy.output_tokens
            + policy.safety_tokens
            + max(1, chunk.bundle.input_tokens // 2),
        )
        if recovery_context >= policy.context_tokens:
            raise TokenBudgetExceededError(
                "TOKEN_BUDGET_EXCEEDED: chunk cannot be replanned more strictly"
            )
        reduced_target = min(
            reduced_target,
            recovery_context - policy.output_tokens - policy.safety_tokens,
        )

    recovery_policy = replace(
        policy,
        context_tokens=recovery_context,
        input_target_tokens=reduced_target,
        max_chunks=max(2, policy.max_chunks),
    )
    replanned = plan_audit(
        source,
        chunk.units,
        evidence_snapshot,
        recovery_policy,
        token_counter,
        common_context=common_context,
        language=language,
        coverage_scope_units=chunk.units,
        plan_version=plan_version,
    )
    children = tuple(
        replace(
            child,
            chunk_id=f"{chunk.chunk_id}.r{plan_version}.{index:03d}",
            ordinal=index,
            parent_chunk_id=chunk.chunk_id,
        )
        for index, child in enumerate(replanned.chunks, start=1)
    )
    if len(children) < 2:
        raise TokenBudgetExceededError(
            "TOKEN_BUDGET_EXCEEDED: stricter replan did not split the chunk"
        )
    return replace(replanned, chunks=children)
