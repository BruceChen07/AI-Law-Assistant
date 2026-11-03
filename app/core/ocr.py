import importlib
import shutil
import subprocess
import time
import logging
from typing import Dict, Any, Tuple, Optional, List

logger = logging.getLogger("law_assistant")


class OCREngine:
    name = ""

    def is_available(self) -> bool:
        raise NotImplementedError()

    def ocr_document(self, path: str, lang: str, dpi: int, doc_type: str = "pdf") -> Tuple[str, int]:
        raise NotImplementedError()

    def ocr_pdf(self, path: str, lang: str, dpi: int) -> Tuple[str, int]:
        return self.ocr_document(path, lang, dpi, doc_type="pdf")

class PluginEngine(OCREngine):
    def __init__(self, name: str, module_name: str, func_name: str):
        self.name = name
        self.module_name = module_name
        self.func_name = func_name

    def _load(self):
        mod = importlib.import_module(self.module_name)
        fn = getattr(mod, self.func_name)
        return fn

    def is_available(self) -> bool:
        try:
            fn = self._load()
            return callable(fn)
        except Exception:
            return False

    def ocr_document(self, path: str, lang: str, dpi: int, doc_type: str = "pdf") -> Tuple[str, int]:
        fn = self._load()
        try:
            result = fn(path=path, lang=lang, dpi=dpi, doc_type=doc_type)
        except TypeError:
            try:
                result = fn(path=path, lang=lang, dpi=dpi)
            except TypeError:
                result = fn(path, lang, dpi)
        if isinstance(result, tuple) and len(result) >= 2:
            return str(result[0]), int(result[1])
        return str(result), 0


class OCREngineManager:
    def __init__(self, cfg: Dict[str, Any], engines: Optional[List[OCREngine]] = None):
        self.cfg = cfg or {}
        if engines is not None:
            self.engines = engines
        else:
            self.engines = build_engines_from_config(self.cfg)

    def list_engines(self) -> List[str]:
        return [e.name for e in self.engines]

    def available_engines(self) -> List[str]:
        return [e.name for e in self.engines if e.is_available()]

    def get_engine(self, name: str) -> Optional[OCREngine]:
        for e in self.engines:
            if e.name == name:
                return e
        return None

    def select_engine(self, doc_type: str) -> Optional[str]:
        doc_type = (doc_type or "").lower()
        by_type = self.cfg.get("ocr_engine_by_type") or {}
        if isinstance(by_type, dict) and doc_type in by_type:
            preferred = by_type.get(doc_type)
            if preferred in self.available_engines():
                return preferred
        preferred = self.cfg.get("ocr_engine")
        if preferred and preferred != "auto":
            return preferred if preferred in self.available_engines() else None
        order = self.cfg.get("ocr_engine_order") or ["tesseract", "mineru"]
        for name in order:
            if name in self.available_engines():
                return name
        return None

    def ocr_document(self, path: str, lang: str, dpi: int, doc_type: str = "pdf") -> Tuple[str, int, Optional[str]]:
        name = self.select_engine(doc_type)
        if not name:
            logger.info("ocr_engine_missing file=%s doc_type=%s",
                        path, doc_type)
            return "", 0, None
        engine = self.get_engine(name)
        if not engine:
            logger.info("ocr_engine_not_found file=%s engine=%s", path, name)
            return "", 0, None
        logger.info(
            "ocr_engine_start file=%s engine=%s lang=%s dpi=%s", path, name, lang, dpi)
        start = time.perf_counter()
        text, pages = engine.ocr_document(path, lang, dpi, doc_type=doc_type)
        elapsed = int((time.perf_counter() - start) * 1000)
        logger.info("ocr_engine_done file=%s engine=%s pages=%s text_length=%s cost_ms=%s",
                    path, name, pages, len(text), elapsed)
        return text, pages, name

    def ocr_pdf(self, path: str, lang: str, dpi: int, doc_type: str = "pdf") -> Tuple[str, int, Optional[str]]:
        return self.ocr_document(path, lang, dpi, doc_type=doc_type)


def build_engines_from_config(cfg: Dict[str, Any]) -> List[OCREngine]:
    engines: List[OCREngine] = []
    engine_cfg = cfg.get("ocr_engines") or {}
    if isinstance(engine_cfg, dict):
        for name, detail in engine_cfg.items():
            if not isinstance(detail, dict):
                continue
            module_name = str(detail.get("module", "")).strip()
            func_name = str(detail.get("function", "")).strip()
            if module_name and func_name:
                engines.append(PluginEngine(name, module_name, func_name))
    if not any(e.name == "mineru" for e in engines):
        engines.append(PluginEngine("mineru", "app.core.mineru_ocr", "ocr_document"))
    return engines


def benchmark_engines(cfg: Dict[str, Any], pdf_path: str, lang: Optional[str] = None, dpi: Optional[int] = None, engines: Optional[List[OCREngine]] = None) -> List[Dict[str, Any]]:
    lang = lang or str(cfg.get("ocr_languages", "chi_sim+eng"))
    dpi = int(dpi or cfg.get("ocr_dpi", 220))
    manager = OCREngineManager(cfg, engines=engines)
    results = []
    for name in manager.list_engines():
        engine = manager.get_engine(name)
        if not engine or not engine.is_available():
            results.append({"engine": name, "available": False,
                           "elapsed_ms": None, "text_length": 0})
            continue
        start = time.perf_counter()
        try:
            text, pages = engine.ocr_pdf(pdf_path, lang, dpi)
            elapsed = int((time.perf_counter() - start) * 1000)
            results.append({"engine": name, "available": True,
                           "elapsed_ms": elapsed, "text_length": len(text), "pages": pages})
        except Exception:
            elapsed = int((time.perf_counter() - start) * 1000)
            results.append({"engine": name, "available": False,
                           "elapsed_ms": elapsed, "text_length": 0})
    return results


def detect_dependencies(cmd_runner=None) -> Dict[str, Any]:
    if cmd_runner is None:
        def cmd_runner(cmd):
            return subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
    info = {}
    mineru_info = {"module": False, "cli": False, "version": None}
    try:
        mineru_info["module"] = importlib.util.find_spec("mineru") is not None
    except Exception:
        mineru_info["module"] = False
    mineru_path = shutil.which("mineru")
    mineru_info["cli"] = bool(mineru_path)
    if mineru_path:
        try:
            out = cmd_runner(["mineru", "--version"])
            mineru_info["version"] = out.splitlines()[0].strip()
        except Exception:
            mineru_info["version"] = None
    info["mineru"] = mineru_info
    return info
