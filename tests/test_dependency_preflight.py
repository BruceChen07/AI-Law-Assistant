"""启动依赖自检的单元测试。"""

import pytest

from app.core import dependency_preflight as dp


def _ok_check(name: str, critical: bool = True):
    return {
        "name": name,
        "label": name,
        "ok": True,
        "critical": critical,
        "detail": {},
        "hint": "",
    }


def _fail_check(name: str, critical: bool = True, hint: str = "fix me"):
    return {
        "name": name,
        "label": name,
        "ok": False,
        "critical": critical,
        "detail": {},
        "hint": hint,
    }


def test_service_deps_missing_marks_critical(monkeypatch):
    monkeypatch.setattr(dp, "_module_present", lambda name: name != "jieba")

    check = dp._check_python_deps()

    assert check["ok"] is False
    assert check["critical"] is True
    assert "jieba" in check["hint"]


def test_service_deps_does_not_require_mineru_only_packages():
    # MinerU 的 pipeline 依赖属于子进程环境，不能要求服务进程安装。
    required = {name for name, _ in dp.REQUIRED_MODULES}

    assert "pyclipper" not in required
    assert "omegaconf" not in required
    assert "dill" not in required


def test_mineru_env_hint_points_at_cli_python(monkeypatch):
    monkeypatch.setattr(dp, "_is_test_process", lambda: False)
    monkeypatch.setattr(
        dp, "_resolve_mineru_cli", lambda: r"C:\app\.venv\Scripts\mineru.exe")
    monkeypatch.setattr(
        dp, "_cli_python", lambda cli: r"C:\app\.venv\Scripts\python.exe")
    monkeypatch.setattr(
        dp, "_probe_python",
        lambda python, code, timeout=30: {
            "ok": False, "stderr": "No module named 'pyclipper'"},
    )

    check = dp._check_mineru_env()

    assert check["ok"] is False
    assert check["critical"] is True
    assert "pyclipper" in check["hint"]
    assert "app\\.venv" in check["hint"] or ".venv" in check["hint"]


def test_mineru_models_skipped_for_remote_source():
    check = dp._check_mineru_models({"mineru": {"model_source": "huggingface"}})

    assert check["ok"] is True
    assert check.get("skipped") is True


def test_collect_report_ok_when_all_checks_pass(monkeypatch):
    monkeypatch.setattr(dp, "_check_python_deps", lambda: _ok_check("service_deps"))
    monkeypatch.setattr(dp, "_check_mineru_cli", lambda: _ok_check("mineru_cli"))
    monkeypatch.setattr(dp, "_check_mineru_env", lambda: _ok_check("mineru_env"))
    monkeypatch.setattr(
        dp, "_check_mineru_models", lambda cfg: _ok_check("mineru_models"))
    monkeypatch.setattr(dp, "_check_embedding", lambda cfg: _ok_check("embedding"))
    monkeypatch.setattr(
        dp, "_check_llm", lambda cfg: _ok_check("llm_endpoint", critical=False))

    report = dp.collect_report({})

    assert report["ok"] is True
    assert report["critical_count"] == 0


def test_llm_failure_is_warning_only(monkeypatch):
    monkeypatch.setattr(dp, "_check_python_deps", lambda: _ok_check("service_deps"))
    monkeypatch.setattr(dp, "_check_mineru_cli", lambda: _ok_check("mineru_cli"))
    monkeypatch.setattr(dp, "_check_mineru_env", lambda: _ok_check("mineru_env"))
    monkeypatch.setattr(
        dp, "_check_mineru_models", lambda cfg: _ok_check("mineru_models"))
    monkeypatch.setattr(dp, "_check_embedding", lambda cfg: _ok_check("embedding"))
    monkeypatch.setattr(
        dp, "_check_llm",
        lambda cfg: _fail_check("llm_endpoint", critical=False, hint="start llama.cpp"))

    report = dp.collect_report({})

    assert report["ok"] is True
    assert report["warning_count"] == 1


def test_run_startup_preflight_strict_raises(monkeypatch):
    failing = {
        "ok": False,
        "critical_count": 1,
        "warning_count": 0,
        "checks": [_fail_check("mineru_env")],
        "critical": [_fail_check("mineru_env")],
        "warnings": [],
        "checked_at": "",
    }
    monkeypatch.setattr(dp, "collect_report", lambda cfg: failing)

    with pytest.raises(RuntimeError):
        dp.run_startup_preflight({"startup_dependency_strict": True})


def test_run_startup_preflight_non_strict_returns_report(monkeypatch):
    failing = {
        "ok": False,
        "critical_count": 1,
        "warning_count": 0,
        "checks": [_fail_check("mineru_env")],
        "critical": [_fail_check("mineru_env")],
        "warnings": [],
        "checked_at": "",
    }
    monkeypatch.setattr(dp, "collect_report", lambda cfg: failing)

    report = dp.run_startup_preflight({"startup_dependency_strict": False})

    assert report["ok"] is False
    assert dp.get_last_report()["critical_count"] == 1


def test_format_report_contains_hint():
    report = {
        "ok": False,
        "critical_count": 1,
        "warning_count": 0,
        "checks": [_fail_check("mineru_env", hint="pip install pyclipper")],
        "critical": [_fail_check("mineru_env", hint="pip install pyclipper")],
        "warnings": [],
        "checked_at": "",
    }

    text = dp.format_report(report)

    assert "FAIL" in text
    assert "pip install pyclipper" in text
