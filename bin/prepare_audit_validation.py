from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict


MODES = {"unit", "integration", "benchmark"}


def _resolve_config_path(project_root: Path, raw: str | None) -> Path:
    if raw:
        path = Path(raw).expanduser().resolve()
    else:
        path = (project_root / "app" / "config.json").resolve()
    if not path.is_file():
        raise ValueError(f"source config does not exist: {path}")
    return path


def _resolve_config_value(config_path: Path, value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value.replace("\\", os.sep).replace("/", os.sep))
    if not path.is_absolute():
        path = config_path.parent / path
    return path.resolve()


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def build_validation_config(
    source: Dict[str, Any],
    *,
    source_path: Path,
    output_dir: Path,
    mode: str,
) -> Dict[str, Any]:
    cfg = dict(source)
    data_dir = output_dir / "data"
    cfg.update(
        {
            "data_dir": str(data_dir),
            "db_path": str(data_dir / "app.db"),
            "files_dir": str(data_dir / "files"),
            "static_dir": str(output_dir / "static"),
            "log_dir": str(output_dir / "logs"),
            "log_base_dir": str(output_dir / "logs"),
            "llm_trace_dir": str(data_dir / "memory" / "llm_interactions"),
            "llm_trace_full_dir": str(data_dir / "llm_traces_full"),
            "llm_trace_full_db_path": str(data_dir / "llm_trace.db"),
            "rag_trace_dir": str(data_dir / "memory" / "debug" / "rag_retrieval"),
            "model_preflight_check_on_startup": False,
            "model_preflight_auto_download_on_startup": False,
            "model_preflight_require_all": False,
            "llm_trace_enabled": mode == "benchmark",
            "llm_trace_full_enabled": False,
            "rag_trace_enabled": mode != "unit",
            "validation_environment": {
                "enabled": True,
                "mode": mode,
                "source_config": str(source_path),
            },
        }
    )
    return cfg


def prepare_environment(
    *,
    project_root: Path,
    source_config: Path,
    output_dir: Path,
    mode: str,
) -> Path:
    if mode not in MODES:
        raise ValueError(f"invalid mode: {mode}")
    if not output_dir.is_absolute():
        raise ValueError("output-dir must be an absolute path")

    with source_config.open("r", encoding="utf-8") as handle:
        source = json.load(handle)
    production_data = _resolve_config_value(source_config, source.get("data_dir"))
    output_dir = output_dir.resolve()
    if production_data and (
        output_dir == production_data
        or _is_within(output_dir, production_data)
        or _is_within(production_data, output_dir)
    ):
        raise ValueError(
            f"output-dir must not overlap production data_dir: {production_data}"
        )
    if _is_within(output_dir, project_root / "data"):
        raise ValueError("output-dir must not be inside the repository production data")

    for relative in (
        "data/files",
        "data/memory/llm_interactions",
        "data/memory/debug/rag_retrieval",
        "logs",
        "static",
        "tmp",
        "evidence",
    ):
        (output_dir / relative).mkdir(parents=True, exist_ok=True)

    config = build_validation_config(
        source,
        source_path=source_config,
        output_dir=output_dir,
        mode=mode,
    )
    config_path = output_dir / "config.json"
    temporary = output_dir / "config.json.tmp"
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(config_path)
    return config_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create an isolated contract-audit validation environment."
    )
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--mode", required=True, choices=sorted(MODES))
    parser.add_argument("--source-config")
    args = parser.parse_args(argv)

    project_root = Path(__file__).resolve().parents[1]
    try:
        source_config = _resolve_config_path(project_root, args.source_config)
        config_path = prepare_environment(
            project_root=project_root,
            source_config=source_config,
            output_dir=Path(args.output_dir).expanduser(),
            mode=args.mode,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"validation environment error: {exc}", file=sys.stderr)
        return 2

    temp_dir = config_path.parent / "tmp"
    print(f"APP_CONFIG={config_path}")
    print(f"TEMP={temp_dir}")
    print(f"TMP={temp_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
