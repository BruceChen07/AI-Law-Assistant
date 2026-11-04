import json
import math

import pytest

from app.services.contract_audit import _build_v2_classic_audit


class _ExactCounter:
    exact = True

    def count_messages(self, messages):
        chars = sum(len(str(item.get("content") or "")) for item in messages)
        return 16 + math.ceil(chars / 4)


class _FakeLLM:
    def __init__(self, failures=0, *, truncate=False):
        self.failures = failures
        self.truncate = truncate
        self.calls = []

    def chat_with_profile(self, messages, task_profile, overrides=None):
        overrides = dict(overrides or {})
        self.calls.append(
            {
                "messages": messages,
                "task_profile": task_profile,
                "overrides": overrides,
            }
        )
        max_tokens = int(overrides.get("max_tokens") or 0)
        if len(self.calls) <= self.failures:
            return "not-json", {"usage": {"completion_tokens": 12}}
        body = {"summary": "ok", "risks": []}
        completion_tokens = max_tokens if self.truncate else 32
        return json.dumps(body), {
            "usage": {"completion_tokens": completion_tokens}
        }


def _cfg(**optimization_overrides):
    optimization = {
        "enabled": True,
        "context_tokens": 4096,
        "safety_tokens": 256,
        "output_tokens": 512,
        "input_target_tokens": 420,
        "max_chunks": 32,
        "max_attempts_per_chunk": 2,
        "max_retries_per_job": 3,
    }
    optimization.update(optimization_overrides)
    return {
        "audit_optimization": optimization,
        "audit_token_policy": {"max_evidence_items_per_round": 12},
        "llm_trace_enabled": False,
    }


def _text_and_clauses():
    clauses = []
    parts = []
    for index in range(1, 5):
        text = f"第{index}条 税务约定\n" + ("设备销售及开票约定。" * 45)
        parts.append(text)
        clauses.append(
            {
                "clause_id": f"c{index}",
                "clause_path": f"第{index}条",
                "clause_text": text,
            }
        )
    return "\n".join(parts), clauses


def _run(llm, cfg=None):
    text, clauses = _text_and_clauses()
    return _build_v2_classic_audit(
        cfg=cfg or _cfg(),
        llm=llm,
        text=text,
        lang="zh",
        preview_clauses=clauses,
        evidence_items=[
            {
                "citation_id": "law-1",
                "law_title": "中华人民共和国增值税法",
                "article_no": "第一条",
                "content": "依法计算并缴纳增值税。",
            }
        ],
        retrieval_opts={"audit_mode": "tax"},
        audit_id="audit-v2-test",
        trace_id="trace-v2-test",
        token_counter=_ExactCounter(),
    )


def test_v2_executes_every_chunk_and_uses_planned_output_budget():
    llm = _FakeLLM()

    result = _run(llm)

    assert result["meta"]["chunks_total"] > 1
    assert result["meta"]["chunks_completed"] == result["meta"]["chunks_total"]
    assert result["meta"]["coverage_planned"] == 1.0
    assert result["meta"]["coverage_audited"] == 1.0
    assert len(llm.calls) == result["meta"]["chunks_total"]
    assert {call["overrides"]["max_tokens"] for call in llm.calls} == {512}
    assert all(
        item["input_tokens"] + item["output_tokens"] + item["safety_tokens"]
        <= item["context_tokens"]
        for item in result["meta"]["token_budget_round_plan"]
    )


def test_v2_retries_parse_failure_and_records_chunk_attempts():
    llm = _FakeLLM(failures=1)

    result = _run(llm)

    first_round = result["raw"]["rounds"][0]
    assert first_round["attempt_count"] == 2
    assert first_round["retry_index"] == 1
    assert result["meta"]["review_retry_count"] == 1
    assert result["meta"]["coverage_audited"] == 1.0


def test_v2_rejects_job_when_chunk_never_returns_valid_json():
    llm = _FakeLLM(failures=10)

    with pytest.raises(RuntimeError, match="INVALID_MODEL_JSON"):
        _run(llm)

    assert len(llm.calls) == 2


def test_v2_rejects_truncated_chunk_instead_of_marking_complete():
    llm = _FakeLLM(truncate=True)

    with pytest.raises(RuntimeError, match="MODEL_RESPONSE_TRUNCATED"):
        _run(llm)

    assert len(llm.calls) == 2
