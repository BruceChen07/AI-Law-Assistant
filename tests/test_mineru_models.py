import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

from app.core import mineru_models as mm


PIPELINE_FILES = {
    "models/Layout/PP-DocLayoutV2/config.json": b'{"layout": "v2"}',
    "models/Layout/PP-DocLayoutV2/model.safetensors": b"L" * 4096,
    "models/OCR/paddleocr_torch/ch_PP-OCRv4_rec_infer.pth": bytes(range(256)) * 8,
}
VLM_FILES = {
    "config.json": b'{"model": "mineru-2.5"}',
    "model.safetensors": b"M" * 8192,
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    range_headers = []
    corrupt = False

    def log_message(self, *args):  # noqa: D401 - silence test server logging
        return

    def _files_for_repo(self, path: str):
        if "PDF-Extract-Kit" in path:
            return PIPELINE_FILES
        return VLM_FILES

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/v1/models/"):
            files = self._files_for_repo(parsed.path)
            rows = [
                {
                    "Path": rel,
                    "Size": len(data),
                    "Sha256": _sha256(data),
                    "Type": "blob",
                }
                for rel, data in files.items()
            ]
            body = json.dumps({"Data": {"Files": rows}}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path.startswith("/models/") and "/resolve/" in parsed.path:
            rest = unquote(parsed.path[len("/models/"):])
            repo_path, file_rel = rest.split("/resolve/", 1)
            file_rel = file_rel.split("/", 1)[1]
            self.range_headers.append(self.headers.get("Range", ""))
            data = self._files_for_repo(repo_path).get(file_rel)
            if data is None:
                self.send_error(404)
                return
            start = 0
            raw_range = self.headers.get("Range", "")
            if raw_range.startswith("bytes="):
                start = int(raw_range.split("=", 1)[1].split("-", 1)[0] or 0)
            payload = data[start:]
            if self.corrupt:
                payload = b"X" * len(payload)
            self.send_response(206 if start else 200)
            if start:
                self.send_header(
                    "Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        self.send_error(404)


@pytest.fixture()
def model_server():
    _Handler.range_headers = []
    _Handler.corrupt = False
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _config(tmp_path: Path, server: ThreadingHTTPServer) -> dict:
    base = f"http://127.0.0.1:{server.server_address[1]}"
    return {
        "mineru": {
            "mode": "auto",
            "formula": False,
            "table": False,
            "model_source": "local",
            "local_models_dir": str(tmp_path / "models"),
            "local_config_path": "",
            "download_source": "modelscope",
            "download_source_fallback": "modelscope",
            "download_endpoints": {"modelscope": base},
            "download_max_workers": 2,
            "download_retries": 1,
            "download_retry_backoff_sec": 0,
            "download_patterns": {
                "pipeline": ["models/Layout/PP-DocLayoutV2/*", "models/OCR/paddleocr_torch/*"],
                "vlm": ["*"],
            },
        }
    }


def test_inspect_reports_missing_models(tmp_path):
    cfg = {"mineru": {"model_source": "local", "local_models_dir": str(tmp_path / "models")}}

    status = mm.inspect_local_models(cfg)

    assert status["ok"] is False
    assert status["missing"]
    assert "ensure_local_models.py" in status["message"]


def test_stage_models_downloads_and_writes_local_config(tmp_path, model_server):
    cfg = _config(tmp_path, model_server)

    result = mm.stage_models(cfg)

    assert result["ok"] is True
    root = tmp_path / "models"
    assert (root / "pipeline" / "models" / "Layout" / "PP-DocLayoutV2" / "model.safetensors").read_bytes() == PIPELINE_FILES[
        "models/Layout/PP-DocLayoutV2/model.safetensors"]
    assert (root / "vlm" / "model.safetensors").read_bytes() == VLM_FILES["model.safetensors"]

    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    vlm_files = manifest["kinds"]["vlm"]["files"]
    assert vlm_files["model.safetensors"]["sha256"] == _sha256(VLM_FILES["model.safetensors"])

    mineru_json = json.loads((root / "mineru.json").read_text(encoding="utf-8"))
    assert mineru_json["models-dir"]["pipeline"] == str(root / "pipeline")
    assert mineru_json["models-dir"]["vlm"] == str(root / "vlm")

    assert mm.inspect_local_models(cfg)["ok"] is True


def test_download_resumes_from_partial_file(tmp_path, model_server):
    cfg = _config(tmp_path, model_server)
    part_path = tmp_path / "models" / "vlm" / "model.safetensors.part"
    part_path.parent.mkdir(parents=True, exist_ok=True)
    part_path.write_bytes(VLM_FILES["model.safetensors"][:2048])

    mm.stage_models(cfg)

    assert "bytes=2048-" in _Handler.range_headers
    assert (tmp_path / "models" / "vlm" / "model.safetensors").read_bytes() == VLM_FILES["model.safetensors"]
    assert not part_path.exists()


def test_corrupt_download_raises_integrity_error(tmp_path, model_server):
    cfg = _config(tmp_path, model_server)
    _Handler.corrupt = True

    with pytest.raises(mm.MineruModelIntegrityError):
        mm.stage_models(cfg)

    assert not (tmp_path / "models" / "vlm" / "model.safetensors").exists()
    assert mm.get_status()["ok"] is False


def test_ensure_local_models_without_download_does_not_touch_network(tmp_path):
    cfg = {"mineru": {"model_source": "local", "local_models_dir": str(tmp_path / "models")}}

    status = mm.ensure_local_models(cfg, allow_download=False)

    assert status["ok"] is False
    assert not (tmp_path / "models").exists()


def test_startup_hook_skips_download_under_pytest(tmp_path):
    cfg = {"mineru": {
        "model_source": "local",
        "local_models_dir": str(tmp_path / "models"),
        "auto_download_on_startup": True,
        "auto_download_background": True,
    }}

    status = mm.ensure_models_on_startup(cfg)

    assert status["ok"] is False
    assert mm.get_status()["running"] is False


def test_local_model_source_is_preserved(tmp_path):
    cfg = {"mineru": {"model_source": "local",
                      "local_models_dir": str(tmp_path / "models")}}

    env = mm.resolve_runtime_env(cfg, base_env={})

    assert env["MINERU_MODEL_SOURCE"] == "local"
    assert env["MINERU_TOOLS_CONFIG_JSON"].endswith("mineru.json")


