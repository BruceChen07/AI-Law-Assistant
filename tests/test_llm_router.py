import unittest

from app.core.llm_router import resolve_llm_route


class LLMRouterTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "llm_config": {
                "provider": "openai_compatible",
                "api_base": "http://127.0.0.1:18081/v1",
                "api_key": "",
                "model": "qwen3-14b-instruct-awq",
                "temperature": 0.2,
                "max_tokens": 512,
                "timeout": 60,
                "headers": {"X-Base": "base"},
            },
            "local_llm": {
                "enabled": True,
                "routing_enabled": True,
                "allow_small_to_main_fallback": True,
                "timeout_sec": 30,
                "main_model": {
                    "provider": "openai_compatible",
                    "api_base": "http://127.0.0.1:18081/v1",
                    "model": "qwen3-14b-instruct-awq",
                    "headers": {"X-Model": "main"},
                },
                "small_model": {
                    "provider": "openai_compatible",
                    "api_base": "http://127.0.0.1:18082/v1",
                    "model": "qwen3-4b-instruct-awq",
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
        self.assertEqual(cfg["model"], "qwen3-14b-instruct-awq")
        self.assertEqual(cfg["provider"], "openai_compatible")
        self.assertEqual(meta["selected_role"], "main")
        self.assertEqual(meta["selected_source"], "main_model")

    def test_small_task_routes_to_small_model(self):
        cfg, meta = resolve_llm_route(self.cfg, task_profile="tax_match_small")
        self.assertEqual(cfg["model"], "qwen3-4b-instruct-awq")
        self.assertEqual(cfg["headers"]["X-Base"], "base")
        self.assertEqual(cfg["headers"]["X-Model"], "small")
        self.assertEqual(meta["selected_role"], "small")

    def test_missing_small_model_falls_back_to_main(self):
        self.cfg["local_llm"]["small_model"] = {}
        cfg, meta = resolve_llm_route(self.cfg, task_profile="tax_match_small")
        self.assertEqual(cfg["model"], "qwen3-14b-instruct-awq")
        self.assertEqual(meta["selected_role"], "main")
        self.assertEqual(meta["reason"], "small_missing_main_fallback")

    def test_explicit_base_role_overrides_local(self):
        cfg, meta = resolve_llm_route(
            self.cfg,
            task_profile="tax_match_small",
            model_role="base",
        )
        self.assertEqual(cfg["model"], "qwen3-14b-instruct-awq")
        self.assertEqual(meta["selected_role"], "base")
        self.assertEqual(meta["reason"], "preferred_role")


if __name__ == "__main__":
    unittest.main()
