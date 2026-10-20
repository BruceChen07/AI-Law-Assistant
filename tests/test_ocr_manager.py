from app.core.ocr import OCREngineManager, OCREngine, benchmark_engines, detect_dependencies
import app.core.mineru_ocr as mineru_ocr
import json
import subprocess


class StubEngine(OCREngine):
    def __init__(self, name, available=True, text="ok", pages=1):
        self.name = name
        self.available = available
        self.text = text
        self.pages = pages

    def is_available(self) -> bool:
        return self.available

    def ocr_document(self, path: str, lang: str, dpi: int, doc_type: str = "pdf"):
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
    assert "mineru" in info


def test_probe_mineru_capabilities(monkeypatch):
    mineru_ocr._CAPABILITIES_CACHE["ts"] = 0
    mineru_ocr._CAPABILITIES_CACHE["data"] = None
    monkeypatch.setattr(mineru_ocr, "_detect_gpu", lambda: True)
    monkeypatch.setattr(mineru_ocr, "_probe_supported_backends", lambda: [
                        "pipeline", "hybrid-auto-engine"])
    monkeypatch.setattr(mineru_ocr.shutil, "which",
                        lambda name: "C:/bin/mineru.exe" if name == "mineru" else None)
    data = mineru_ocr.probe_mineru_capabilities(
        force_refresh=True, cache_ttl_sec=300)
    assert data["mineru_cli_found"] is True
    assert data["has_gpu"] is True
    assert "pipeline" in data["supported_backends"]


def test_probe_mineru_capabilities_cache(monkeypatch):
    mineru_ocr._CAPABILITIES_CACHE["ts"] = 0
    mineru_ocr._CAPABILITIES_CACHE["data"] = None
    monkeypatch.setattr(mineru_ocr, "_detect_gpu", lambda: False)
    monkeypatch.setattr(
        mineru_ocr, "_probe_supported_backends", lambda: ["pipeline"])
    monkeypatch.setattr(mineru_ocr.shutil, "which",
                        lambda name: "C:/bin/mineru.exe" if name == "mineru" else None)
    first = mineru_ocr.probe_mineru_capabilities(
        force_refresh=True, cache_ttl_sec=300)
    monkeypatch.setattr(mineru_ocr, "_detect_gpu", lambda: True)
    monkeypatch.setattr(mineru_ocr, "_probe_supported_backends", lambda: [
                        "hybrid-auto-engine"])
    second = mineru_ocr.probe_mineru_capabilities(
        force_refresh=False, cache_ttl_sec=300)
    assert second["has_gpu"] == first["has_gpu"]
    assert second["supported_backends"] == first["supported_backends"]


def test_resolve_mineru_backend_auto_gpu_hybrid():
    b = mineru_ocr.resolve_mineru_backend(
        {"mode": "auto"},
        {"has_gpu": True, "supported_backends": [
            "hybrid-auto-engine", "pipeline"]},
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
        {"has_gpu": True, "supported_backends": [
            "hybrid-auto-engine", "pipeline"]},
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


def test_build_model_source_attempts_with_hf_fallback():
    attempts = mineru_ocr._build_model_source_attempts(
        {
            "model_source": "huggingface",
            "hf_retry_count": 2,
            "model_source_fallback_enabled": True,
            "fallback_model_source": "modelscope",
        }
    )
    assert [item["source"] for item in attempts] == [
        "huggingface",
        "huggingface",
        "huggingface",
        "modelscope",
    ]
    assert attempts[-1]["kind"] == "fallback"


def test_is_huggingface_network_error_matches_expected_signatures():
    assert mineru_ocr._is_huggingface_network_error(
        "requests.exceptions.ConnectionError: HTTPSConnectionPool(host='huggingface.co', port=443)"
    )
    assert mineru_ocr._is_huggingface_network_error(
        "HTTP 503 Service Unavailable from huggingface"
    )
    assert mineru_ocr._is_huggingface_network_error(
        "socket.gaierror: [Errno 11001] getaddrinfo failed"
    )
    assert not mineru_ocr._is_huggingface_network_error(
        "ValueError: invalid pdf content list"
    )


def test_run_mineru_falls_back_to_modelscope_on_hf_network_error(tmp_path, monkeypatch):
    source_pdf = tmp_path / "sample.pdf"
    source_pdf.write_bytes(b"%PDF-1.4\n")
    out_dir = tmp_path / "mineru"
    out_dir.mkdir()

    monkeypatch.setattr(
        mineru_ocr,
        "get_config",
        lambda: {
            "ocr_languages": "chi_sim+eng",
            "mineru": {
                "mode": "force_pipeline",
                "fallback_backends": ["pipeline"],
                "probe_cache_ttl_sec": 300,
                "method": "auto",
                "lang": "ch",
                "device": "cpu",
                "formula": False,
                "table": False,
                "model_source": "huggingface",
                "model_source_fallback_enabled": True,
                "fallback_model_source": "modelscope",
                "hf_retry_count": 0,
                "timeout": 30,
                "hf_connect_timeout_sec": 3,
                "hf_download_timeout_sec": 5,
            },
        },
    )
    monkeypatch.setattr(
        mineru_ocr,
        "probe_mineru_capabilities",
        lambda **_kwargs: {"supported_backends":
                           ["pipeline"], "has_gpu": False},
    )
    monkeypatch.setattr(mineru_ocr, "_resolve_mineru_cli",
                        lambda: "mineru.exe")

    calls = []

    def fake_run(cmd, check, capture_output, text, timeout, env, encoding=None, errors=None):
        calls.append({"cmd": cmd, "env": dict(env)})
        source = env.get("MINERU_MODEL_SOURCE")
        if source == "huggingface":
            raise subprocess.CalledProcessError(
                returncode=1,
                cmd=cmd,
                output="",
                stderr="requests.exceptions.ConnectionError: HTTPSConnectionPool(host='huggingface.co', port=443)",
            )
        stem = source_pdf.stem
        (out_dir / f"{stem}.md").write_text("ok", encoding="utf-8")
        (out_dir / f"{stem}_middle.json").write_text(
            json.dumps({"pdf_info": [{"page_no": 1}]}),
            encoding="utf-8",
        )
        (out_dir / f"{stem}_content_list.json").write_text("[]",
                                                           encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="done", stderr="")

    monkeypatch.setattr(mineru_ocr.subprocess, "run", fake_run)

    result = mineru_ocr.run_mineru_extract(
        str(source_pdf), output_dir=str(out_dir))

    assert result["text"] == "ok"
    assert result["pages"] == 1
    assert [item["env"]["MINERU_MODEL_SOURCE"] for item in calls] == [
        "huggingface",
        "modelscope",
    ]
    assert calls[0]["env"]["HF_HUB_ETAG_TIMEOUT"] == "3"
    assert calls[0]["env"]["HF_HUB_DOWNLOAD_TIMEOUT"] == "5"


def test_run_mineru_does_not_fallback_to_modelscope_on_non_network_failure(tmp_path, monkeypatch):
    source_pdf = tmp_path / "sample.pdf"
    source_pdf.write_bytes(b"%PDF-1.4\n")
    out_dir = tmp_path / "mineru"
    out_dir.mkdir()

    monkeypatch.setattr(
        mineru_ocr,
        "get_config",
        lambda: {
            "ocr_languages": "chi_sim+eng",
            "mineru": {
                "mode": "force_pipeline",
                "fallback_backends": ["pipeline"],
                "method": "auto",
                "lang": "ch",
                "device": "cpu",
                "formula": False,
                "table": False,
                "model_source": "huggingface",
                "model_source_fallback_enabled": True,
                "fallback_model_source": "modelscope",
                "hf_retry_count": 0,
                "timeout": 30,
            },
        },
    )
    monkeypatch.setattr(
        mineru_ocr,
        "probe_mineru_capabilities",
        lambda **_kwargs: {"supported_backends":
                           ["pipeline"], "has_gpu": False},
    )
    monkeypatch.setattr(mineru_ocr, "_resolve_mineru_cli",
                        lambda: "mineru.exe")

    calls = []

    def fake_run(cmd, check, capture_output, text, timeout, env, encoding=None, errors=None):
        calls.append(env.get("MINERU_MODEL_SOURCE"))
        raise subprocess.CalledProcessError(
            returncode=1,
            cmd=cmd,
            output="",
            stderr="ValueError: malformed layout result",
        )

    monkeypatch.setattr(mineru_ocr.subprocess, "run", fake_run)

    try:
        mineru_ocr.run_mineru_extract(str(source_pdf), output_dir=str(out_dir))
    except subprocess.CalledProcessError:
        pass
    else:
        raise AssertionError("Expected CalledProcessError to be raised")

    assert calls == ["huggingface"]
