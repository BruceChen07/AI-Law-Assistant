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
            "LLM_API_BASE": None,
            "LLM_MODEL": None,
            "LLM_API_KEY": None,
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

    def test_deepseek_provider_uses_deepseek_vendor_env(self):
        with temp_env({
            "LLM_PROVIDER": "deepseek",
            "LLM_API_BASE": None,
            "LLM_MODEL": None,
            "LLM_API_KEY": None,
            "DEEPSEEK_API_KEY": "sk-deepseek-test",
            "DEEPSEEK_MODEL": "deepseek-v4-pro",
            "DEEPSEEK_API_BASE": "https://api.deepseek.com",
        }):
            resolved, meta = resolve_runtime_llm_config(self.cfg["llm_config"])
        self.assertEqual(resolved["provider"], "deepseek")
        self.assertEqual(resolved["model"], "deepseek-v4-pro")
        self.assertEqual(resolved["api_base"], "https://api.deepseek.com")
        self.assertEqual(resolved["api_key"], "sk-deepseek-test")
        self.assertEqual(meta["env_sources"]["provider"], "LLM_PROVIDER")
        self.assertEqual(meta["env_sources"]["api_key"], "DEEPSEEK_API_KEY")

    def test_deepseek_env_can_infer_provider_without_llm_provider(self):
        with temp_env({
            "LLM_PROVIDER": None,
            "LLM_API_BASE": None,
            "LLM_MODEL": None,
            "LLM_API_KEY": None,
            "DEEPSEEK_API_KEY": "sk-deepseek-test",
            "DEEPSEEK_MODEL": "deepseek-v4-pro",
            "DEEPSEEK_API_BASE": "https://api.deepseek.com",
        }):
            resolved, meta = resolve_runtime_llm_config(self.cfg["llm_config"])
        self.assertEqual(resolved["provider"], "deepseek")
        self.assertEqual(meta["env_sources"]["provider"], "DEEPSEEK_*")

    def test_qwen_provider_uses_dashscope_api_key(self):
        with temp_env({
            "LLM_PROVIDER": "qwen",
            "LLM_API_BASE": None,
            "LLM_MODEL": None,
            "LLM_API_KEY": None,
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
            "LLM_API_BASE": None,
            "LLM_MODEL": None,
            "LLM_API_KEY": None,
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
            _resolved, meta = resolve_runtime_llm_config(
                self.cfg["llm_config"])
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

    def test_local_route_keeps_local_target_when_env_points_to_cloud(self):
        cfg = {
            "llm_config": {
                "provider": "openai_compatible",
                "api_base": "https://api.openai.com/v1",
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
                    "provider": "ollama",
                    "api_base": "http://127.0.0.1:11434/v1",
                    "model": "qwen3.5:9b",
                    "timeout": 30,
                    "headers": {},
                },
            },
        }
        svc = LLMService(cfg)
        with temp_env({
            "LLM_PROVIDER": "openai",
            "LLM_API_BASE": "https://api.deepseek.com",
            "LLM_MODEL": "deepseek-v4-pro",
            "LLM_API_KEY": "deepseek-key",
            "OPENAI_BASE_URL": None,
            "OPENAI_MODEL": None,
            "OPENAI_API_KEY": None,
        }):
            runtime = svc.describe_runtime_config()
        self.assertEqual(runtime["provider"], "ollama")
        self.assertEqual(runtime["api_base"], "http://127.0.0.1:11434/v1")
        self.assertEqual(runtime["model"], "qwen3.5:9b")
        self.assertTrue(runtime["route"]["runtime_target_locked"])

    def test_explicit_cloud_role_still_allows_env_managed_cloud_target(self):
        cfg = {
            "llm_config": {
                "provider": "openai_compatible",
                "api_base": "https://api.openai.com/v1",
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
                    "provider": "ollama",
                    "api_base": "http://127.0.0.1:11434/v1",
                    "model": "qwen3.5:9b",
                    "timeout": 30,
                    "headers": {},
                },
            },
        }
        svc = LLMService(cfg)
        with temp_env({
            "LLM_PROVIDER": "openai",
            "LLM_API_BASE": "https://api.deepseek.com",
            "LLM_MODEL": "deepseek-v4-pro",
            "LLM_API_KEY": "deepseek-key",
            "OPENAI_BASE_URL": None,
            "OPENAI_MODEL": None,
            "OPENAI_API_KEY": None,
        }):
            runtime = svc.describe_runtime_config(
                overrides={"model_role": "cloud_fallback"}
            )
        self.assertEqual(runtime["provider"], "openai")
        self.assertEqual(runtime["api_base"], "https://api.deepseek.com")
        self.assertEqual(runtime["model"], "deepseek-v4-pro")
        self.assertEqual(runtime["route"]["selected_role"], "cloud_fallback")

    def test_contract_audit_can_force_env_cloud_when_enabled(self):
        cfg = {
            "llm_config": {
                "provider": "ollama",
                "api_base": "http://127.0.0.1:11434/v1",
                "model": "qwen3.5:9b",
                "temperature": 0.2,
                "max_tokens": 256,
                "timeout": 10,
                "headers": {},
            },
            "local_llm": {
                "enabled": True,
                "routing_enabled": True,
                "cloud_fallback_enabled": False,
                "main_model": {
                    "provider": "ollama",
                    "api_base": "http://127.0.0.1:11434/v1",
                    "model": "qwen3.5:9b",
                    "timeout": 30,
                    "headers": {},
                },
                "routing": {
                    "contract_audit_force_env_cloud": True,
                },
            },
        }
        svc = LLMService(cfg)
        with temp_env({
            "LLM_PROVIDER": None,
            "LLM_API_BASE": "https://api.deepseek.com",
            "LLM_MODEL": "deepseek-v4-pro",
            "LLM_API_KEY": "deepseek-key",
            "DEEPSEEK_API_KEY": "deepseek-key",
            "DEEPSEEK_MODEL": "deepseek-v4-pro",
            "DEEPSEEK_API_BASE": "https://api.deepseek.com",
        }):
            runtime = svc.describe_runtime_config(
                overrides={"task_profile": "contract_audit_main"}
            )
        self.assertEqual(runtime["provider"], "deepseek")
        self.assertEqual(runtime["api_base"], "https://api.deepseek.com")
        self.assertEqual(runtime["model"], "deepseek-v4-pro")
        self.assertEqual(runtime["route"]["selected_role"], "cloud_fallback")
        self.assertEqual(
            runtime["route"]["reason"], "contract_audit_env_cloud_override")
        self.assertFalse(runtime["route"]["runtime_target_locked"])

    def test_runtime_mode_cloud_uses_deepseek_profile_without_touching_ollama(self):
        cfg = {
            "llm_config": {
                "provider": "deepseek",
                "api_base": "https://api.deepseek.com/v1",
                "model": "deepseek-chat",
                "temperature": 0.2,
                "max_tokens": 256,
                "timeout": 10,
                "headers": {},
            },
            "local_llm": {
                "enabled": True,
                "runtime_mode": "cloud",
                "routing_enabled": True,
                "cloud_fallback_enabled": False,
                "main_model": {
                    "provider": "ollama",
                    "api_base": "http://127.0.0.1:11434/v1",
                    "model": "qwen3.5:9b",
                    "timeout": 30,
                    "headers": {},
                },
            },
        }
        svc = LLMService(cfg)
        with temp_env({
            "LLM_PROVIDER": None,
            "LLM_API_BASE": None,
            "LLM_MODEL": None,
            "LLM_API_KEY": None,
            "DEEPSEEK_API_KEY": "deepseek-key",
            "DEEPSEEK_MODEL": "deepseek-chat",
            "DEEPSEEK_API_BASE": "https://api.deepseek.com/v1",
        }):
            runtime = svc.describe_runtime_config(
                overrides={"task_profile": "contract_audit_main"}
            )
        self.assertEqual(runtime["provider"], "deepseek")
        self.assertEqual(runtime["api_base"], "https://api.deepseek.com/v1")
        self.assertEqual(runtime["model"], "deepseek-chat")
        self.assertEqual(runtime["route"]["selected_role"], "cloud_fallback")
        self.assertEqual(runtime["route"]["reason"], "runtime_mode_cloud")
        self.assertFalse(runtime["route"]["runtime_target_locked"])

    def test_contract_audit_cloud_runtime_raises_timeout_floor(self):
        with temp_env({
            "LLM_PROVIDER": None,
            "LLM_API_BASE": None,
            "LLM_MODEL": None,
            "LLM_TIMEOUT": None,
        }):
            resolved, _meta = resolve_runtime_llm_config({
                "provider": "openai",
                "api_base": "https://api.deepseek.com",
                "model": "deepseek-v4-pro",
                "timeout": 30,
                "_runtime_task_profile": "contract_audit_main",
                "_prefer_long_timeout": True,
            })
        self.assertEqual(resolved["timeout"], 90)
        self.assertEqual(resolved["timeout_retry"], 480)


if __name__ == "__main__":
    unittest.main()
