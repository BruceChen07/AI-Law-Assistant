import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def resolve_config_item_path(value: str, base_dir: Path) -> Path | None:
    cleaned = str(value or "").strip()
    if not cleaned:
        return None
    candidate = Path(cleaned)
    if candidate.is_absolute():
        return candidate.resolve()
    return (base_dir / candidate).resolve()


def load_config(config_path: Path) -> dict:
    with config_path.open("r", encoding="utf-8") as file_obj:
        return json.load(file_obj)


def ensure_directory(path: Path | None) -> None:
    if path:
        path.mkdir(parents=True, exist_ok=True)


def collect_directories(cfg: dict, config_dir: Path) -> list[Path]:
    dirs: list[Path] = []

    for key in ("data_dir", "files_dir", "static_dir", "log_dir"):
        resolved = resolve_config_item_path(cfg.get(key), config_dir)
        if resolved:
            dirs.append(resolved)

    embedding_profiles = cfg.get("embedding_profiles")
    if isinstance(embedding_profiles, dict):
        for profile in embedding_profiles.values():
            if not isinstance(profile, dict):
                continue
            embedding_model = resolve_config_item_path(profile.get("embedding_model"), config_dir)
            if embedding_model:
                dirs.append(embedding_model.parent)
            tokenizer_dir = resolve_config_item_path(profile.get("embedding_tokenizer_dir"), config_dir)
            if tokenizer_dir:
                dirs.append(tokenizer_dir)
    else:
        embedding_model = resolve_config_item_path(cfg.get("embedding_model"), config_dir)
        if embedding_model:
            dirs.append(embedding_model.parent)
        tokenizer_dir = resolve_config_item_path(cfg.get("embedding_tokenizer_dir"), config_dir)
        if tokenizer_dir:
            dirs.append(tokenizer_dir)

    unique_dirs: list[Path] = []
    seen: set[str] = set()
    for item in dirs:
        normalized = str(item)
        if normalized not in seen:
            seen.add(normalized)
            unique_dirs.append(item)
    return unique_dirs


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Initialize repository directories and run app.main --init."
    )
    parser.add_argument(
        "--config-path",
        default=str(repo_root / "app" / "config.json"),
        help="Path to app/config.json",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    app_dir = repo_root / "app"
    config_path = Path(args.config_path).resolve()
    config_dir = config_path.parent
    example_path = app_dir / "config.example.json"

    if not config_path.exists():
        config_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(example_path, config_path)
        print(f"[OK] Created config from example: {config_path}")

    cfg = load_config(config_path)
    for directory in collect_directories(cfg, config_dir):
        ensure_directory(directory)
        print(f"[OK] Ensured directory: {directory}")

    env = os.environ.copy()
    old_app_config = env.get("APP_CONFIG")
    env["APP_CONFIG"] = str(config_path)

    try:
        subprocess.run(
            [sys.executable, "-m", "app.main", "--init"],
            cwd=str(repo_root),
            env=env,
            check=True,
        )
    finally:
        if old_app_config is None:
            env.pop("APP_CONFIG", None)
        else:
            env["APP_CONFIG"] = old_app_config

    print("[OK] Initialization completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
