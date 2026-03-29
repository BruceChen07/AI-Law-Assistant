import os
import unittest
from unittest.mock import patch

from app.core.llm import LLMService


class LLMApiKeyResolutionTests(unittest.TestCase):
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
            "secret_store": {
                "enabled": True,
                "backend": "keyring",
                "service_name": "ai-law-assistant",
                "llm_api_key_name": "llm_api_key",
            },
        }

    def test_explicit_key_has_priority(self):
        svc = LLMService(self.cfg)
        with patch("app.core.llm.get_llm_api_key", return_value="sk-secure"):
            self.assertEqual(svc._resolve_api_key(
                {"api_key": "sk-explicit"}), "sk-explicit")

    def test_secure_store_used_when_explicit_missing(self):
        svc = LLMService(self.cfg)
        with patch("app.core.llm.get_llm_api_key", return_value="sk-secure"):
            self.assertEqual(svc._resolve_api_key(
                {"api_key": ""}), "sk-secure")

    def test_env_fallback_when_secure_empty(self):
        svc = LLMService(self.cfg)
        old = os.environ.get("LLM_API_KEY")
        os.environ["LLM_API_KEY"] = "sk-env"
        try:
            with patch("app.core.llm.get_llm_api_key", return_value=""):
                self.assertEqual(svc._resolve_api_key(
                    {"api_key": ""}), "sk-env")
        finally:
            if old is None:
                os.environ.pop("LLM_API_KEY", None)
            else:
                os.environ["LLM_API_KEY"] = old


if __name__ == "__main__":
    unittest.main()
