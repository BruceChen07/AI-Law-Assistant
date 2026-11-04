import json
import logging
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from app.app_factory import create_app


class _FakeEmbedder:
    def get_registry_status(self):
        return {
            "ready": True,
            "default_language": "zh",
            "languages": ["zh"],
        }


def _isolated_config(tmp_path: Path) -> dict:
    data_dir = tmp_path / "data"
    return {
        "data_dir": str(data_dir),
        "db_path": str(data_dir / "app.db"),
        "files_dir": str(data_dir / "files"),
        "static_dir": str(tmp_path / "static"),
        "log_dir": str(tmp_path / "logs"),
        "log_base_dir": str(tmp_path / "logs"),
        "default_language": "zh",
        "model_preflight_check_on_startup": False,
        "cors_allow_origins": [],
    }


def test_create_app_accepts_isolated_config_without_bootstrap(tmp_path):
    marker = object()
    app = create_app(
        cfg_override=_isolated_config(tmp_path),
        service_overrides={
            "logger": logging.getLogger("audit-validation-test"),
            "rag_logger": logging.getLogger("audit-validation-rag-test"),
            "embedder": _FakeEmbedder(),
            "reranker": marker,
            "llm": marker,
            "translator": marker,
        },
        initialize=False,
        bootstrap_admin=False,
        preflight=False,
    )

    with TestClient(app) as client:
        root = client.get("/")
        health = client.get("/health")

    assert root.status_code == 200
    assert health.status_code == 200
    assert health.json()["embedding_ready"] is True
    assert not Path(app.state.cfg["db_path"]).exists()


def test_prepare_validation_environment_uses_only_output_directory(tmp_path):
    source_root = tmp_path / "source"
    production_data = source_root / "production-data"
    source_root.mkdir()
    production_data.mkdir()
    source_config = source_root / "config.json"
    source_config.write_text(
        json.dumps(
            {
                "data_dir": str(production_data),
                "db_path": str(production_data / "app.db"),
                "files_dir": str(production_data / "files"),
                "static_dir": str(source_root / "static"),
            }
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "validation"
    script = Path(__file__).resolve().parents[1] / "bin" / "prepare_audit_validation.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--source-config",
            str(source_config),
            "--output-dir",
            str(output_dir),
            "--mode",
            "unit",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    generated = json.loads((output_dir / "config.json").read_text(encoding="utf-8"))
    assert generated["validation_environment"]["enabled"] is True
    assert generated["model_preflight_check_on_startup"] is False
    assert Path(generated["db_path"]).is_relative_to(output_dir)
    assert list(production_data.iterdir()) == []


def test_prepare_validation_environment_rejects_production_data(tmp_path):
    production_data = tmp_path / "production-data"
    production_data.mkdir()
    source_config = tmp_path / "config.json"
    source_config.write_text(
        json.dumps({"data_dir": str(production_data)}),
        encoding="utf-8",
    )
    script = Path(__file__).resolve().parents[1] / "bin" / "prepare_audit_validation.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--source-config",
            str(source_config),
            "--output-dir",
            str(production_data),
            "--mode",
            "unit",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "must not overlap production data_dir" in completed.stderr
