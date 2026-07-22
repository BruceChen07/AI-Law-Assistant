import os
import re
from datetime import datetime, timezone
from typing import Optional


_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WHITESPACE_RE = re.compile(r"\s+")
_UNDERSCORE_RE = re.compile(r"_+")


def sanitize_export_filename_stem(value: str, fallback: str = "contract") -> str:
    raw = str(value or "").strip()
    if raw:
        raw = os.path.splitext(os.path.basename(raw))[0]
    if not raw:
        raw = fallback
    cleaned = _INVALID_FILENAME_CHARS.sub("_", raw)
    cleaned = _WHITESPACE_RE.sub("_", cleaned)
    cleaned = _UNDERSCORE_RE.sub("_", cleaned).strip(" ._")
    return cleaned[:120] if cleaned else fallback


def to_export_timestamp(value: Optional[str] = None) -> str:
    raw = str(value or "").strip()
    if raw:
        normalized = raw.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(normalized)
        except ValueError:
            dt = None
        if dt is not None:
            if dt.tzinfo is not None:
                dt = dt.astimezone()
            return dt.strftime("%Y%m%d_%H%M%S")
    return datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")


def build_export_filename(
    *,
    source_filename: str,
    generated_at: Optional[str],
    suffix: str,
    ext: str,
    fallback_stem: str = "contract",
) -> str:
    stem = sanitize_export_filename_stem(source_filename, fallback=fallback_stem)
    timestamp = to_export_timestamp(generated_at)
    normalized_suffix = sanitize_export_filename_stem(suffix, fallback="export")
    normalized_ext = str(ext or "").strip().lstrip(".") or "dat"
    return f"{stem}_{timestamp}_{normalized_suffix}.{normalized_ext}"
