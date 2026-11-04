"""审计阶段是否构建预览清单的单元测试。"""

from app.services.contract_audit import _should_build_preview_manifest


def test_preview_manifest_build_defaults_to_enabled():
    assert _should_build_preview_manifest({}) is True


def test_preview_manifest_build_can_be_disabled():
    cfg = {"contract_preview": {"audit_build_manifest": False}}

    assert _should_build_preview_manifest(cfg) is False


def test_preview_manifest_ignores_non_dict_block():
    cfg = {"contract_preview": "disabled"}

    assert _should_build_preview_manifest(cfg) is True
