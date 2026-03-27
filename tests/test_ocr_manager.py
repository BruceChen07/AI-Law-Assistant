from app.core.ocr import OCREngineManager, OCREngine, benchmark_engines, detect_dependencies


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
