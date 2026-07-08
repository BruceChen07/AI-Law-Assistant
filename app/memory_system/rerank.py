from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any, Dict, List


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[\u4e00-\u9fffA-Za-z0-9_]{2,}", str(text or "").lower())


def _clip(text: Any, max_chars: int) -> str:
    s = str(text or "")
    limit = max(0, int(max_chars or 0))
    if limit <= 0 or len(s) <= limit:
        return s
    return s[:limit]


def _parse_iso(ts: str) -> datetime | None:
    s = str(ts or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00").replace("+00:00", ""))
    except Exception:
        return None


def _semantic_similarity(query_text: str, candidate_text: str) -> float:
    q = set(_tokenize(query_text))
    c = set(_tokenize(candidate_text))
    if not q or not c:
        return 0.0
    overlap = len(q & c)
    return max(0.0, min(1.0, float(overlap) / float(max(1, len(q)))))


def _regulation_match(candidate: Dict[str, Any], regulation_pack_id: str) -> float:
    expected = str(regulation_pack_id or "").strip()
    if not expected:
        return 0.0
    row_pack = str(candidate.get("regulation_pack_id") or "").strip()
    if not row_pack:
        return 0.0
    return 1.0 if row_pack == expected else 0.0


def _feedback_quality(candidate: Dict[str, Any]) -> float:
    try:
        v = float(candidate.get("memory_quality_score") or 0.0)
    except Exception:
        v = 0.0
    return max(0.0, min(1.0, v))


def _freshness(candidate: Dict[str, Any], now: datetime | None = None) -> float:
    dt = _parse_iso(str(candidate.get("created_at") or ""))
    if dt is None:
        return 0.0
    cur = now or datetime.utcnow()
    days = max(0.0, float((cur - dt).days))
    # 180-day half-life style decay
    return max(0.0, min(1.0, math.exp(-days / 180.0)))


def _success_rate(candidate: Dict[str, Any]) -> float:
    try:
        explicit = float(candidate.get("success_rate") or 0.0)
    except Exception:
        explicit = 0.0
    if explicit > 0.0:
        return max(0.0, min(1.0, explicit))
    outcome = str(candidate.get("outcome") or "").strip().lower()
    if outcome == "success":
        return 1.0
    if outcome == "failure":
        return 0.1
    if outcome == "disputed":
        return 0.4
    if outcome == "pending":
        return 0.5
    return 0.5


def _candidate_text(candidate: Dict[str, Any]) -> str:
    return " ".join(
        [
            str(candidate.get("pattern_text") or ""),
            str(candidate.get("clause_text_excerpt") or ""),
            str(candidate.get("risk_reasoning") or ""),
            str(candidate.get("risk_label") or ""),
            " ".join(candidate.get("legal_basis") or []),
        ]
    ).strip()


def rerank_memory_candidates(
    candidates: List[Dict[str, Any]],
    query_text: str,
    regulation_pack_id: str = "",
) -> List[Dict[str, Any]]:
    """
    score = semantic_similarity*0.35 + regulation_match*0.30
          + feedback_quality*0.20 + freshness*0.10 + success_rate*0.05
    """
    out: List[Dict[str, Any]] = []
    now = datetime.utcnow()
    for item in list(candidates or []):
        cand = dict(item or {})
        text = _candidate_text(cand)
        semantic_similarity = _semantic_similarity(query_text, text)
        regulation_match = _regulation_match(cand, regulation_pack_id)
        feedback_quality = _feedback_quality(cand)
        freshness = _freshness(cand, now=now)
        success_rate = _success_rate(cand)
        score = (
            semantic_similarity * 0.35
            + regulation_match * 0.30
            + feedback_quality * 0.20
            + freshness * 0.10
            + success_rate * 0.05
        )
        cand["_context_text"] = _clip(text, 360)
        cand["_rerank"] = {
            "semantic_similarity": round(semantic_similarity, 6),
            "regulation_match": round(regulation_match, 6),
            "feedback_quality": round(feedback_quality, 6),
            "freshness": round(freshness, 6),
            "success_rate": round(success_rate, 6),
        }
        cand["score"] = round(float(score), 6)
        out.append(cand)
    out.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
    return out


def apply_context_budget(
    candidates: List[Dict[str, Any]],
    max_items: int,
    max_chars: int,
) -> List[Dict[str, Any]]:
    items = list(candidates or [])
    keep_n = max(1, int(max_items or 1))
    char_budget = max(120, int(max_chars or 120))
    selected: List[Dict[str, Any]] = []
    used = 0
    for item in items:
        if len(selected) >= keep_n:
            break
        text = str(item.get("_context_text") or "")
        text_len = len(text)
        if used + text_len > char_budget:
            continue
        selected.append(item)
        used += text_len
    if not selected and items:
        first = dict(items[0])
        first["_context_text"] = _clip(first.get("_context_text") or "", char_budget)
        selected = [first]
    return selected
