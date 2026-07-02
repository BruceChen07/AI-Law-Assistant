import unittest
from unittest.mock import patch

import httpx

from app.core.llm import LLMService


class LLMLocalModeTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "llm_config": {
                "provider": "openai_compatible",
                "api_base": "http://127.0.0.1:18081/v1",
                "api_key": "",
                "model": "qwen3-14b-instruct-awq",
                "temperature": 0.2,
                "max_tokens": 256,
                "timeout": 10,
                "headers": {},
            },
            "local_llm": {
                "enabled": True,
                "routing_enabled": True,
                "allow_small_to_main_fallback": True,
                "main_model": {
                    "provider": "ollama",
                    "api_base": "http://127.0.0.1:11434/v1",
                    "api_key": "",
                    "model": "qwen3.6:27b",
                    "timeout": 30,
                    "headers": {},
                },
                "small_model": {
                    "provider": "ollama",
                    "api_base": "http://127.0.0.1:11434/v1",
                    "api_key": "",
                    "model": "llama3.2:3b",
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

        def _fake_ollama_post(url, body, headers, timeout):
            captured.update({
                "url": url,
                "body": body,
                "headers": headers,
                "timeout": timeout,
            })
            return {
                "model": body["model"],
                "done": True,
                "message": {"role": "assistant", "content": '{"ok": true}'},
                "prompt_eval_count": 12,
                "eval_count": 24,
            }

        with patch.object(svc, "_post_ollama_chat", side_effect=_fake_ollama_post):
            content, raw = svc.chat_with_profile(
                [{"role": "user", "content": "test"}],
                "tax_match_small",
            )

        self.assertEqual(content, '{"ok": true}')
        self.assertEqual(captured["url"], "http://127.0.0.1:11434/api/chat")
        self.assertEqual(captured["body"]["model"], "llama3.2:3b")
        self.assertEqual(raw["_route"]["selected_role"], "small")
        self.assertEqual(raw["_route"]["task_profile"], "tax_match_small")

    def test_chat_defaults_to_main_model_when_local_enabled(self):
        svc = LLMService(self.cfg)
        captured = {}

        def _fake_ollama_post(url, body, headers, timeout):
            captured.update({
                "url": url,
                "body": body,
                "headers": headers,
                "timeout": timeout,
            })
            return {
                "model": body["model"],
                "done": True,
                "message": {"role": "assistant", "content": '{"ok": true}'},
                "prompt_eval_count": 12,
                "eval_count": 24,
            }

        with patch.object(svc, "_post_ollama_chat", side_effect=_fake_ollama_post):
            _content, raw = svc.chat(
                [{"role": "user", "content": "test"}],
                overrides={"max_tokens": 123},
            )

        self.assertEqual(captured["url"], "http://127.0.0.1:11434/api/chat")
        self.assertEqual(captured["body"]["model"], "qwen3.6:27b")
        self.assertEqual(captured["body"]["options"]["num_predict"], 123)
        self.assertEqual(raw["_route"]["selected_role"], "main")

    def test_chat_retries_once_on_ollama_read_error(self):
        svc = LLMService(self.cfg)
        calls = {"count": 0}

        def _fake_ollama_post(url, body, headers, timeout):
            calls["count"] += 1
            if calls["count"] == 1:
                raise httpx.ReadError("[WinError 10054] connection reset")
            return {
                "model": body["model"],
                "done": True,
                "message": {"role": "assistant", "content": "pong"},
                "prompt_eval_count": 12,
                "eval_count": 24,
            }

        with patch.object(svc, "_post_ollama_chat", side_effect=_fake_ollama_post):
            content, raw = svc.chat([{"role": "user", "content": "test"}])

        self.assertEqual(content, "pong")
        self.assertEqual(raw["model"], "qwen3.6:27b")
        self.assertEqual(calls["count"], 2)

    def test_chat_maps_ollama_model_not_found(self):
        svc = LLMService(self.cfg)
        request = httpx.Request("POST", "http://127.0.0.1:11434/api/chat")
        response = httpx.Response(
            404,
            request=request,
            json={"error": "model 'qwen3.6:27b' not found"},
        )

        with patch.object(
            svc,
            "_post_ollama_chat",
            side_effect=httpx.HTTPStatusError(
                "Client error '404 Not Found'",
                request=request,
                response=response,
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "ollama model not found: qwen3.6:27b"):
                svc.chat([{"role": "user", "content": "test"}])


if __name__ == "__main__":
    unittest.main()
