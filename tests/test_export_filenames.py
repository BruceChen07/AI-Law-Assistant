import re

from app.services.export_filenames import build_export_filename, sanitize_export_filename_stem


def test_sanitize_export_filename_stem_keeps_readable_chinese_name():
    value = '采购合同（终版）: 华北区/2026?.docx'
    result = sanitize_export_filename_stem(value)
    assert result == "采购合同（终版）_华北区_2026"


def test_build_export_filename_includes_source_name_and_timestamp():
    result = build_export_filename(
        source_filename="采购合同（终版）.docx",
        generated_at="2026-07-22T12:49:30+08:00",
        suffix="contract_audit_report",
        ext="docx",
    )
    assert result == "采购合同（终版）_20260722_124930_contract_audit_report.docx"
    assert re.match(r"^采购合同（终版）_\d{8}_\d{6}_contract_audit_report\.docx$", result)
