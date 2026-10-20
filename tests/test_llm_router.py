import unittest
import os
from contextlib import contextmanager

from app.core.llm_router import resolve_llm_route


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


class LLMRouterTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "llm_config": {
                "provider": "openai_compatible",
                "api_base": "https://api.openai.com/v1",
                "api_key": "",
                "model": "gpt-4o-mini",
                "temperature": 0.2,
                "max_tokens": 512,
                "timeout": 60,
                "headers": {"X-Base": "base"},
            },
            "local_llm": {
                "enabled": True,
                "routing_enabled": True,
                "cloud_fallback_enabled": True,
                "timeout_sec": 30,
                "main_model": {
                    "provider": "ollama",
                    "api_base": "http://127.0.0.1:11434/v1",
                    "model": "qwen3.6:27b",
                    "headers": {"X-Model": "main"},
                },
                "small_model": {
                    "provider": "ollama",
                    "api_base": "http://127.0.0.1:11434/v1",
                    "model": "llama3.2:3b",
                    "headers": {"X-Model": "small"},
                },
                "routing": {
                    "task_profiles": {
                        "tax_match_small": "small",
                        "entity_extract_small": "small",
                    }
                },
            },
        }

    def test_default_task_routes_to_main_model(self):
        cfg, meta = resolve_llm_route(self.cfg, task_profile="default")
        self.assertEqual(cfg["model"], "qwen3.6:27b")
        self.assertEqual(cfg["provider"], "ollama")
        self.assertEqual(meta["selected_role"], "main")
        self.assertEqual(meta["selected_source"], "main_model")
        self.assertEqual(meta["runtime_mode"], "local")

    def test_small_task_routes_to_small_model(self):
        cfg, meta = resolve_llm_route(self.cfg, task_profile="tax_match_small")
        self.assertEqual(cfg["model"], "llama3.2:3b")
        self.assertEqual(cfg["headers"]["X-Base"], "base")
        self.assertEqual(cfg["headers"]["X-Model"], "small")
        self.assertEqual(meta["selected_role"], "small")

    def test_missing_small_model_falls_back_to_cloud(self):
        self.cfg["local_llm"]["small_model"] = {}
        cfg, meta = resolve_llm_route(self.cfg, task_profile="tax_match_small")
        self.assertEqual(cfg["model"], "gpt-4o-mini")
        self.assertEqual(meta["selected_role"], "cloud_fallback")
        self.assertEqual(meta["reason"], "small_missing_fallback")

    def test_explicit_cloud_role_overrides_local(self):
        cfg, meta = resolve_llm_route(
            self.cfg,
            task_profile="tax_match_small",
            model_role="cloud_fallback",
        )
        self.assertEqual(cfg["model"], "gpt-4o-mini")
        self.assertEqual(meta["selected_role"], "cloud_fallback")
        self.assertEqual(meta["reason"], "preferred_role")

    def test_config_runtime_mode_cloud_switches_to_base_llm(self):
        self.cfg["local_llm"]["runtime_mode"] = "cloud"
        cfg, meta = resolve_llm_route(self.cfg, task_profile="default")
        self.assertEqual(cfg["model"], "gpt-4o-mini")
        self.assertEqual(cfg["provider"], "openai_compatible")
        self.assertEqual(meta["selected_role"], "cloud_fallback")
        self.assertEqual(meta["reason"], "runtime_mode_cloud")
        self.assertEqual(meta["runtime_mode_source"], "config")

    def test_env_runtime_mode_cloud_overrides_local_default(self):
        with temp_env({"LLM_RUNTIME_MODE": "cloud"}):
            cfg, meta = resolve_llm_route(self.cfg, task_profile="default")
        self.assertEqual(cfg["model"], "gpt-4o-mini")
        self.assertEqual(meta["selected_role"], "cloud_fallback")
        self.assertEqual(meta["runtime_mode"], "cloud")
        self.assertEqual(meta["runtime_mode_source"], "env")

    def test_env_runtime_mode_local_preserves_ollama_when_config_cloud(self):
        self.cfg["local_llm"]["runtime_mode"] = "cloud"
        with temp_env({"LLM_RUNTIME_MODE": "local"}):
            cfg, meta = resolve_llm_route(self.cfg, task_profile="default")
        self.assertEqual(cfg["model"], "qwen3.6:27b")
        self.assertEqual(cfg["provider"], "ollama")
        self.assertEqual(meta["selected_role"], "main")
        self.assertEqual(meta["runtime_mode"], "local")
        self.assertEqual(meta["runtime_mode_source"], "env")


if __name__ == "__main__":
    unittest.main()
