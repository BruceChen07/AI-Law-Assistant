"""llama.cpp 请求体 prompt 缓存开关的单元测试。"""

from app.local_llm.llama_cpp import LlamaCppProvider


class _StubService:
    def __init__(self) -> None:
        self.bodies = []

    def _build_base_url(self, api_base: str) -> str:
        return str(api_base).rstrip("/")

    def _clean_text(self, value) -> str:
        return str(value or "").strip()

    def _build_openai_compatible_body(self, model, messages, temperature, max_tokens, cfg):
        return {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

    def _build_headers(self, _extra, _headers):
        return {}

    def _post_json(self, url, body, headers, timeout):
        self.bodies.append(body)
        return {"choices": [{"message": {"content": "{}"}}]}


def _llamacpp_cfg(**extra):
    cfg = {
        "provider": "llama_cpp",
        "api_base": "http://127.0.0.1:18080/v1",
        "model": "Qwen3.6-35B-A3B-UD-Q4_K_M",
        "temperature": 0.2,
        "max_tokens": 64,
        "timeout": 30,
    }
    cfg.update(extra)
    return cfg


def test_llamacpp_request_enables_prompt_cache():
    service = _StubService()

    LlamaCppProvider().chat(service, [{"role": "user", "content": "hi"}],
                            _llamacpp_cfg())

    assert service.bodies[0]["cache_prompt"] is True


def test_llamacpp_request_can_disable_prompt_cache():
    service = _StubService()

    LlamaCppProvider().chat(service, [{"role": "user", "content": "hi"}],
                            _llamacpp_cfg(cache_prompt=False))

    assert service.bodies[0]["cache_prompt"] is False
