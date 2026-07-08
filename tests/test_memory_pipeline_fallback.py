import asyncio
from pathlib import Path

from app.services.contract_audit_modules.memory_pipeline.callbacks import create_memory_callbacks
from app.services.contract_audit_modules.memory_pipeline import callbacks as cb


def _build_callback_args(tmp_path: Path):
    return {
        "cfg": {
            "memory_dir": str(tmp_path / "memory"),
            "local_llm": {
                "cloud_fallback_enabled": True,
                "execution": {
                    "fallback_on_error": True,
                    "fallback_on_invalid_json": True,
                },
            },
        },
        "norm_lang": "zh",
        "lang": "zh",
        "is_relaxed": True,
        "memory_use_long_hits": False,
        "evidence_whitelist_text": "C1: 发票条款",
        "workflow_memory_block": "",
        "workflow_memories": [],
        "global_tax_context_text": "",
        "base_trace_meta": {"audit_id": "a1"},
        "retrieval_opts": {"audit_mode": "rag", "risk_detection_mode": "relaxed"},
        "memory_dir": str(tmp_path / "memory"),
        "preview_order_map": {"c1": 1},
        "preview_priority_orders": [1],
        "preview_priority_clause_ids": {"c1"},
        "llm_budget": {
            "limit": 8,
            "calls": 0,
            "guard_hit": False,
            "skipped_clause_calls": 0,
            "skipped_flush_calls": 0,
            "skipped_low_priority_calls": 0,
            "called_high_priority_clauses": 0,
        },
        "clause_parse_state": {"count": 0, "clause_ids": []},
        "citation_alias_map": {},
        "round_runtime": {"round": 0, "clause_id": ""},
        "write_round": lambda *args, **kwargs: None,
    }


def test_clause_callback_retries_cloud_on_invalid_json(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(cb, "recall_similar_audit_memories", lambda **kwargs: [])
    monkeypatch.setattr(cb, "recall_failure_patterns", lambda **kwargs: [])
    monkeypatch.setattr(cb, "rerank_memory_candidates", lambda items, **kwargs: items)
    monkeypatch.setattr(cb, "apply_context_budget", lambda items, **kwargs: items)

    calls = []

    class FakeLLM:
        def chat_with_profile(self, messages, task_profile, overrides=None):
            payload = {
                "task_profile": task_profile,
                "overrides": dict(overrides or {}),
            }
            calls.append(payload)
            if payload["overrides"].get("_model_role") == "cloud_fallback":
                return (
                    '{"summary":"ok","risks":[{"level":"high","issue":"invoice risk","suggestion":"fix","law_title":"税法","article_no":"第一条","evidence":"quote"}]}',
                    {
                        "usage": {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140},
                        "_route": {"selected_role": "cloud_fallback"},
                    },
                )
            return (
                "not-json",
                {
                    "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
                    "_route": {"selected_role": "main"},
                },
            )

    clause_cb, _flush_cb = create_memory_callbacks(
        llm=FakeLLM(),
        **_build_callback_args(tmp_path),
    )
    result = asyncio.run(
        clause_cb(
            {
                "round": 1,
                "clause": {"clause_id": "c1", "title": "发票", "text": "甲方应开具发票", "clause_path": "1.1"},
                "short_memory": "",
                "long_memory_hits": "",
            }
        )
    )
    assert result["summary"] == "ok"
    assert len(result["risks"]) == 1
    assert any(x["overrides"].get("_model_role") == "cloud_fallback" for x in calls)


def test_flush_callback_retries_cloud_on_exception(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(cb, "recall_similar_audit_memories", lambda **kwargs: [])
    monkeypatch.setattr(cb, "recall_failure_patterns", lambda **kwargs: [])
    monkeypatch.setattr(cb, "rerank_memory_candidates", lambda items, **kwargs: items)
    monkeypatch.setattr(cb, "apply_context_budget", lambda items, **kwargs: items)

    calls = []

    class FakeLLM:
        def chat_with_profile(self, messages, task_profile, overrides=None):
            payload = {
                "task_profile": task_profile,
                "overrides": dict(overrides or {}),
            }
            calls.append(payload)
            if payload["overrides"].get("_model_role") == "cloud_fallback":
                return "- flush cloud", {"_route": {"selected_role": "cloud_fallback"}}
            raise RuntimeError("local flush timeout")

    _clause_cb, flush_cb = create_memory_callbacks(
        llm=FakeLLM(),
        **_build_callback_args(tmp_path),
    )
    result = asyncio.run(flush_cb("需要压缩的记忆内容"))
    assert result == "- flush cloud"
    assert any(x["overrides"].get("_model_role") == "cloud_fallback" for x in calls)
