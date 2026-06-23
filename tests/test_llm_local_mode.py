import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.core.llm import LLMService


class _FakeResponse:
    def __init__(self, content: str):
        self.choices = [SimpleNamespace(
            message=SimpleNamespace(content=content))]

    def model_dump(self):
        return {
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 24,
                "total_tokens": 36,
            }
        }


class LLMLocalModeTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "llm_config": {
                "provider": "openai_compatible",
                "api_base": "https://api.openai.com/v1",
                "api_key": "",
                "model": "gpt-4o-mini",
                "temperature": 0.2,
                "max_tokens": 256,
                "timeout": 10,
                "headers": {},
            },
            "local_llm": {
                "enabled": True,
                "routing_enabled": True,
                "cloud_fallback_enabled": True,
                "main_model": {
                    "provider": "openai_compatible",
                    "api_base": "http://127.0.0.1:8011/v1",
                    "api_key": "",
                    "model": "qwen3.6-27b-q4",
                    "timeout": 30,
                    "headers": {},
                },
                "small_model": {
                    "provider": "openai_compatible",
                    "api_base": "http://127.0.0.1:8012/v1",
                    "api_key": "",
                    "model": "llama-3.2-3b-q4",
                    "timeout": 20,
                    "headers": {},
                },
                "routing": {
                    "task_profiles": {
                        "tax_match_small": "small",
                    }
                },
            },
        }

    def test_chat_with_profile_uses_small_model(self):
        svc = LLMService(self.cfg)
        captured = {}

        def _fake_completion(_client, kwargs):
            captured.update(kwargs)
            return _FakeResponse('{"ok": true}')

        with patch("app.core.llm.OpenAI", return_value=object()):
            with patch.object(svc, "_create_chat_completion", side_effect=_fake_completion):
                content, raw = svc.chat_with_profile(
                    [{"role": "user", "content": "test"}],
                    "tax_match_small",
                )

        self.assertEqual(content, '{"ok": true}')
        self.assertEqual(captured["model"], "llama-3.2-3b-q4")
        self.assertEqual(raw["_route"]["selected_role"], "small")
        self.assertEqual(raw["_route"]["task_profile"], "tax_match_small")

    def test_chat_defaults_to_main_model_when_local_enabled(self):
        svc = LLMService(self.cfg)
        captured = {}

        def _fake_completion(_client, kwargs):
            captured.update(kwargs)
            return _FakeResponse('{"ok": true}')

        with patch("app.core.llm.OpenAI", return_value=object()):
            with patch.object(svc, "_create_chat_completion", side_effect=_fake_completion):
                _content, raw = svc.chat(
                    [{"role": "user", "content": "test"}],
                    overrides={"max_tokens": 123},
                )

        self.assertEqual(captured["model"], "qwen3.6-27b-q4")
        self.assertEqual(captured["max_tokens"], 123)
        self.assertEqual(raw["_route"]["selected_role"], "main")


if __name__ == "__main__":
    unittest.main()
