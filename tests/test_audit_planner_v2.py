import math

import pytest

from app.services.audit_normalizer import (
    SourceMappingError,
    build_coverage_manifest,
    normalize_units,
)
from app.services.audit_planner import (
    AuditPolicy,
    AuditPolicyError,
    TokenBudgetExceededError,
    plan_audit,
    replan_incomplete_chunk,
)
from app.services.audit_prompt_v2 import TokenizerUnavailableError
from app.services.audit_source import source_from_pages, source_from_text
from app.services.audit_types import AuditUnit, SourceRef


class _ExactCounter:
    exact = True

    def count_messages(self, messages):
        chars = sum(len(str(item.get("content") or "")) for item in messages)
        return 16 + math.ceil(chars / 4)


class _EstimatedCounter(_ExactCounter):
    exact = False


def _evidence():
    return [
        {
            "citation_id": "law-1",
            "law_title": "中华人民共和国增值税法",
            "article_no": "第一条",
            "content": "依法计算并缴纳增值税。",
        }
    ]


def test_normalizer_preserves_source_ranges_across_pages():
    source = source_from_pages(
        document_id="doc-1",
        source_hash="hash-1",
        pages=[
            (1, "第一条 标的\n设备销售，税率13%。\n"),
            (2, "第二条 发票\n应开具增值税专用发票。\n"),
        ],
    )

    units = normalize_units(source, target_chars=128)
    coverage = build_coverage_manifest(source, units)

    assert coverage.planned_ratio == 1.0
    assert coverage.missing_ranges == ()
    assert coverage.duplicate_primary_ranges == ()
    assert {ref.page_no for unit in units for ref in unit.source_refs} == {1, 2}
    assert "".join(
        source.text[ref.start:ref.end]
        for unit in units
        for ref in unit.source_refs
    ).replace("\f", "") == source.text.replace("\f", "")


def test_normalizer_maps_repeated_preview_text_to_distinct_source_ranges():
    repeated = "设备销售，税率13%。"
    source = source_from_text(f"第一条\n{repeated}\n第二条\n{repeated}\n")
    preview = [
        {"clause_id": "c1", "clause_text": repeated},
        {"clause_id": "c2", "clause_text": repeated},
    ]

    units = normalize_units(source, preview, target_chars=128)
    mapped = [
        clause_id
        for unit in units
        for ref in unit.source_refs
        for clause_id in ref.preview_clause_ids
    ]

    assert mapped == ["c1", "c2"]
    assert build_coverage_manifest(source, units).planned_ratio == 1.0


def test_source_preserves_unknown_pages_and_extraction_warnings():
    source = source_from_pages(
        document_id="ocr-doc",
        source_hash="ocr-hash",
        pages=[(None, "扫描识别文本")],
        extraction_warnings=[
            "OCR_PAGE_BOUNDARIES_UNAVAILABLE",
            "OCR_USED:test",
        ],
    )

    units = normalize_units(source)
    coverage = build_coverage_manifest(source, units)

    assert source.pages[0].page_no is None
    assert units[0].source_refs[0].page_no is None
    assert coverage.extraction_warnings == (
        "OCR_PAGE_BOUNDARIES_UNAVAILABLE",
        "OCR_USED:test",
    )


def test_coverage_detects_duplicate_primary_range():
    source = source_from_text("第一条 税率为13%。")
    ref = SourceRef(start=0, end=len(source.text), page_no=None)
    units = [
        AuditUnit("u1", 1, source.text, (ref,)),
        AuditUnit("u2", 2, source.text, (ref,)),
    ]

    coverage = build_coverage_manifest(source, units)

    assert coverage.duplicate_primary_ranges


def test_planner_covers_797_fragments_without_oversized_tail():
    text = "\n".join(f"第{i}项 税务约定{i}。" for i in range(1, 798))
    source = source_from_text(text)
    units = normalize_units(source, target_chars=128)
    policy = AuditPolicy(
        context_tokens=4096,
        safety_tokens=256,
        output_tokens=512,
        input_target_tokens=700,
        max_chunks=64,
    )

    plan = plan_audit(
        source,
        units,
        _evidence(),
        policy,
        _ExactCounter(),
        common_context="交易包含设备销售。",
    )

    assert plan.coverage.planned_ratio == 1.0
    assert 2 <= len(plan.chunks) <= 64
    assert max(len(chunk.units) for chunk in plan.chunks) < len(plan.units) / 2
    assert all(
        chunk.bundle.input_tokens
        + chunk.bundle.output_tokens
        + chunk.bundle.safety_tokens
        <= chunk.bundle.context_tokens
        for chunk in plan.chunks
    )


def test_planner_splits_single_oversized_unit_without_losing_text():
    source = source_from_text("第一条 " + ("设备销售及开票约定。" * 1200))
    units = normalize_units(source, target_chars=10000)
    policy = AuditPolicy(
        context_tokens=2048,
        safety_tokens=256,
        output_tokens=512,
        input_target_tokens=900,
        max_chunks=64,
    )

    plan = plan_audit(
        source,
        units,
        _evidence(),
        policy,
        _ExactCounter(),
    )

    assert len(plan.units) > 1
    assert plan.coverage.planned_ratio == 1.0
    assert all(unit.parent_unit_id for unit in plan.units)


def test_replan_replaces_only_incomplete_chunk_with_exact_child_coverage():
    text = "\n".join(
        f"第{index}条 " + ("设备销售及开票约定。" * 45)
        for index in range(1, 13)
    )
    source = source_from_text(text)
    units = normalize_units(source, target_chars=512)
    policy = AuditPolicy(
        context_tokens=4096,
        safety_tokens=256,
        output_tokens=512,
        input_target_tokens=620,
        max_chunks=64,
    )
    initial = plan_audit(
        source,
        units,
        _evidence(),
        policy,
        _ExactCounter(),
        common_context="交易包含设备销售。",
    )
    assert len(initial.chunks) >= 2
    completed = initial.chunks[0]
    incomplete = initial.chunks[1]

    replanned = replan_incomplete_chunk(
        source,
        incomplete,
        _evidence(),
        policy,
        _ExactCounter(),
        common_context="交易包含设备销售。",
        plan_version=initial.plan_version + 1,
    )

    expected = build_coverage_manifest(source, incomplete.units)
    assert replanned.plan_version == 2
    assert len(replanned.chunks) >= 2
    assert all(
        child.parent_chunk_id == incomplete.chunk_id
        for child in replanned.chunks
    )
    assert all(
        completed.chunk_id != child.chunk_id
        for child in replanned.chunks
    )
    assert replanned.coverage.planned_nonspace_chars == (
        expected.planned_nonspace_chars
    )
    assert replanned.coverage.missing_ranges == expected.missing_ranges
    assert replanned.coverage.duplicate_primary_ranges == ()


def test_planner_rejects_more_than_max_chunks():
    text = "\n".join(
        f"第{i}条 " + ("单独条款内容。" * 80)
        for i in range(1, 66)
    )
    source = source_from_text(text)
    units = normalize_units(source, target_chars=700)
    policy = AuditPolicy(
        context_tokens=4096,
        safety_tokens=256,
        output_tokens=512,
        input_target_tokens=300,
        max_chunks=10,
    )

    with pytest.raises(TokenBudgetExceededError, match="max_chunks"):
        plan_audit(source, units, _evidence(), policy, _ExactCounter())


def test_planner_requires_exact_token_counter():
    source = source_from_text("第一条 税率为13%。")
    units = normalize_units(source)

    with pytest.raises(TokenizerUnavailableError):
        plan_audit(
            source,
            units,
            _evidence(),
            AuditPolicy(),
            _EstimatedCounter(),
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"context_tokens": 0},
        {"safety_tokens": 0},
        {"output_tokens": 511},
        {"input_target_tokens": 0},
        {"max_chunks": 0},
    ],
)
def test_policy_rejects_invalid_zero_or_out_of_range_values(overrides):
    with pytest.raises(AuditPolicyError):
        AuditPolicy.from_config({"audit_optimization": overrides})
