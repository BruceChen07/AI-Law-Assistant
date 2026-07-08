from app.core.translation import TranslationService


def test_translation_service_mock_backend_and_cache():
    cfg = {
        "translation_config": {
            "enabled": True,
            "backend": "mock",
            "mode": "dual",
            "source_langs": ["en"],
            "target_lang": "zh",
            "cache_size": 4,
            "glossary": {
                "VAT": "增值税"
            }
        }
    }
    svc = TranslationService(cfg)
    first = svc.translate_query("VAT invoice should be issued", "en", "zh")
    second = svc.translate_query("VAT invoice should be issued", "en", "zh")
    assert first["ok"] is True
    assert "增值税" in first["text"]
    assert second["used_cache"] is True


def test_translation_service_disabled():
    cfg = {
        "translation_config": {
            "enabled": False,
            "backend": "mock",
        }
    }
    svc = TranslationService(cfg)
    out = svc.translate_query("abc", "en", "zh")
    assert out["ok"] is False
    assert out["text"] == "abc"
