from app.core.ocr import OCREngineManager, OCREngine, benchmark_engines, detect_dependencies
import app.core.mineru_ocr as mineru_ocr


class StubEngine(OCREngine):
    def __init__(self, name, available=True, text="ok", pages=1):
        self.name = name
        self.available = available
        self.text = text
        self.pages = pages

    def is_available(self) -> bool:
        return self.available

    def ocr_pdf(self, path: str, lang: str, dpi: int):
        return self.text, self.pages


def test_select_engine_order():
    cfg = {"ocr_engine": "auto", "ocr_engine_order": ["a", "b"]}
    engines = [StubEngine("a", available=False),
               StubEngine("b", available=True)]
    manager = OCREngineManager(cfg, engines=engines)
    assert manager.select_engine("pdf") == "b"


def test_select_engine_by_type():
    cfg = {"ocr_engine_by_type": {"pdf": "a"}}
    engines = [StubEngine("a", available=True)]
    manager = OCREngineManager(cfg, engines=engines)
    assert manager.select_engine("pdf") == "a"


def test_benchmark_engines():
    cfg = {"ocr_languages": "chi_sim+eng", "ocr_dpi": 200}
    engines = [StubEngine("a", available=True, text="hello", pages=2)]
    results = benchmark_engines(cfg, "dummy.pdf", engines=engines)
    assert results[0]["engine"] == "a"
    assert results[0]["available"] is True
    assert results[0]["text_length"] == 5


def test_detect_dependencies_shape():
    info = detect_dependencies(cmd_runner=lambda cmd: "v1")
    assert "tesseract" in info
    assert "poppler" in info


def test_probe_mineru_capabilities(monkeypatch):
    mineru_ocr._CAPABILITIES_CACHE["ts"] = 0
    mineru_ocr._CAPABILITIES_CACHE["data"] = None
    monkeypatch.setattr(mineru_ocr, "_detect_gpu", lambda: True)
    monkeypatch.setattr(mineru_ocr, "_probe_supported_backends", lambda: ["pipeline", "hybrid-auto-engine"])
    monkeypatch.setattr(mineru_ocr.shutil, "which", lambda name: "C:/bin/mineru.exe" if name == "mineru" else None)
    data = mineru_ocr.probe_mineru_capabilities(force_refresh=True, cache_ttl_sec=300)
    assert data["mineru_cli_found"] is True
    assert data["has_gpu"] is True
    assert "pipeline" in data["supported_backends"]


def test_probe_mineru_capabilities_cache(monkeypatch):
    mineru_ocr._CAPABILITIES_CACHE["ts"] = 0
    mineru_ocr._CAPABILITIES_CACHE["data"] = None
    monkeypatch.setattr(mineru_ocr, "_detect_gpu", lambda: False)
    monkeypatch.setattr(mineru_ocr, "_probe_supported_backends", lambda: ["pipeline"])
    monkeypatch.setattr(mineru_ocr.shutil, "which", lambda name: "C:/bin/mineru.exe" if name == "mineru" else None)
    first = mineru_ocr.probe_mineru_capabilities(force_refresh=True, cache_ttl_sec=300)
    monkeypatch.setattr(mineru_ocr, "_detect_gpu", lambda: True)
    monkeypatch.setattr(mineru_ocr, "_probe_supported_backends", lambda: ["hybrid-auto-engine"])
    second = mineru_ocr.probe_mineru_capabilities(force_refresh=False, cache_ttl_sec=300)
    assert second["has_gpu"] == first["has_gpu"]
    assert second["supported_backends"] == first["supported_backends"]


def test_resolve_mineru_backend_auto_gpu_hybrid():
    b = mineru_ocr.resolve_mineru_backend(
        {"mode": "auto"},
        {"has_gpu": True, "supported_backends": ["hybrid-auto-engine", "pipeline"]},
    )
    assert b == "hybrid-auto-engine"


def test_resolve_mineru_backend_auto_cpu_pipeline():
    b = mineru_ocr.resolve_mineru_backend(
        {"mode": "auto"},
        {"has_gpu": False, "supported_backends": ["pipeline"]},
    )
    assert b == "pipeline"


def test_resolve_mineru_backend_force_pipeline():
    b = mineru_ocr.resolve_mineru_backend(
        {"mode": "force_pipeline"},
        {"has_gpu": True, "supported_backends": ["hybrid-auto-engine", "pipeline"]},
    )
    assert b == "pipeline"


def test_resolve_mineru_backend_fallback_when_hybrid_not_supported():
    b = mineru_ocr.resolve_mineru_backend(
        {"mode": "auto"},
        {"has_gpu": True, "supported_backends": ["pipeline"]},
    )
    assert b == "pipeline"


def test_build_backend_attempts_default():
    attempts = mineru_ocr.build_backend_attempts(
        {},
        {"supported_backends": ["hybrid-auto-engine", "pipeline"]},
        "hybrid-auto-engine",
    )
    assert attempts == ["hybrid-auto-engine", "pipeline"]


def test_build_backend_attempts_supported_filter():
    attempts = mineru_ocr.build_backend_attempts(
        {"fallback_backends": ["pipeline", "vlm-transformers"]},
        {"supported_backends": ["pipeline"]},
        "hybrid-auto-engine",
    )
    assert attempts == ["pipeline"]
