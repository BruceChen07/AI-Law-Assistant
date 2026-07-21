import json

from app.services.audit_token_policy import (
    build_contract_audit_budget,
    get_audit_token_policy,
    plan_clause_groups,
)
from app.services.contract_audit import (
    _build_classic_prompt_payload,
    _build_multipass_classic_audit,
)


class _FakeLLM:
    def __init__(self):
        self.calls = 0

    def chat_with_profile(self, messages, task_profile, overrides=None):
        self.calls += 1
        body = {
            "summary": f"round-{self.calls}",
            "risks": [
                {
                    "level": "medium",
                    "issue": f"risk-{self.calls}",
                    "suggestion": "fix",
                    "citation_id": "cid-1",
                    "law_title": "中华人民共和国增值税法",
                    "article_no": "第1条",
                    "evidence": "证据",
                    "confidence": 0.88,
                    "clause_id": f"c{self.calls}",
                }
            ],
        }
        return json.dumps(body, ensure_ascii=False), {
            "usage": {"completion_tokens": 256}
        }


def test_get_audit_token_policy_defaults():
    policy = get_audit_token_policy({})
    assert policy["enabled"] is True
    assert policy["context_window_tokens"] == 16384
    assert policy["reserve_output_tokens"] >= 1024
    assert policy["input_hard_limit_tokens"] > policy["input_soft_limit_tokens"]


def test_build_contract_audit_budget_requires_multi_pass_when_prompt_truncated():
    cfg = {
        "classic_audit_max_tokens": 2048,
        "audit_token_policy": {
            "enabled": True,
            "force_multi_pass_when_prompt_truncated": True,
        },
    }
    preview_clauses = [
        {"clause_id": f"c{i}", "clause_text": "税务条款" * 900, "clause_path": f"第{i}条"}
        for i in range(1, 13)
    ]
    evidence_items = [
        {"citation_id": f"cid-{i}", "law_title": "法条", "article_no": f"第{i}条", "content": "证据" * 700}
        for i in range(1, 30)
    ]
    prompt_payload = _build_classic_prompt_payload(
        cfg=cfg,
        text="合同正文" * 5000,
        lang="zh",
        preview_clauses=preview_clauses,
        evidence_items=evidence_items,
    )
    budget = build_contract_audit_budget(
        cfg,
        full_contract_text="合同正文" * 5000,
        preview_clauses=preview_clauses,
        evidence_items=evidence_items,
        prompt_messages=prompt_payload["messages"],
        prompt_meta=prompt_payload["prompt_meta"],
        requested_output_tokens=2048,
    )
    assert budget["requires_multi_pass"] is True
    assert "clauses_omitted" in budget["reasons"]
    assert "evidence_items_omitted" in budget["reasons"]


def test_plan_clause_groups_preserves_order():
    preview_clauses = [
        {"clause_id": f"c{i}", "clause_text": "税务约定" * 60, "clause_path": f"第{i}条"}
        for i in range(1, 6)
    ]
    plan = plan_clause_groups(
        preview_clauses,
        {
            "clause_group_target_tokens": 200,
            "max_clauses_per_group": 2,
            "max_rounds": 4,
        },
    )
    assert plan["round_count"] >= 3
    assert plan["rounds"][0]["clause_ids"][0] == "c1"
    assert plan["rounds"][1]["clause_ids"][0] in {"c2", "c3"}


def test_build_multipass_classic_audit_returns_rounds_and_merged_risks():
    cfg = {
        "classic_audit_max_tokens": 1024,
        "audit_token_policy": {
            "clause_group_target_tokens": 120,
            "max_clauses_per_group": 1,
            "max_rounds": 4,
            "max_evidence_items_per_round": 4,
        },
        "llm_trace_enabled": False,
    }
    preview_clauses = [
        {"clause_id": "c1", "clause_text": "税务条款一" * 80, "clause_path": "第一条"},
        {"clause_id": "c2", "clause_text": "税务条款二" * 80, "clause_path": "第二条"},
    ]
    evidence_items = [
        {"citation_id": "cid-1", "law_title": "中华人民共和国增值税法", "article_no": "第1条", "content": "证据一", "final_score": 0.9}
    ]
    result = _build_multipass_classic_audit(
        cfg=cfg,
        llm=_FakeLLM(),
        text="合同全文",
        lang="zh",
        preview_clauses=preview_clauses,
        evidence_items=evidence_items,
        retrieval_opts={"audit_mode": "tax"},
        audit_id="audit_x",
        trace_id="trace_x",
        token_budget={
            "policy": get_audit_token_policy(cfg),
            "reasons": ["clauses_omitted"],
        },
    )
    assert result["meta"]["execution_path"] == "multipass_classic_stage1"
    assert result["meta"]["multipass_round_count"] == 2
    assert len(result["raw"]["rounds"]) == 2
    assert len(result["raw"]["chunk_audit_results"]) == 2
    first_chunk = result["raw"]["chunk_audit_results"][0]
    assert first_chunk["chunk_id"] == "chunk-001"
    assert first_chunk["clause_range"]["start_clause_id"] == "c1"
    assert first_chunk["risk_count"] == 1
    assert first_chunk["parse_failed_flag"] is False
    assert first_chunk["truncated_flag"] is False
    assert len(result["audit"]["risks"]) == 2
