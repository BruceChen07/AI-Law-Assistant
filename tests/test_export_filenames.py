import re

from app.services.export_filenames import (
    build_export_filename,
    export_fallback_stem,
    localized_export_suffix,
    sanitize_export_filename_stem,
)


def test_sanitize_keeps_readable_chinese_and_replaces_illegal_chars():
    value = "采购合同（终版）: 华北区/2026?.docx"
    assert sanitize_export_filename_stem(value) == "采购合同（终版）_华北区_2026"


def test_sanitize_keeps_cjk_and_brackets_for_plain_names():
    value = "#MSA Kellanova成品仓库管理服务协议.docx"
    assert sanitize_export_filename_stem(value) == "#MSA_Kellanova成品仓库管理服务协议"


def test_sanitize_falls_back_when_name_is_empty():
    assert sanitize_export_filename_stem("") == "contract"
    assert sanitize_export_filename_stem("   .docx ") == "contract"
    assert sanitize_export_filename_stem("", fallback="合同") == "合同"


def test_sanitize_guards_windows_reserved_names():
    assert sanitize_export_filename_stem("CON.docx") == "CON_"
    assert sanitize_export_filename_stem("lpt1.txt") == "lpt1_"


def test_sanitize_truncates_long_names():
    long_name = "合同" * 100 + ".docx"
    result = sanitize_export_filename_stem(long_name)
    assert len(result) <= 80
    assert result.startswith("合同")


def test_localized_suffix_follows_locale():
    assert localized_export_suffix("report", "zh-CN") == "审计报告"
    assert localized_export_suffix("data", "zh-CN") == "审计数据"
    assert localized_export_suffix("comments", "zh-CN") == "批注版"
    assert localized_export_suffix("report", "en-US") == "audit_report"
    assert localized_export_suffix("comments", "en-US") == "commented"
    assert localized_export_suffix("tax_data", "zh-CN") == "税务审计数据"
    assert export_fallback_stem("zh-CN") == "合同"
    assert export_fallback_stem("en-US") == "contract"


def test_build_export_filename_uses_contract_name_and_export_time():
    result = build_export_filename(
        source_filename="采购合同（终版）.docx",
        generated_at="2026-07-22T12:49:30+08:00",
        suffix=localized_export_suffix("report", "zh-CN"),
        ext="docx",
        fallback_stem=export_fallback_stem("zh-CN"),
    )
    assert re.match(r"^采购合同（终版）_\d{8}_\d{6}_审计报告\.docx$", result)


def test_build_export_filename_never_emits_generic_contract_prefix_when_name_exists():
    result = build_export_filename(
        source_filename="MSA Kellanova成品仓库管理服务协议.docx",
        generated_at="2026-11-04T18:08:33+08:00",
        suffix=localized_export_suffix("comments", "zh-CN"),
        ext="docx",
        fallback_stem=export_fallback_stem("zh-CN"),
    )
    assert result.startswith("MSA_Kellanova成品仓库管理服务协议_")
    assert result.endswith("_批注版.docx")
    assert not result.startswith("contract_")


def test_build_export_filename_defaults_timestamp_to_now_when_missing():
    result = build_export_filename(
        source_filename="合同.docx",
        generated_at=None,
        suffix=localized_export_suffix("data", "zh-CN"),
        ext="json",
    )
    assert re.match(r"^合同_\d{8}_\d{6}_审计数据\.json$", result)
