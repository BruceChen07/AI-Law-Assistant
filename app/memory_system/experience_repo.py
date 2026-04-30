from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

_EPISODE_WRITE_LOCK = threading.Lock()


def _utc_now_iso() -> str:
    return datetime.utcnow().isoformat()


def _clip(text: Any, max_chars: int) -> str:
    s = str(text or "")
    limit = max(0, int(max_chars or 0))
    if limit <= 0 or len(s) <= limit:
        return s
    return s[:limit]


def _memory_root(cfg: Dict[str, Any]) -> str:
    memory_dir = str((cfg or {}).get("memory_dir") or "").strip()
    if memory_dir:
        return os.path.abspath(memory_dir)
    data_dir = str((cfg or {}).get("data_dir") or "").strip()
    if data_dir:
        return os.path.abspath(os.path.join(data_dir, "memory"))
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "../../memory"))


def _episode_store_path(cfg: Dict[str, Any]) -> str:
    custom = str((cfg or {}).get("memory_episode_store_path") or "").strip()
    if custom:
        return os.path.abspath(custom)
    root = _memory_root(cfg)
    return os.path.join(root, "experience", "case_episode_pending.jsonl")


def _feedback_store_path(cfg: Dict[str, Any]) -> str:
    custom = str((cfg or {}).get("memory_feedback_store_path") or "").strip()
    if custom:
        return os.path.abspath(custom)
    root = _memory_root(cfg)
    return os.path.join(root, "experience", "feedback_events.jsonl")


def _workflow_store_path(cfg: Dict[str, Any]) -> str:
    custom = str((cfg or {}).get("memory_workflow_store_path") or "").strip()
    if custom:
        return os.path.abspath(custom)
    root = _memory_root(cfg)
    return os.path.join(root, "experience", "workflow_memory_active.jsonl")


def _tokenize(text: str) -> List[str]:
    raw = re.findall(r"[\u4e00-\u9fffA-Za-z0-9_]{2,}", str(text or "").lower())
    return [x for x in raw if x]


def _build_legal_basis(risks: List[Dict[str, Any]], limit: int = 5) -> List[str]:
    out: List[str] = []
    seen = set()
    for item in list(risks or []):
        if not isinstance(item, dict):
            continue
        law_title = str(item.get("law_title") or "").strip()
        article_no = str(item.get("article_no") or "").strip()
        basis = f"{law_title} {article_no}".strip()
        if not basis or basis in seen:
            continue
        seen.add(basis)
        out.append(basis)
        if len(out) >= max(1, int(limit or 1)):
            break
    return out


def _build_episode_payload(
    audit_id: str,
    regulation_pack_id: str,
    regulation_fingerprint: str,
    retrieval_opts: Optional[Dict[str, Any]],
    preview_clauses: List[Dict[str, Any]],
    audit: Dict[str, Any],
    meta: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    now = _utc_now_iso()
    opts = dict(retrieval_opts or {})
    risks = audit.get("risks") if isinstance(audit.get("risks"), list) else []
    first_clause = (preview_clauses or [{}])[0] if preview_clauses else {}
    first_excerpt = _clip(first_clause.get("clause_text")
                          or first_clause.get("text") or "", 220)
    first_risk = risks[0] if risks and isinstance(risks[0], dict) else {}
    confidence_values: List[float] = []
    for item in risks:
        if not isinstance(item, dict):
            continue
        loc = item.get("location") if isinstance(
            item.get("location"), dict) else {}
        score = loc.get("score")
        if score is None:
            continue
        try:
            confidence_values.append(float(score))
        except Exception:
            continue
    confidence = round(sum(confidence_values) /
                       len(confidence_values), 4) if confidence_values else 0.0

    episode_id = f"ep_{uuid.uuid4().hex[:16]}"
    risk_summary = audit.get("risk_summary") if isinstance(
        audit.get("risk_summary"), dict) else {}
    return {
        "memory_id": episode_id,
        "memory_type": "case",
        "audit_id": str(audit_id or ""),
        "contract_type": str(opts.get("contract_type") or ""),
        "industry": str(opts.get("industry") or ""),
        "jurisdiction": str(opts.get("region") or ""),
        "regulation_pack_id": str(regulation_pack_id or ""),
        "regulation_fingerprint": str(regulation_fingerprint or ""),
        "clause_category": str(first_clause.get("clause_path") or ""),
        "clause_text_excerpt": first_excerpt,
        "risk_label": str(first_risk.get("level") or ""),
        "risk_reasoning": _clip(audit.get("summary") or "", 600),
        "legal_basis": _build_legal_basis(risks, limit=5),
        "exception_conditions": "",
        "outcome": "pending",
        "feedback_source": "offline_eval",
        "confidence": confidence,
        "memory_quality_score": 0.5,
        "created_at": now,
        "updated_at": now,
        "superseded_by": "",
        "status": "pending",
        "risk_count": int(len(risks)),
        "risk_summary": risk_summary,
        "meta": {
            "language": str((meta or {}).get("language") or ""),
            "retrieval_mode": str((meta or {}).get("retrieval_mode") or ""),
            "risk_detection_mode": str((meta or {}).get("risk_detection_mode") or ""),
        },
    }


def save_audit_episode(
    cfg: Dict[str, Any],
    audit_id: str,
    regulation_pack_id: str,
    regulation_fingerprint: str,
    retrieval_opts: Optional[Dict[str, Any]],
    preview_clauses: List[Dict[str, Any]],
    audit: Dict[str, Any],
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Stage 1 memory write:
    Save case episode in pending status for later offline promotion.
    """
    if not bool((cfg or {}).get("memory_episode_store_enabled", True)):
        return {"saved": False, "reason": "disabled"}
    payload = _build_episode_payload(
        audit_id=audit_id,
        regulation_pack_id=regulation_pack_id,
        regulation_fingerprint=regulation_fingerprint,
        retrieval_opts=retrieval_opts,
        preview_clauses=preview_clauses,
        audit=audit if isinstance(audit, dict) else {},
        meta=meta if isinstance(meta, dict) else {},
    )
    target = _episode_store_path(cfg)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False) + "\n"
    with _EPISODE_WRITE_LOCK:
        with open(target, "a", encoding="utf-8") as f:
            f.write(line)
    return {
        "saved": True,
        "episode_id": payload.get("memory_id", ""),
        "status": payload.get("status", "pending"),
        "store_path": target,
    }


def _map_feedback_fields(reviewer_status: str) -> Dict[str, Any]:
    status = str(reviewer_status or "").strip().lower()
    # reviewer_status -> outcome / feedback_source / quality delta
    if status == "confirmed":
        return {"outcome": "success", "feedback_source": "user_confirmed", "quality_delta": 0.12}
    if status == "rejected":
        return {"outcome": "failure", "feedback_source": "reviewer_corrected", "quality_delta": -0.20}
    if status in {"downgraded", "exception"}:
        return {"outcome": "disputed", "feedback_source": "reviewer_corrected", "quality_delta": -0.08}
    return {"outcome": "pending", "feedback_source": "offline_eval", "quality_delta": 0.0}


def record_user_feedback(
    cfg: Dict[str, Any],
    issue: Dict[str, Any],
    reviewer_status: str,
    reviewer_note: str = "",
    operator_id: str = "",
    risk_level: str = "",
) -> Dict[str, Any]:
    """
    Feedback loop write-back:
    Persist mapped outcome and quality score update as auditable event.
    """
    if not bool((cfg or {}).get("memory_feedback_store_enabled", True)):
        return {"saved": False, "reason": "disabled"}

    mapped = _map_feedback_fields(reviewer_status)
    base_quality = float((cfg or {}).get(
        "memory_feedback_base_quality", 0.5) or 0.5)
    quality = max(
        0.0, min(1.0, round(base_quality + float(mapped["quality_delta"]), 4)))
    now = _utc_now_iso()
    payload = {
        "memory_id": f"fb_{uuid.uuid4().hex[:16]}",
        "memory_type": "case",
        "issue_id": str((issue or {}).get("id") or ""),
        "contract_id": str((issue or {}).get("contract_document_id") or ""),
        "rule_id": str((issue or {}).get("rule_id") or ""),
        "clause_category": str((issue or {}).get("clause_id") or ""),
        "risk_label": str(risk_level or (issue or {}).get("risk_level") or ""),
        "risk_reasoning": _clip((issue or {}).get("issue_text") or "", 500),
        "outcome": str(mapped["outcome"]),
        "feedback_source": str(mapped["feedback_source"]),
        "confidence": 0.5,
        "memory_quality_score": quality,
        "reviewer_status": str(reviewer_status or "").strip().lower(),
        "reviewer_note": _clip(reviewer_note, 500),
        "operator_id": str(operator_id or ""),
        "created_at": now,
        "updated_at": now,
    }
    target = _feedback_store_path(cfg)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False) + "\n"
    with _EPISODE_WRITE_LOCK:
        with open(target, "a", encoding="utf-8") as f:
            f.write(line)
    return {
        "saved": True,
        "feedback_id": payload["memory_id"],
        "outcome": payload["outcome"],
        "feedback_source": payload["feedback_source"],
        "memory_quality_score": payload["memory_quality_score"],
        "store_path": target,
    }


def recall_failure_patterns(
    cfg: Dict[str, Any],
    query_text: str,
    top_k: int = 3,
    regulation_pack_id: str = "",
    clause_category: str = "",
    jurisdiction: str = "",
    industry: str = "",
    contract_type: str = "",
) -> List[Dict[str, Any]]:
    """
    Recall failure/disputed patterns from feedback memory store.
    """
    target = _feedback_store_path(cfg)
    if not os.path.exists(target):
        return []
    limit = max(1, min(int(top_k or 3), 3))
    q_tokens = set(_tokenize(query_text))
    out: List[Dict[str, Any]] = []
    try:
        with open(target, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return []

    for line in reversed(lines):
        s = str(line or "").strip()
        if not s:
            continue
        try:
            row = json.loads(s)
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        outcome = str(row.get("outcome") or "").strip().lower()
        reviewer_status = str(row.get("reviewer_status") or "").strip().lower()
        if outcome not in {"failure", "disputed"} and reviewer_status not in {"rejected", "downgraded", "exception"}:
            continue

        # Hard filter when row carries same dimension fields.
        if regulation_pack_id:
            row_pack = str(row.get("regulation_pack_id") or "").strip()
            if row_pack and row_pack != str(regulation_pack_id):
                continue
        if clause_category:
            row_cat = str(row.get("clause_category") or "").strip()
            if row_cat and row_cat != str(clause_category):
                continue
        if jurisdiction:
            row_jur = str(row.get("jurisdiction") or "").strip()
            if row_jur and row_jur != str(jurisdiction):
                continue
        if industry:
            row_ind = str(row.get("industry") or "").strip()
            if row_ind and row_ind != str(industry):
                continue
        if contract_type:
            row_ct = str(row.get("contract_type") or "").strip()
            if row_ct and row_ct != str(contract_type):
                continue

        pattern_text = " ".join(
            [
                str(row.get("risk_reasoning") or ""),
                str(row.get("reviewer_note") or ""),
                str(row.get("risk_label") or ""),
                str(row.get("clause_category") or ""),
            ]
        ).strip()
        p_tokens = set(_tokenize(pattern_text))
        overlap = len(q_tokens & p_tokens) if q_tokens else 0
        overlap_ratio = (float(overlap) /
                         float(max(1, len(q_tokens)))) if q_tokens else 0.0
        quality = float(row.get("memory_quality_score") or 0.0)
        confidence = float(row.get("confidence") or 0.0)
        score = round(overlap_ratio * 0.6 + quality *
                      0.3 + confidence * 0.1, 6)
        out.append(
            {
                "memory_id": str(row.get("memory_id") or ""),
                "outcome": outcome or "failure",
                "reviewer_status": reviewer_status,
                "regulation_pack_id": str(row.get("regulation_pack_id") or ""),
                "risk_label": str(row.get("risk_label") or ""),
                "clause_category": str(row.get("clause_category") or ""),
                "pattern_text": _clip(pattern_text, 320),
                "memory_quality_score": quality,
                "confidence": confidence,
                "success_rate": float(row.get("success_rate") or 0.0),
                "created_at": str(row.get("created_at") or ""),
                "score": score,
            }
        )
    out.sort(key=lambda x: (float(x.get("score") or 0.0), float(
        x.get("memory_quality_score") or 0.0)), reverse=True)
    return out[:limit]


def recall_similar_audit_memories(
    cfg: Dict[str, Any],
    query_text: str,
    top_k: int = 3,
    regulation_pack_id: str = "",
    clause_category: str = "",
    jurisdiction: str = "",
    industry: str = "",
    contract_type: str = "",
) -> List[Dict[str, Any]]:
    """
    Recall similar case memories from pending episode store.
    Includes hard filtering and lightweight semantic/rule scoring.
    """
    target = _episode_store_path(cfg)
    if not os.path.exists(target):
        return []
    limit = max(1, min(int(top_k or 3), 5))
    q_tokens = set(_tokenize(query_text))
    out: List[Dict[str, Any]] = []
    try:
        with open(target, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return []

    for line in reversed(lines):
        s = str(line or "").strip()
        if not s:
            continue
        try:
            row = json.loads(s)
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        if str(row.get("memory_type") or "").strip().lower() not in {"case", ""}:
            continue

        # Hard filter first.
        row_pack = str(row.get("regulation_pack_id") or "").strip()
        if regulation_pack_id and row_pack != str(regulation_pack_id):
            continue
        row_cat = str(row.get("clause_category") or "").strip()
        if clause_category and row_cat and row_cat != str(clause_category):
            continue
        row_jur = str(row.get("jurisdiction") or "").strip()
        if jurisdiction and row_jur and row_jur != str(jurisdiction):
            continue
        row_ind = str(row.get("industry") or "").strip()
        if industry and row_ind and row_ind != str(industry):
            continue
        row_ct = str(row.get("contract_type") or "").strip()
        if contract_type and row_ct and row_ct != str(contract_type):
            continue

        memory_text = " ".join(
            [
                str(row.get("clause_text_excerpt") or ""),
                str(row.get("risk_reasoning") or ""),
                str(row.get("risk_label") or ""),
                " ".join(row.get("legal_basis") or []),
            ]
        ).strip()
        m_tokens = set(_tokenize(memory_text))
        overlap = len(q_tokens & m_tokens) if q_tokens else 0
        semantic_sim = (float(overlap) /
                        float(max(1, len(q_tokens)))) if q_tokens else 0.0
        regulation_match = 1.0 if regulation_pack_id and row_pack == str(
            regulation_pack_id) else 0.0
        rule_match = 1.0 if clause_category and row_cat and row_cat == str(
            clause_category) else 0.0
        quality = float(row.get("memory_quality_score") or 0.0)
        confidence = float(row.get("confidence") or 0.0)
        score = round(semantic_sim * 0.55 + regulation_match * 0.25 +
                      rule_match * 0.10 + quality * 0.05 + confidence * 0.05, 6)
        out.append(
            {
                "memory_id": str(row.get("memory_id") or ""),
                "memory_type": "case",
                "regulation_pack_id": row_pack,
                "clause_category": row_cat,
                "risk_label": str(row.get("risk_label") or ""),
                "clause_text_excerpt": _clip(row.get("clause_text_excerpt") or "", 200),
                "risk_reasoning": _clip(row.get("risk_reasoning") or "", 260),
                "legal_basis": list(row.get("legal_basis") or [])[:3],
                "memory_quality_score": quality,
                "confidence": confidence,
                "created_at": str(row.get("created_at") or ""),
                "score": score,
            }
        )
    out.sort(key=lambda x: (float(x.get("score") or 0.0), float(
        x.get("memory_quality_score") or 0.0)), reverse=True)
    return out[:limit]


def recall_workflow_memories(
    cfg: Dict[str, Any],
    query_text: str,
    top_k: int = 2,
    regulation_pack_id: str = "",
    jurisdiction: str = "",
    industry: str = "",
    contract_type: str = "",
) -> List[Dict[str, Any]]:
    """
    Recall workflow strategy memories before audit planning.
    """
    target = _workflow_store_path(cfg)
    if not os.path.exists(target):
        return []
    limit = max(1, min(int(top_k or 2), 3))
    q_tokens = set(_tokenize(query_text))
    out: List[Dict[str, Any]] = []
    try:
        with open(target, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return []

    for line in reversed(lines):
        s = str(line or "").strip()
        if not s:
            continue
        try:
            row = json.loads(s)
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        if str(row.get("memory_type") or "").strip().lower() not in {"workflow", ""}:
            continue
        if str(row.get("status") or "active").strip().lower() not in {"active", ""}:
            continue

        row_pack = str(row.get("regulation_pack_id") or "").strip()
        if regulation_pack_id and row_pack and row_pack != str(regulation_pack_id):
            continue
        row_jur = str(row.get("jurisdiction") or "").strip()
        if jurisdiction and row_jur and row_jur != str(jurisdiction):
            continue
        row_ind = str(row.get("industry") or "").strip()
        if industry and row_ind and row_ind != str(industry):
            continue
        row_ct = str(row.get("contract_type") or "").strip()
        if contract_type and row_ct and row_ct != str(contract_type):
            continue

        text = " ".join(
            [
                str(row.get("workflow_title") or ""),
                str(row.get("workflow_steps") or ""),
                str(row.get("risk_reasoning") or ""),
                str(row.get("clause_category") or ""),
            ]
        ).strip()
        tks = set(_tokenize(text))
        overlap = len(q_tokens & tks) if q_tokens else 0
        semantic_sim = (float(overlap) /
                        float(max(1, len(q_tokens)))) if q_tokens else 0.0
        quality = float(row.get("memory_quality_score") or 0.0)
        confidence = float(row.get("confidence") or 0.0)
        score = round(semantic_sim * 0.6 + quality *
                      0.25 + confidence * 0.15, 6)
        out.append(
            {
                "memory_id": str(row.get("memory_id") or ""),
                "memory_type": "workflow",
                "workflow_title": _clip(row.get("workflow_title") or "", 120),
                "workflow_steps": _clip(row.get("workflow_steps") or "", 320),
                "risk_reasoning": _clip(row.get("risk_reasoning") or "", 220),
                "regulation_pack_id": row_pack,
                "memory_quality_score": quality,
                "confidence": confidence,
                "created_at": str(row.get("created_at") or ""),
                "score": score,
            }
        )
    out.sort(key=lambda x: (float(x.get("score") or 0.0), float(
        x.get("memory_quality_score") or 0.0)), reverse=True)
    return out[:limit]
