import unittest
from unittest.mock import patch

from app.core import secure_store


class FakeKeyring:
    def __init__(self):
        self.db = {}

    def set_password(self, service, name, value):
        self.db[(service, name)] = value

    def get_password(self, service, name):
        return self.db.get((service, name))

    def delete_password(self, service, name):
        self.db.pop((service, name), None)


class SecureStoreTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "secret_store": {
                "enabled": True,
                "backend": "keyring",
                "service_name": "ai-law-assistant",
                "llm_api_key_name": "llm_api_key",
            }
        }

    def test_set_get_has_delete(self):
        fake = FakeKeyring()
        with patch("app.core.secure_store.importlib.import_module", return_value=fake):
            ok = secure_store.set_llm_api_key(self.cfg, "sk-test-123")
            self.assertTrue(ok)
            self.assertEqual(secure_store.get_llm_api_key(
                self.cfg), "sk-test-123")
            self.assertTrue(secure_store.has_llm_api_key(self.cfg))
            ok2 = secure_store.delete_llm_api_key(self.cfg)
            self.assertTrue(ok2)
            self.assertEqual(secure_store.get_llm_api_key(self.cfg), "")
            self.assertFalse(secure_store.has_llm_api_key(self.cfg))

    def test_disabled_returns_empty(self):
        cfg = {"secret_store": {"enabled": False}}
        self.assertEqual(secure_store.get_llm_api_key(cfg), "")
        self.assertFalse(secure_store.set_llm_api_key(cfg, "x"))


if __name__ == "__main__":
    unittest.main()
