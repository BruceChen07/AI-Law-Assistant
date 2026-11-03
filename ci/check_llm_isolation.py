"""Fail CI when local llama.cpp code crosses into cloud API test concerns."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCAL_PATHS = (
    ROOT / "app" / "local_llm",
    ROOT / "bin" / "apply-local-llm-config.py",
    ROOT / "bin" / "start-local-llamacpp-server.py",
    ROOT / "bin" / "start-local-llamacpp-stack.py",
)
FORBIDDEN = {
    "cloud URL": re.compile(
        r"https?://(?:api\.openai\.com|api\.deepseek\.com|"
        r"dashscope\.aliyuncs\.com|qianfan\.baidubce\.com)",
        re.IGNORECASE,
    ),
    "cloud credential": re.compile(
        r"(?:OPENAI|DEEPSEEK|DASHSCOPE|QIANFAN)_(?:API_KEY|ACCESS_KEY|SECRET_KEY)"
    ),
    "cloud module import": re.compile(
        r"^\s*(?:from|import)\s+(?:app\.)?cloud_ai_test\b",
        re.MULTILINE,
    ),
    "Ollama runtime": re.compile(r"\bollama\b", re.IGNORECASE),
}


def _iter_files(path: Path):
    if path.is_file():
        yield path
        return
    yield from (
        item
        for item in path.rglob("*")
        if item.is_file() and item.suffix.lower() in {".py", ".json", ".md"}
    )


def _validate_config() -> list[str]:
    config = json.loads((ROOT / "app" / "config.json").read_text(encoding="utf-8"))
    errors: list[str] = []
    targets = [
        ("llm_config", config.get("llm_config") or {}),
        ("local_llm.main_model", (config.get("local_llm") or {}).get("main_model") or {}),
        ("local_llm.small_model", (config.get("local_llm") or {}).get("small_model") or {}),
    ]
    for name, target in targets:
        if target.get("provider") != "llama_cpp":
            errors.append(f"{name}.provider must be llama_cpp")
        api_base = str(target.get("api_base") or "")
        if not re.match(r"^https?://(?:127\.0\.0\.1|localhost|\[::1\])(?::\d+)?/", api_base):
            errors.append(f"{name}.api_base must be loopback: {api_base!r}")
    local_cfg = config.get("local_llm") or {}
    if local_cfg.get("runtime_mode") != "local":
        errors.append("local_llm.runtime_mode must be local")
    if local_cfg.get("cloud_fallback_enabled") is not False:
        errors.append("local_llm.cloud_fallback_enabled must be false")
    return errors


def main() -> int:
    errors = _validate_config()
    for root in LOCAL_PATHS:
        for path in _iter_files(root):
            text = path.read_text(encoding="utf-8", errors="replace")
            for label, pattern in FORBIDDEN.items():
                if pattern.search(text):
                    relative = path.relative_to(ROOT)
                    errors.append(f"{relative}: forbidden {label}")

    if errors:
        print("LLM isolation check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("LLM isolation check passed: pure loopback llama.cpp runtime.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
