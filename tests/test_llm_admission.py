import threading
import time

import httpx
import pytest

from app.core.llm import LLMService
from app.core.llm_admission import (
    ModelAdmissionTimeout,
    admission_slot,
    normalize_endpoint,
)


def test_normalize_endpoint_shares_v1_and_runtime_base():
    assert normalize_endpoint("HTTP://LOCALHOST:18080/v1/") == (
        "http://localhost:18080"
    )
    assert normalize_endpoint("http://localhost:18080") == (
        "http://localhost:18080"
    )


def test_same_endpoint_allows_only_one_active_call():
    endpoint = "http://127.0.0.1:28081/v1"
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()

    def first():
        with admission_slot(endpoint, timeout_seconds=1):
            first_entered.set()
            release_first.wait(timeout=1)

    def second():
        first_entered.wait(timeout=1)
        with admission_slot(endpoint, timeout_seconds=1):
            second_entered.set()

    first_thread = threading.Thread(target=first)
    second_thread = threading.Thread(target=second)
    first_thread.start()
    second_thread.start()
    assert first_entered.wait(timeout=1)
    time.sleep(0.05)
    assert second_entered.is_set() is False
    release_first.set()
    first_thread.join(timeout=1)
    second_thread.join(timeout=1)

    assert second_entered.is_set() is True
    assert first_thread.is_alive() is False
    assert second_thread.is_alive() is False


def test_waiting_call_times_out_with_model_busy():
    endpoint = "http://127.0.0.1:28082/v1"

    with admission_slot(endpoint, timeout_seconds=1):
        with pytest.raises(ModelAdmissionTimeout, match="MODEL_BUSY"):
            with admission_slot(endpoint, timeout_seconds=0.02):
                pytest.fail("timed out call must not enter the slot")


def test_exception_releases_slot():
    endpoint = "http://127.0.0.1:28083/v1"

    with pytest.raises(RuntimeError, match="boom"):
        with admission_slot(endpoint, timeout_seconds=1):
            raise RuntimeError("boom")

    with admission_slot(endpoint, timeout_seconds=1):
        pass


def test_v2_can_disable_core_http_retry(monkeypatch):
    service = LLMService({})
    calls = []

    def fail_once(*args, **kwargs):
        calls.append((args, kwargs))
        raise httpx.TimeoutException("timeout")

    monkeypatch.setattr(service, "_post_json", fail_once)

    with pytest.raises(httpx.TimeoutException):
        service._chat_via_openai_compatible(
            api_base="http://127.0.0.1:28084/v1",
            model="test",
            messages=[{"role": "user", "content": "test"}],
            temperature=0.1,
            max_tokens=512,
            timeout=1,
            cfg={"implicit_retry_enabled": False},
            api_key="",
            extra_headers=None,
        )

    assert len(calls) == 1
