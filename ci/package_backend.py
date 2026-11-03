from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

IGNORE_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "node_modules",
    "dist",
    "storybook-static",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a reproducible backend runtime artifact.")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    output_dir = Path(args.output_dir).resolve()
    package_root = output_dir / "backend-runtime"

    if package_root.exists():
        shutil.rmtree(package_root)
    package_root.mkdir(parents=True, exist_ok=True)

    include_paths = [
        repo_root / "app",
        repo_root / "bin",
        repo_root / "README.zh-CN.md",
        repo_root / "README.en-US.md",
        repo_root / "Dockerfile",
        repo_root / "docker-compose.yml",
    ]

    for source in include_paths:
        target = package_root / source.name
        if source.is_dir():
            shutil.copytree(
                source,
                target,
                ignore=shutil.ignore_patterns(
                    *IGNORE_NAMES, "*.pyc", "*.pyo", "*.log"),
            )
        elif source.exists():
            shutil.copy2(source, target)

    manifest = {
        "artifact": "backend-runtime",
        "repo_root": str(repo_root),
        "included": [source.name for source in include_paths if source.exists()],
    }
    (package_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    archive_base = output_dir / "backend-runtime"
    shutil.make_archive(str(archive_base), "zip",
                        root_dir=output_dir, base_dir=package_root.name)
    print(str(archive_base.with_suffix(".zip")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
