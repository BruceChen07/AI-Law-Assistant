import json

from app.services.review_retry import (
    build_reliability_review_plan,
    build_reliability_summary,
)
from app.services.contract_audit import _build_multipass_classic_audit
from app.services.audit_token_policy import get_audit_token_policy


class _RetryAwareFakeLLM:
    def __init__(self):
        self.chunk_calls = {}

    def chat_with_profile(self, messages, task_profile, overrides=None):
        text = messages[1]["content"] if len(messages) > 1 else ""
        if "第一条" in text:
            key = "c1"
        elif "第二条" in text:
            key = "c2"
        else:
            key = "other"
        self.chunk_calls[key] = self.chunk_calls.get(key, 0) + 1
        if key == "c1" and self.chunk_calls[key] == 1:
            return "{bad json", {"usage": {"completion_tokens": 1024}}
        body = {
            "summary": f"{key}-{self.chunk_calls[key]}",
            "risks": [
                {
                    "level": "medium",
                    "issue": f"{key}-risk",
                    "suggestion": "fix",
                    "citation_id": "cid-1",
                    "law_title": "中华人民共和国增值税法",
                    "article_no": "第1条",
                    "evidence": "证据",
                    "confidence": 0.92,
                    "clause_id": key,
                }
            ],
        }
        return json.dumps(body, ensure_ascii=False), {"usage": {"completion_tokens": 256}}


def test_build_reliability_review_plan_targets_chunks_from_review_items_and_conflicts():
    chunk_results = [
        {
            "chunk_id": "chunk-001",
            "clause_range": {"clause_ids": ["c1"]},
        },
        {
            "chunk_id": "chunk-002",
            "clause_range": {"clause_ids": ["c2"]},
        },
    ]
    aggregated_audit = {
        "legal_validation": {
            "issues": [
                {"chunk_id": "chunk-001", "reason": "parse_failed", "severity": "high"},
                {"clause_id": "c2", "reason": "high_risk_missing_citation", "severity": "high"},
            ]
        }
    }
    aggregated_meta = {
        "removed_conflicts": [
            {"clause_id": "c1", "counter_clause_id": "c2"}
        ]
    }
    out = build_reliability_review_plan(
        cfg={},
        aggregated_audit=aggregated_audit,
        aggregated_meta=aggregated_meta,
        chunk_audit_results=chunk_results,
    )
    assert out["should_retry"] is True
    assert set(out["retry_chunk_ids"]) == {"chunk-001", "chunk-002"}


def test_build_reliability_summary_returns_medium_when_retry_clears_issues():
    summary = build_reliability_summary(
        review_plan={"review_items": [{"reason": "parse_failed"}]},
        retry_logs=[{"retry_performed": True}],
        final_audit={"legal_validation": {"issues": []}},
    )
    assert summary["retry_count"] == 1
    assert summary["unresolved_review_item_count"] == 0
    assert summary["reliability_level"] == "medium"


def test_build_multipass_classic_audit_retries_only_failed_chunk():
    cfg = {
        "classic_audit_max_tokens": 1024,
        "llm_trace_enabled": False,
        "audit_token_policy": {
            "clause_group_target_tokens": 120,
            "max_clauses_per_group": 1,
            "max_rounds": 4,
            "max_evidence_items_per_round": 4,
        },
        "audit_reliability_policy": {
            "enabled": True,
            "retry_enabled": True,
            "max_retry_per_chunk": 1,
            "max_retry_chunks": 3,
        },
    }
    preview_clauses = [
        {"clause_id": "c1", "clause_text": "税务条款一" * 80, "clause_path": "第一条"},
        {"clause_id": "c2", "clause_text": "税务条款二" * 80, "clause_path": "第二条"},
    ]
    evidence_items = [
        {"citation_id": "cid-1", "law_title": "中华人民共和国增值税法", "article_no": "第1条", "content": "证据一", "final_score": 0.9}
    ]
    llm = _RetryAwareFakeLLM()
    result = _build_multipass_classic_audit(
        cfg=cfg,
        llm=llm,
        text="合同全文",
        lang="zh",
        preview_clauses=preview_clauses,
        evidence_items=evidence_items,
        retrieval_opts={"audit_mode": "tax"},
        audit_id="audit_retry",
        trace_id="trace_retry",
        token_budget={
            "policy": get_audit_token_policy(cfg),
            "reasons": ["clauses_omitted"],
        },
    )
    assert result["meta"]["review_retry_count"] == 1
    assert result["meta"]["reliability_level"] == "medium"
    assert llm.chunk_calls["c1"] == 2
    assert llm.chunk_calls["c2"] == 1
    retry_logs = result["raw"]["review_retry_logs"]
    assert len(retry_logs) == 1
    assert retry_logs[0]["chunk_id"] == "chunk-001"
    assert retry_logs[0]["before_parse_failed"] is True
    assert retry_logs[0]["after_parse_failed"] is False
