import json
from pathlib import Path

from app.services.memory_promotion import (
    list_pending_rule_memories,
    promote_episode_to_rule,
    review_rule_memory,
)


def _write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_promote_episode_to_rule_queues_high_impact(tmp_path: Path):
    memory_dir = tmp_path / "memory"
    experience_dir = memory_dir / "experience"
    episode_path = experience_dir / "case_episode_pending.jsonl"
    feedback_path = experience_dir / "feedback_events.jsonl"

    episode = {
        "memory_id": "ep_001",
        "memory_type": "case",
        "status": "pending",
        "contract_type": "sales",
        "industry": "retail",
        "jurisdiction": "CN",
        "regulation_pack_id": "rp_A",
        "regulation_fingerprint": "fp_A",
        "clause_category": "invoice.tax",
        "clause_text_excerpt": "发票税率约定不清",
        "risk_label": "medium",
        "risk_reasoning": "建议明确税率与开票时点",
        "legal_basis": ["增值税法 第三条"],
        "exception_conditions": "",
        "confidence": 0.8,
        "memory_quality_score": 0.8,
        "risk_count": 3,
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }
    feedback_rows = [
        {
            "memory_id": "fb_1",
            "regulation_pack_id": "rp_A",
            "clause_category": "invoice.tax",
            "risk_label": "medium",
            "outcome": "success",
            "memory_quality_score": 0.9,
        },
        {
            "memory_id": "fb_2",
            "regulation_pack_id": "rp_A",
            "clause_category": "invoice.tax",
            "risk_label": "medium",
            "outcome": "success",
            "memory_quality_score": 0.88,
        },
    ]
    _write_jsonl(episode_path, [episode])
    _write_jsonl(feedback_path, feedback_rows)

    cfg = {"memory_dir": str(memory_dir)}
    result = promote_episode_to_rule(cfg=cfg, min_support_count=1, min_quality_score=0.6, high_impact_threshold=0.82)
    assert result["ok"] is True
    assert result["promoted_count"] == 1
    assert result["queued_for_approval_count"] == 1

    pending_rows = list_pending_rule_memories(cfg, limit=10)
    assert len(pending_rows) == 1
    assert pending_rows[0]["status"] == "pending_approval"
    assert pending_rows[0]["approval_required"] is True
    assert pending_rows[0]["regulation_pack_id"] == "rp_A"


def test_review_rule_memory_approve_moves_to_active(tmp_path: Path):
    memory_dir = tmp_path / "memory"
    experience_dir = memory_dir / "experience"
    pending_rule_path = experience_dir / "rule_memory_pending_approval.jsonl"
    active_rule_path = experience_dir / "rule_memory_active.jsonl"

    pending_rule = {
        "memory_id": "rm_001",
        "memory_type": "rule",
        "status": "pending_approval",
        "approval_required": True,
        "outcome": "pending",
        "regulation_pack_id": "rp_A",
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }
    _write_jsonl(pending_rule_path, [pending_rule])

    cfg = {"memory_dir": str(memory_dir)}
    result = review_rule_memory(cfg=cfg, rule_id="rm_001", action="approve", reviewer_id="admin_u1", note="ok")
    assert result["ok"] is True
    assert result["status"] == "active"

    pending_rows = list_pending_rule_memories(cfg, limit=10)
    assert pending_rows == []
    with open(active_rule_path, "r", encoding="utf-8") as f:
        rows = [json.loads(x) for x in f.read().splitlines() if x.strip()]
    assert len(rows) == 1
    assert rows[0]["memory_id"] == "rm_001"
    assert rows[0]["status"] == "active"
    assert rows[0]["approval_required"] is False
