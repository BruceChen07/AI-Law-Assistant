import os
import unittest
from contextlib import contextmanager

from app.core.llm import LLMService
from app.core.llm_env import collect_persisted_secret_issues, resolve_runtime_llm_config


@contextmanager
def temp_env(mapping):
    old = {}
    missing = object()
    for key, value in mapping.items():
        old[key] = os.environ.get(key, missing)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    try:
        yield
    finally:
        for key, value in old.items():
            if value is missing:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class LLMEnvResolutionTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "llm_config": {
                "provider": "openai_compatible",
                "api_base": "http://127.0.0.1:11434/v1",
                "model": "qwen3.6:27b",
                "temperature": 0.2,
                "max_tokens": 256,
                "timeout": 10,
                "headers": {},
            }
        }

    def test_openai_provider_uses_vendor_env(self):
        with temp_env({
            "LLM_PROVIDER": "openai",
            "OPENAI_API_KEY": "sk-openai-test",
            "OPENAI_MODEL": "gpt-4o-mini",
            "OPENAI_BASE_URL": "https://api.openai.com/v1",
        }):
            resolved, meta = resolve_runtime_llm_config(self.cfg["llm_config"])
        self.assertEqual(resolved["provider"], "openai")
        self.assertEqual(resolved["model"], "gpt-4o-mini")
        self.assertEqual(resolved["api_key"], "sk-openai-test")
        self.assertIn("OPENAI_API_KEY", meta["required_env_vars"])
        self.assertEqual(meta["env_sources"]["provider"], "LLM_PROVIDER")

    def test_qwen_provider_uses_dashscope_api_key(self):
        with temp_env({
            "LLM_PROVIDER": "qwen",
            "DASHSCOPE_API_KEY": "dashscope-test-key",
            "DASHSCOPE_MODEL": "qwen-max",
        }):
            resolved, meta = resolve_runtime_llm_config(self.cfg["llm_config"])
        self.assertEqual(resolved["provider"], "qwen")
        self.assertEqual(resolved["model"], "qwen-max")
        self.assertEqual(resolved["api_key"], "dashscope-test-key")
        self.assertEqual(meta["env_sources"]["api_key"], "DASHSCOPE_API_KEY")

    def test_wenxin_provider_accepts_access_key_and_secret_key(self):
        with temp_env({
            "LLM_PROVIDER": "wenxin",
            "QIANFAN_ACCESS_KEY": "ak-test",
            "QIANFAN_SECRET_KEY": "sk-test",
            "QIANFAN_MODEL": "ernie-4.0-turbo",
        }):
            resolved, meta = resolve_runtime_llm_config(self.cfg["llm_config"])
        self.assertEqual(resolved["provider"], "wenxin")
        self.assertEqual(resolved["access_key"], "ak-test")
        self.assertEqual(resolved["secret_key"], "sk-test")
        self.assertEqual(meta["missing_env_vars"], [])

    def test_missing_openai_key_is_reported(self):
        with temp_env({
            "LLM_PROVIDER": "openai",
            "OPENAI_API_KEY": None,
            "LLM_API_KEY": None,
            "OPENAI_MODEL": "gpt-4o-mini",
        }):
            _resolved, meta = resolve_runtime_llm_config(self.cfg["llm_config"])
        self.assertIn("OPENAI_API_KEY", meta["missing_env_vars"])

    def test_persisted_secret_issue_is_detected(self):
        cfg = {
            "llm_config": {"api_key": "plain-secret"},
            "local_llm": {"main_model": {"api_key": ""}, "small_model": {"api_key": ""}},
        }
        issues = collect_persisted_secret_issues(cfg)
        self.assertEqual(len(issues), 1)
        self.assertIn("llm_config.api_key", issues[0])

    def test_llm_service_runtime_description_contains_missing_envs(self):
        svc = LLMService(self.cfg)
        with temp_env({
            "LLM_PROVIDER": "openai",
            "OPENAI_API_KEY": None,
            "LLM_API_KEY": None,
            "OPENAI_MODEL": "gpt-4o-mini",
        }):
            runtime = svc.describe_runtime_config()
        self.assertEqual(runtime["provider"], "openai")
        self.assertIn("OPENAI_API_KEY", runtime["missing_env_vars"])


if __name__ == "__main__":
    unittest.main()

