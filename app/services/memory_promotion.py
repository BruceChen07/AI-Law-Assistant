from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

_PROMOTION_LOCK = threading.Lock()


def _utc_now_iso() -> str:
    return datetime.utcnow().isoformat()


def _memory_root(cfg: Dict[str, Any]) -> str:
    memory_dir = str((cfg or {}).get("memory_dir") or "").strip()
    if memory_dir:
        return os.path.abspath(memory_dir)
    data_dir = str((cfg or {}).get("data_dir") or "").strip()
    if data_dir:
        return os.path.abspath(os.path.join(data_dir, "memory"))
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "../../memory"))


def _experience_dir(cfg: Dict[str, Any]) -> str:
    return os.path.join(_memory_root(cfg), "experience")


def _episode_store_path(cfg: Dict[str, Any]) -> str:
    custom = str((cfg or {}).get("memory_episode_store_path") or "").strip()
    if custom:
        return os.path.abspath(custom)
    return os.path.join(_experience_dir(cfg), "case_episode_pending.jsonl")


def _feedback_store_path(cfg: Dict[str, Any]) -> str:
    custom = str((cfg or {}).get("memory_feedback_store_path") or "").strip()
    if custom:
        return os.path.abspath(custom)
    return os.path.join(_experience_dir(cfg), "feedback_events.jsonl")


def _rule_pending_store_path(cfg: Dict[str, Any]) -> str:
    custom = str((cfg or {}).get("memory_rule_pending_store_path") or "").strip()
    if custom:
        return os.path.abspath(custom)
    return os.path.join(_experience_dir(cfg), "rule_memory_pending_approval.jsonl")


def _rule_active_store_path(cfg: Dict[str, Any]) -> str:
    custom = str((cfg or {}).get("memory_rule_active_store_path") or "").strip()
    if custom:
        return os.path.abspath(custom)
    return os.path.join(_experience_dir(cfg), "rule_memory_active.jsonl")


def _promotion_log_path(cfg: Dict[str, Any]) -> str:
    custom = str((cfg or {}).get("memory_promotion_log_path") or "").strip()
    if custom:
        return os.path.abspath(custom)
    return os.path.join(_experience_dir(cfg), "promotion_log.jsonl")


def _read_jsonl(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    out: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = str(line or "").strip()
            if not s:
                continue
            try:
                row = json.loads(s)
            except Exception:
                continue
            if isinstance(row, dict):
                out.append(row)
    return out


def _write_jsonl(path: str, rows: List[Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in list(rows or []):
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _append_jsonl(path: str, row: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[\u4e00-\u9fffA-Za-z0-9_]{2,}", str(text or "").lower())


def _extract_trigger_patterns(episode: Dict[str, Any], max_terms: int = 6) -> List[str]:
    joined = " ".join(
        [
            str(episode.get("clause_text_excerpt") or ""),
            str(episode.get("risk_reasoning") or ""),
            str(episode.get("risk_label") or ""),
        ]
    )
    tokens = _tokenize(joined)
    seen = set()
    out: List[str] = []
    for t in tokens:
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= max(1, int(max_terms or 1)):
            break
    return out


def _feedback_support(feedback_rows: List[Dict[str, Any]], episode: Dict[str, Any]) -> Dict[str, Any]:
    pack_id = str(episode.get("regulation_pack_id") or "")
    clause_category = str(episode.get("clause_category") or "")
    risk_label = str(episode.get("risk_label") or "")
    matched: List[Dict[str, Any]] = []
    for row in list(feedback_rows or []):
        if not isinstance(row, dict):
            continue
        row_pack = str(row.get("regulation_pack_id") or "")
        if pack_id and row_pack and row_pack != pack_id:
            continue
        row_cat = str(row.get("clause_category") or "")
        if clause_category and row_cat and row_cat != clause_category:
            continue
        row_label = str(row.get("risk_label") or "")
        if risk_label and row_label and row_label != risk_label:
            continue
        matched.append(row)
    quality_values: List[float] = []
    success_count = 0
    for row in matched:
        try:
            quality_values.append(float(row.get("memory_quality_score") or 0.0))
        except Exception:
            pass
        if str(row.get("outcome") or "").strip().lower() == "success":
            success_count += 1
    avg_quality = (sum(quality_values) / len(quality_values)) if quality_values else 0.0
    support_count = len(matched)
    success_rate = (float(success_count) / float(max(1, support_count))) if support_count > 0 else 0.0
    return {
        "support_count": support_count,
        "avg_feedback_quality": round(avg_quality, 4),
        "success_rate": round(success_rate, 4),
    }


def _compose_rule_from_episode(
    episode: Dict[str, Any],
    support: Dict[str, Any],
    status: str,
) -> Dict[str, Any]:
    now = _utc_now_iso()
    base_quality = float(episode.get("memory_quality_score") or 0.0)
    feedback_quality = float(support.get("avg_feedback_quality") or 0.0)
    quality = round(base_quality * 0.6 + feedback_quality * 0.4, 4)
    rule_id = f"rm_{uuid.uuid4().hex[:16]}"
    return {
        "memory_id": rule_id,
        "memory_type": "rule",
        "source_episode_id": str(episode.get("memory_id") or ""),
        "contract_type": str(episode.get("contract_type") or ""),
        "industry": str(episode.get("industry") or ""),
        "jurisdiction": str(episode.get("jurisdiction") or ""),
        "regulation_pack_id": str(episode.get("regulation_pack_id") or ""),
        "regulation_fingerprint": str(episode.get("regulation_fingerprint") or ""),
        "clause_category": str(episode.get("clause_category") or ""),
        "clause_text_excerpt": str(episode.get("clause_text_excerpt") or ""),
        "risk_label": str(episode.get("risk_label") or ""),
        "risk_reasoning": str(episode.get("risk_reasoning") or ""),
        "legal_basis": list(episode.get("legal_basis") or []),
        "exception_conditions": str(episode.get("exception_conditions") or ""),
        "trigger_patterns": _extract_trigger_patterns(episode),
        "outcome": "pending" if status == "pending_approval" else "success",
        "feedback_source": "offline_eval",
        "confidence": float(episode.get("confidence") or 0.0),
        "memory_quality_score": quality,
        "support_count": int(support.get("support_count") or 0),
        "success_rate": float(support.get("success_rate") or 0.0),
        "created_at": now,
        "updated_at": now,
        "superseded_by": "",
        "status": status,
        "approval_required": bool(status == "pending_approval"),
    }


def promote_episode_to_rule(
    cfg: Dict[str, Any],
    episode_id: str = "",
    min_support_count: Optional[int] = None,
    min_quality_score: Optional[float] = None,
    high_impact_threshold: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Stage 2 write:
    - Select pending episodes
    - Refine into rule memories
    - Gate high-impact rules with manual approval
    """
    if not bool((cfg or {}).get("memory_rule_promotion_enabled", True)):
        return {"ok": False, "reason": "disabled"}

    support_threshold = int(min_support_count if min_support_count is not None else (cfg or {}).get("memory_promotion_min_support_count", 1))
    quality_threshold = float(min_quality_score if min_quality_score is not None else (cfg or {}).get("memory_promotion_min_quality_score", 0.55))
    high_impact_gate = float(high_impact_threshold if high_impact_threshold is not None else (cfg or {}).get("memory_rule_high_impact_threshold", 0.82))
    risk_count_gate = int((cfg or {}).get("memory_rule_high_impact_risk_count", 3))

    episode_path = _episode_store_path(cfg)
    feedback_path = _feedback_store_path(cfg)
    pending_rule_path = _rule_pending_store_path(cfg)
    active_rule_path = _rule_active_store_path(cfg)
    log_path = _promotion_log_path(cfg)

    with _PROMOTION_LOCK:
        episodes = _read_jsonl(episode_path)
        feedback_rows = _read_jsonl(feedback_path)
        remain: List[Dict[str, Any]] = []
        promoted = 0
        queued_for_approval = 0
        skipped = 0

        for ep in episodes:
            if not isinstance(ep, dict):
                continue
            this_id = str(ep.get("memory_id") or "")
            if episode_id and this_id != str(episode_id):
                remain.append(ep)
                continue
            if str(ep.get("status") or "").strip().lower() != "pending":
                remain.append(ep)
                continue

            support = _feedback_support(feedback_rows, ep)
            merged_quality = round(
                float(ep.get("memory_quality_score") or 0.0) * 0.6
                + float(support.get("avg_feedback_quality") or 0.0) * 0.4,
                4,
            )
            if int(support.get("support_count") or 0) < max(0, support_threshold) or merged_quality < max(0.0, quality_threshold):
                skipped += 1
                remain.append(ep)
                continue

            high_impact = (
                merged_quality >= high_impact_gate
                or int(ep.get("risk_count") or 0) >= max(1, risk_count_gate)
            )
            status = "pending_approval" if high_impact else "active"
            rule = _compose_rule_from_episode(ep, support, status=status)
            if status == "pending_approval":
                _append_jsonl(pending_rule_path, rule)
                queued_for_approval += 1
            else:
                _append_jsonl(active_rule_path, rule)
            promoted += 1

            ep_next = dict(ep)
            ep_next["status"] = "promoted"
            ep_next["superseded_by"] = str(rule.get("memory_id") or "")
            ep_next["updated_at"] = _utc_now_iso()
            remain.append(ep_next)

            _append_jsonl(
                log_path,
                {
                    "event_id": f"pl_{uuid.uuid4().hex[:16]}",
                    "event_type": "promote_episode_to_rule",
                    "episode_id": this_id,
                    "rule_id": str(rule.get("memory_id") or ""),
                    "status": status,
                    "support_count": int(support.get("support_count") or 0),
                    "memory_quality_score": float(rule.get("memory_quality_score") or 0.0),
                    "created_at": _utc_now_iso(),
                },
            )

        _write_jsonl(episode_path, remain)
        return {
            "ok": True,
            "promoted_count": promoted,
            "queued_for_approval_count": queued_for_approval,
            "skipped_count": skipped,
            "episode_store_path": episode_path,
            "pending_rule_store_path": pending_rule_path,
            "active_rule_store_path": active_rule_path,
        }


def list_pending_rule_memories(cfg: Dict[str, Any], limit: int = 50) -> List[Dict[str, Any]]:
    rows = _read_jsonl(_rule_pending_store_path(cfg))
    out = [x for x in rows if str((x or {}).get("status") or "").strip().lower() == "pending_approval"]
    out.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return out[: max(1, int(limit or 1))]


def review_rule_memory(
    cfg: Dict[str, Any],
    rule_id: str,
    action: str,
    reviewer_id: str = "",
    note: str = "",
) -> Dict[str, Any]:
    act = str(action or "").strip().lower()
    if act not in {"approve", "reject"}:
        return {"ok": False, "reason": "invalid_action"}

    pending_path = _rule_pending_store_path(cfg)
    active_path = _rule_active_store_path(cfg)
    log_path = _promotion_log_path(cfg)
    with _PROMOTION_LOCK:
        rows = _read_jsonl(pending_path)
        remain: List[Dict[str, Any]] = []
        target: Optional[Dict[str, Any]] = None
        for row in rows:
            if str((row or {}).get("memory_id") or "") == str(rule_id):
                target = dict(row or {})
                continue
            remain.append(row)
        if target is None:
            return {"ok": False, "reason": "not_found"}

        now = _utc_now_iso()
        target["updated_at"] = now
        target["reviewed_by"] = str(reviewer_id or "")
        target["review_note"] = str(note or "")
        if act == "approve":
            target["status"] = "active"
            target["approval_required"] = False
            target["outcome"] = "success"
            _append_jsonl(active_path, target)
        else:
            target["status"] = "rejected"
            target["outcome"] = "failure"
        _write_jsonl(pending_path, remain)

        _append_jsonl(
            log_path,
            {
                "event_id": f"pl_{uuid.uuid4().hex[:16]}",
                "event_type": "review_rule_memory",
                "rule_id": str(rule_id),
                "action": act,
                "reviewer_id": str(reviewer_id or ""),
                "note": str(note or ""),
                "created_at": now,
            },
        )
        return {
            "ok": True,
            "rule_id": str(rule_id),
            "status": str(target.get("status") or ""),
            "action": act,
        }
