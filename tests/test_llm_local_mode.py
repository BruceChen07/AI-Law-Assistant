import unittest
from unittest.mock import patch

import httpx

from app.core.llm import LLMService


MAIN_MODEL = "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
SMALL_MODEL = "Llama-3.2-3B-Instruct-Q4_K_M.gguf"


def _ok_payload(model: str, content: str = '{"ok": true}') -> dict:
    return {
        "model": model,
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": content,
                }
            }
        ],
        "usage": {
            "prompt_tokens": 12,
            "completion_tokens": 24,
            "total_tokens": 36,
        },
    }


class LLMLocalModeTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "llm_config": {
                "provider": "llama_cpp",
                "api_base": "http://127.0.0.1:18080/v1",
                "api_key": "",
                "model": MAIN_MODEL,
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
                    "provider": "llama_cpp",
                    "api_base": "http://127.0.0.1:18080/v1",
                    "api_key": "",
                    "model": MAIN_MODEL,
                    "timeout": 30,
                    "headers": {},
                },
                "small_model": {
                    "provider": "llama_cpp",
                    "api_base": "http://127.0.0.1:18081/v1",
                    "api_key": "",
                    "model": SMALL_MODEL,
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

        def _fake_post_json(url, body, headers, timeout):
            captured.update({
                "url": url,
                "body": body,
                "headers": headers,
                "timeout": timeout,
            })
            return _ok_payload(body["model"])

        with patch.object(svc, "_post_json", side_effect=_fake_post_json):
            content, raw = svc.chat_with_profile(
                [{"role": "user", "content": "test"}],
                "tax_match_small",
            )

        self.assertEqual(content, '{"ok": true}')
        self.assertEqual(
            captured["url"], "http://127.0.0.1:18081/v1/chat/completions")
        self.assertEqual(captured["body"]["model"], SMALL_MODEL)
        self.assertEqual(raw["_route"]["selected_role"], "small")
        self.assertEqual(raw["_route"]["task_profile"], "tax_match_small")

    def test_chat_defaults_to_main_model_when_local_enabled(self):
        svc = LLMService(self.cfg)
        captured = {}

        def _fake_post_json(url, body, headers, timeout):
            captured.update({
                "url": url,
                "body": body,
                "headers": headers,
                "timeout": timeout,
            })
            return _ok_payload(body["model"])

        with patch.object(svc, "_post_json", side_effect=_fake_post_json):
            _content, raw = svc.chat(
                [{"role": "user", "content": "test"}],
                overrides={"max_tokens": 123},
            )

        self.assertEqual(
            captured["url"], "http://127.0.0.1:18080/v1/chat/completions")
        self.assertEqual(captured["body"]["model"], MAIN_MODEL)
        self.assertEqual(captured["body"]["max_tokens"], 123)
        self.assertEqual(raw["_route"]["selected_role"], "main")

    def test_chat_retries_once_on_llamacpp_timeout(self):
        svc = LLMService(self.cfg)
        calls = {"count": 0, "max_tokens": []}

        def _fake_post_json(_url, body, _headers, _timeout):
            calls["count"] += 1
            calls["max_tokens"].append(body["max_tokens"])
            if calls["count"] == 1:
                raise httpx.ReadTimeout("timed out")
            return _ok_payload(body["model"], "pong")

        with patch.object(svc, "_post_json", side_effect=_fake_post_json):
            content, raw = svc.chat([{"role": "user", "content": "test"}])

        self.assertEqual(content, "pong")
        self.assertEqual(raw["model"], MAIN_MODEL)
        self.assertEqual(calls["count"], 2)
        self.assertEqual(calls["max_tokens"][0], 256)
        self.assertEqual(calls["max_tokens"][1], 220)

    def test_chat_surfaces_llamacpp_model_not_found(self):
        svc = LLMService(self.cfg)
        request = httpx.Request(
            "POST", "http://127.0.0.1:18080/v1/chat/completions")
        response = httpx.Response(
            404,
            request=request,
            json={"error": f"model '{MAIN_MODEL}' not found"},
        )

        with patch.object(
            svc,
            "_post_json",
            side_effect=httpx.HTTPStatusError(
                "Client error '404 Not Found'",
                request=request,
                response=response,
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "llm request failed"):
                svc.chat([{"role": "user", "content": "test"}])

    def test_chat_uses_llamacpp_main_model_via_openai_compatible_route(self):
        svc = LLMService(self.cfg)
        captured = {}

        def _fake_post_json(url, body, headers, timeout):
            captured.update({
                "url": url,
                "body": body,
                "headers": headers,
                "timeout": timeout,
            })
            return _ok_payload(body["model"])

        with patch.object(svc, "_post_json", side_effect=_fake_post_json):
            content, raw = svc.chat([{"role": "user", "content": "test"}])

        self.assertEqual(content, '{"ok": true}')
        self.assertEqual(
            captured["url"], "http://127.0.0.1:18080/v1/chat/completions")
        self.assertEqual(captured["body"]["model"], MAIN_MODEL)
        self.assertEqual(captured["timeout"], 30)
        self.assertNotIn("Authorization", captured["headers"])
        self.assertEqual(raw["_route"]["selected_role"], "main")

    def test_chat_uses_reasoning_content_fallback_for_llamacpp_when_content_is_empty(self):
        svc = LLMService(self.cfg)

        def _fake_post_json(_url, _body, _headers, _timeout):
            payload = _ok_payload(MAIN_MODEL, "")
            payload["choices"][0]["message"]["reasoning_content"] = "主模型联调成功"
            return payload

        with patch.object(svc, "_post_json", side_effect=_fake_post_json):
            content, raw = svc.chat([{"role": "user", "content": "test"}])

        self.assertEqual(content, "主模型联调成功")
        self.assertTrue(raw["_used_reasoning_content_fallback"])
        self.assertEqual(
            raw["choices"][0]["message"]["reasoning_content"],
            "主模型联调成功",
        )
        self.assertEqual(
            raw["choices"][0]["message"]["content"],
            "主模型联调成功",
        )


if __name__ == "__main__":
    unittest.main()
