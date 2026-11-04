"""配置文件应容忍 Windows 编辑器写入的 UTF-8 BOM。"""

import importlib.util
import json
from pathlib import Path

import pytest

from app.core import config as app_config

REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_bom_json(path: Path, payload: dict) -> None:
    path.write_bytes(
        b"\xef\xbb\xbf" + json.dumps(payload, ensure_ascii=False).encode("utf-8"))


@pytest.fixture()
def bom_config(tmp_path, monkeypatch):
    cfg = {
        "data_dir": str(tmp_path / "data"),
        "db_path": str(tmp_path / "data" / "app.db"),
        "files_dir": str(tmp_path / "data" / "files"),
        "static_dir": str(tmp_path / "web" / "dist"),
        "default_language": "zh",
    }
    path = tmp_path / "config.json"
    _write_bom_json(path, cfg)
    monkeypatch.setenv("APP_CONFIG", str(path))
    app_config.load_config.cache_clear()
    yield path
    app_config.load_config.cache_clear()


def test_load_config_accepts_bom(bom_config):
    cfg = app_config.get_config()

    assert cfg["default_language"] == "zh"
    assert cfg["db_path"].endswith("app.db")


def test_update_config_patch_accepts_bom(bom_config):
    app_config.update_config_patch({"default_language": "en"})

    raw = bom_config.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert json.loads(raw.decode("utf-8"))["default_language"] == "en"


def test_start_services_read_config_accepts_bom(bom_config, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "start_services_script", REPO_ROOT / "bin" / "start-services.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    cfg = module._read_config(tmp_path)

    assert cfg is not None
    assert cfg["default_language"] == "zh"
