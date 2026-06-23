"""Utilities for tolerant JSON object extraction and light repair."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Tuple
import json
import re


def _normalize_text(raw_text: Any) -> str:
    s = str(raw_text or "").strip()
    if not s:
        return ""
    s = s.replace("\ufeff", "").strip()
    s = s.replace("“", '"').replace("”", '"')
    s = s.replace("‘", "'").replace("’", "'")
    return s


def _strip_code_fence(text: str) -> str:
    fenced = re.sub(r"^\s*```(?:json)?\s*", "", text,
                    count=1, flags=re.IGNORECASE)
    fenced = re.sub(r"\s*```\s*$", "", fenced, count=1,
                    flags=re.IGNORECASE).strip()
    return fenced


def _extract_outer_object(text: str) -> str:
    left = text.find("{")
    right = text.rfind("}")
    if left >= 0 and right > left:
        return text[left:right + 1].strip()
    return ""


def _remove_trailing_commas(text: str) -> str:
    return re.sub(r",\s*([}\]])", r"\1", text)


def iter_json_object_candidates(raw_text: Any) -> Iterable[str]:
    s = _normalize_text(raw_text)
    if not s:
        return []

    fenced = _strip_code_fence(s)
    outer_from_fenced = _extract_outer_object(fenced)
    outer_from_raw = _extract_outer_object(s)

    candidates = []
    for item in [fenced, outer_from_fenced, s, outer_from_raw]:
        value = str(item or "").strip()
        if value and value not in candidates:
            candidates.append(value)

    repaired = []
    for item in list(candidates):
        cleaned = _remove_trailing_commas(item)
        if cleaned and cleaned not in candidates and cleaned not in repaired:
            repaired.append(cleaned)
    candidates.extend(repaired)
    return candidates


def parse_json_object(raw_text: Any, defaults: Dict[str, Any] | None = None) -> Dict[str, Any]:
    base = dict(defaults or {})
    for item in iter_json_object_candidates(raw_text):
        try:
            parsed = json.loads(item)
            if isinstance(parsed, dict):
                out = dict(base)
                out.update(parsed)
                return out
        except Exception:
            continue
    return dict(base)


def parse_json_object_with_meta(
    raw_text: Any,
    defaults: Dict[str, Any] | None = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    candidates = list(iter_json_object_candidates(raw_text))
    parsed = parse_json_object(raw_text, defaults=defaults)
    meta = {
        "ok": bool(parsed),
        "candidate_count": len(candidates),
        "used_fallback_defaults": bool(defaults) and not bool(parsed.keys() - dict(defaults or {}).keys()),
    }
    return parsed, meta
