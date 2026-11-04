"""Export file naming.

Design goals (see docs/engineering/export-filename-scheme-20261104):
- the visible name must be derived from the uploaded contract name, so a user can
  tell immediately which contract a file belongs to;
- the timestamp is the export time (not the audit time), so repeated exports of
  the same contract stay distinguishable;
- the trailing token describes the content in the user's language;
- sanitizing only replaces characters that are illegal on disk: CJK, brackets and
  spaces must survive (the previous implementation truncated such names).
"""
import os
import re
from datetime import datetime, timezone
from typing import Optional


_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WHITESPACE_RE = re.compile(r"\s+")
_UNDERSCORE_RE = re.compile(r"_+")
_BARE_EXTENSION_STEMS = {
    "docx", "doc", "pdf", "txt", "md", "json", "jsonl", "csv", "xlsx", "xls",
    "png", "jpg", "jpeg", "gif", "zip", "dat",
}

_RESERVED_WINDOWS_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

MAX_STEM_LENGTH = 80

# kind -> (zh label, en label)
_EXPORT_KIND_LABELS = {
    "report": ("审计报告", "audit_report"),
    "data": ("审计数据", "audit_data"),
    "comments": ("批注版", "commented"),
    "markdown": ("报告", "report"),
    "tax_report": ("税务审计报告", "tax_audit_report"),
    "tax_data": ("税务审计数据", "tax_audit_data"),
}


def _is_zh(locale: str) -> bool:
    return str(locale or "zh").strip().lower().startswith("zh")


def localized_export_suffix(kind: str, locale: str = "zh-CN") -> str:
    """Content label in the UI language, e.g. report -> 审计报告 / audit_report."""
    key = str(kind or "").strip().lower()
    zh_label, en_label = _EXPORT_KIND_LABELS.get(key, (key, key))
    return zh_label if _is_zh(locale) else en_label


def export_fallback_stem(locale: str = "zh-CN") -> str:
    return "合同" if _is_zh(locale) else "contract"


def sanitize_export_filename_stem(
    value: str,
    fallback: str = "contract",
    max_length: int = MAX_STEM_LENGTH,
) -> str:
    """Turn an arbitrary contract name into a safe, still-readable file stem.

    Characters that are illegal on common filesystems become "_"; CJK text,
    brackets and other printable characters are preserved. The extension is
    dropped after sanitizing (sanitizing first keeps names such as
    "采购合同（终版）: 华北区/2026" intact instead of collapsing to "2026").
    """
    raw = str(value or "").strip()
    if raw:
        raw = _INVALID_FILENAME_CHARS.sub("_", raw)
        raw = raw.replace("\u3000", " ")
        raw = _WHITESPACE_RE.sub("_", raw)
        raw = _UNDERSCORE_RE.sub("_", raw)
        raw = os.path.splitext(raw)[0]
        raw = raw.strip(" ._")
        if raw.lower() in _BARE_EXTENSION_STEMS:
            raw = ""
    if not raw:
        raw = str(fallback or "contract").strip() or "contract"
    raw = raw[: max(8, int(max_length or MAX_STEM_LENGTH))].strip(" ._")
    if not raw:
        raw = "contract"
    if raw.upper() in _RESERVED_WINDOWS_NAMES:
        raw = f"{raw}_"
    return raw


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
    """`{contract name}_{export time}_{content label}.{ext}`"""
    stem = sanitize_export_filename_stem(source_filename, fallback=fallback_stem)
    timestamp = to_export_timestamp(generated_at)
    normalized_suffix = sanitize_export_filename_stem(
        suffix, fallback="export", max_length=32)
    normalized_ext = str(ext or "").strip().lstrip(".") or "dat"
    return f"{stem}_{timestamp}_{normalized_suffix}.{normalized_ext}"
