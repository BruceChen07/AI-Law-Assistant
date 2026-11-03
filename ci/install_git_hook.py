from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install local git hooks for the CI pipeline.")
    parser.add_argument("--repo-root", default="")
    parser.add_argument("--hook-name", default="post-commit")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve() if args.repo_root else Path(__file__).resolve().parent.parent
    source = repo_root / ".githooks" / args.hook_name
    target = repo_root / ".git" / "hooks" / args.hook_name
    if not source.exists():
        raise FileNotFoundError(f"hook template not found: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    target.chmod(0o755)
    print(target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
