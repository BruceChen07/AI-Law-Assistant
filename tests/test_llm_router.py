import unittest

from app.core.llm_router import resolve_llm_route


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
                    "api_base": "http://127.0.0.1:8011/v1",
                    "model": "qwen3.6-27b-q4",
                    "headers": {"X-Model": "main"},
                },
                "small_model": {
                    "api_base": "http://127.0.0.1:8012/v1",
                    "model": "llama-3.2-3b-q4",
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
        self.assertEqual(cfg["model"], "qwen3.6-27b-q4")
        self.assertEqual(meta["selected_role"], "main")
        self.assertEqual(meta["selected_source"], "main_model")

    def test_small_task_routes_to_small_model(self):
        cfg, meta = resolve_llm_route(self.cfg, task_profile="tax_match_small")
        self.assertEqual(cfg["model"], "llama-3.2-3b-q4")
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


if __name__ == "__main__":
    unittest.main()
