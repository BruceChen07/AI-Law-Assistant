from app.services.local_llm_runtime import call_with_fallback, get_local_worker_limit


def test_call_with_fallback_retries_cloud_on_exception():
    calls = []

    class FakeLLM:
        def chat_with_profile(self, messages, task_profile, overrides=None):
            payload = {
                "task_profile": task_profile,
                "overrides": dict(overrides or {}),
            }
            calls.append(payload)
            if payload["overrides"].get("_model_role") == "cloud_fallback":
                return '{"ok": true}', {"_route": {"selected_role": "cloud_fallback"}}
            raise RuntimeError("local model timeout")

    cfg = {"local_llm": {"cloud_fallback_enabled": True, "execution": {"fallback_on_error": True}}}
    text, raw, meta = call_with_fallback(
        FakeLLM(),
        cfg,
        [{"role": "user", "content": "test"}],
        "tax_match_small",
    )
    assert text == '{"ok": true}'
    assert raw["_route"]["selected_role"] == "cloud_fallback"
    assert meta["fallback_used"] is True
    assert meta["fallback_reason"] == "error"
    assert len(calls) == 2


def test_call_with_fallback_retries_cloud_on_invalid_result():
    calls = []

    class FakeLLM:
        def chat_with_profile(self, messages, task_profile, overrides=None):
            payload = {
                "task_profile": task_profile,
                "overrides": dict(overrides or {}),
            }
            calls.append(payload)
            if payload["overrides"].get("_model_role") == "cloud_fallback":
                return '{"label":"compliant"}', {"_route": {"selected_role": "cloud_fallback"}}
            return 'not-json', {"_route": {"selected_role": "small"}}

    cfg = {"local_llm": {"cloud_fallback_enabled": True, "execution": {"fallback_on_invalid_json": True}}}
    text, _raw, meta = call_with_fallback(
        FakeLLM(),
        cfg,
        [{"role": "user", "content": "test"}],
        "tax_match_small",
        validator=lambda text, _raw: text.startswith("{"),
    )
    assert text == '{"label":"compliant"}'
    assert meta["fallback_used"] is True
    assert meta["fallback_reason"] == "invalid_result"
    assert len(calls) == 2


def test_get_local_worker_limit_prefers_local_execution_setting():
    cfg = {
        "tax_audit_max_workers": 4,
        "local_llm": {
            "execution": {
                "tax_match_max_workers": 2,
            }
        },
    }
    assert get_local_worker_limit(cfg, "tax_match_max_workers", "tax_audit_max_workers", 4, task_count=10) == 2
