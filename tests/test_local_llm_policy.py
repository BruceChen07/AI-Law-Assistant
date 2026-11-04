import pytest

from app.local_llm.policy import (
    assert_local_runtime_enabled,
    ensure_llamacpp_target,
)


def test_local_runtime_is_enabled_by_default(monkeypatch):
    monkeypatch.delenv("ENABLE_LOCAL_LLM", raising=False)
    monkeypatch.delenv("ENABLE_CLOUD_API_TEST", raising=False)

    assert_local_runtime_enabled()


def test_cloud_api_test_flag_is_rejected(monkeypatch):
    monkeypatch.setenv("ENABLE_LOCAL_LLM", "true")
    monkeypatch.setenv("ENABLE_CLOUD_API_TEST", "true")

    with pytest.raises(RuntimeError, match="not supported"):
        assert_local_runtime_enabled()


@pytest.mark.parametrize(
    ("provider", "api_base"),
    [
        ("ollama", "http://127.0.0.1:11434/v1"),
        ("deepseek", "https://api.deepseek.com/v1"),
        ("llama_cpp", "https://example.com/v1"),
    ],
)
def test_non_llamacpp_or_non_loopback_targets_are_rejected(provider, api_base):
    with pytest.raises(RuntimeError):
        ensure_llamacpp_target(provider, api_base)


def test_loopback_llamacpp_target_is_allowed(monkeypatch):
    monkeypatch.setenv("ENABLE_LOCAL_LLM", "true")
    monkeypatch.setenv("ENABLE_CLOUD_API_TEST", "false")

    ensure_llamacpp_target("llama_cpp", "http://127.0.0.1:18080/v1")
