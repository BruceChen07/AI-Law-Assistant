import json
import math
from pathlib import Path

import pytest

from app.core.database import init_db
from app.services.contract_audit import _build_v2_classic_audit
from app.services.crud import (
    get_contract_audit_progress,
    list_contract_audit_chunks,
    upsert_contract_audit_progress,
)


class _ExactCounter:
    exact = True

    def count_messages(self, messages):
        chars = sum(len(str(item.get("content") or "")) for item in messages)
        return 16 + math.ceil(chars / 4)


class _FakeLLM:
    def __init__(self, failures=0):
        self.failures = failures
        self.calls = []

    def chat_with_profile(self, messages, task_profile, overrides=None):
        self.calls.append({"messages": messages, "overrides": dict(overrides or {})})
        if len(self.calls) <= self.failures:
            return "not-json", {"usage": {"completion_tokens": 12}}
        return json.dumps({"summary": "ok", "risks": []}), {
            "usage": {"completion_tokens": 32}
        }


class _ExplodingLLM:
    def chat_with_profile(self, messages, task_profile, overrides=None):
        raise AssertionError("LLM must not be called when every chunk is reusable")


def _cfg(tmp_path: Path) -> dict:
    cfg = {
        "db_path": str(tmp_path / "app.db"),
        "data_dir": str(tmp_path / "data"),
        "files_dir": str(tmp_path / "files"),
        "static_dir": str(tmp_path / "static"),
        "llm_trace_enabled": False,
        "audit_optimization": {
            "enabled": True,
            "context_tokens": 4096,
            "safety_tokens": 256,
            "output_tokens": 512,
            "input_target_tokens": 420,
            "max_chunks": 32,
            "max_attempts_per_chunk": 2,
            "max_retries_per_job": 3,
        },
        "audit_token_policy": {"max_evidence_items_per_round": 12},
    }
    init_db(cfg)
    return cfg


def _text_and_clauses():
    clauses = []
    parts = []
    for index in range(1, 5):
        text = f"第{index}条 税务约定\n" + ("设备销售及开票约定。" * 45)
        parts.append(text)
        clauses.append(
            {"clause_id": f"c{index}", "clause_path": f"第{index}条", "clause_text": text}
        )
    return "\n".join(parts), clauses


def _run(cfg, llm, audit_id="audit-v2-test"):
    text, clauses = _text_and_clauses()
    return _build_v2_classic_audit(
        cfg=cfg,
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
        audit_id=audit_id,
        trace_id="trace-v2-test",
        token_counter=_ExactCounter(),
    )


def test_v2_persists_every_chunk_with_plan_and_source_spans(tmp_path: Path):
    cfg = _cfg(tmp_path)
    result = _run(cfg, _FakeLLM())

    rows = list_contract_audit_chunks(cfg, "audit-v2-test")
    assert len(rows) == result["meta"]["chunks_total"]
    assert all(row["status"] == "ok" for row in rows)
    assert {row["plan_hash"] for row in rows} == {result["meta"]["plan_hash"]}
    first = rows[0]
    assert json.loads(first["clause_ids"])
    spans = json.loads(first["source_spans"])
    assert spans and all(span["end"] > span["start"] for span in spans)
    assert first["prompt_hash"]
    assert first["raw_response"]
    assert json.loads(first["parsed_json"])["chunk_id"] == first["chunk_id"]


def test_second_run_reuses_finished_chunks_without_calling_llm(tmp_path: Path):
    cfg = _cfg(tmp_path)
    first = _run(cfg, _FakeLLM())

    second = _run(cfg, _ExplodingLLM(), audit_id="audit-v2-second")

    assert second["meta"]["chunk_reused_count"] == first["meta"]["chunks_total"]
    assert second["meta"]["coverage_audited"] == 1.0
    assert second["meta"]["chunks_completed"] == second["meta"]["chunks_total"]

    # The reused audit still gets its own chunk manifest for traceability.
    second_rows = list_contract_audit_chunks(cfg, "audit-v2-second")
    assert len(second_rows) == second["meta"]["chunks_total"]
    assert all(row["status"] == "ok" for row in second_rows)


def test_failed_chunk_is_persisted_and_not_reused(tmp_path: Path):
    cfg = _cfg(tmp_path)

    with pytest.raises(RuntimeError, match="INVALID_MODEL_JSON"):
        _run(cfg, _FakeLLM(failures=10))

    rows = list_contract_audit_chunks(cfg, "audit-v2-test")
    assert rows
    assert rows[0]["status"] == "parse_failed"
    assert rows[0]["attempts"] == 2
    assert rows[0]["error"] == "parse_failed"
    assert rows[0]["raw_response"] == "not-json"

    retry_llm = _FakeLLM()
    retry_result = _run(cfg, retry_llm, audit_id="audit-v2-retry")
    assert retry_result["meta"]["chunk_reused_count"] == 0
    assert len(retry_llm.calls) == retry_result["meta"]["chunks_total"]


def test_review_pass_re_audits_only_the_flagged_chunk(tmp_path: Path, monkeypatch):
    import app.services.contract_audit as contract_audit

    cfg = _cfg(tmp_path)
    llm = _FakeLLM()

    def _fake_review_plan(**kwargs):
        return {
            "policy": {"max_retry_per_chunk": 1},
            "review_items": [{"chunk_id": "chunk-001", "reason": "truncated"}],
            "retry_chunks": [{"chunk_id": "chunk-001", "reasons": ["truncated"]}],
            "retry_chunk_ids": ["chunk-001"],
            "should_retry": True,
            "review_item_count": 1,
            "initial_reliability_level": "medium",
        }

    monkeypatch.setattr(
        contract_audit, "build_reliability_review_plan", _fake_review_plan
    )

    result = _run(cfg, llm)

    assert len(llm.calls) == result["meta"]["chunks_total"] + 1
    assert result["meta"]["review_retry_applied_count"] == 1
    assert result["meta"]["review_retry_chunk_ids"] == ["chunk-001"]
    logs = result["raw"]["review_retry_logs"]
    assert logs and logs[0]["chunk_id"] == "chunk-001"
    assert logs[0]["retry_performed"] is True
    assert result["meta"]["reliability"]["retry_count"] == 1

    rows = {row["chunk_id"]: row for row in list_contract_audit_chunks(cfg, "audit-v2-test")}
    assert rows["chunk-001"]["attempts"] == 2
    assert rows["chunk-001"]["status"] == "ok"


def test_review_retry_respects_per_chunk_limit(tmp_path: Path, monkeypatch):
    import app.services.contract_audit as contract_audit

    cfg = _cfg(tmp_path)
    llm = _FakeLLM()

    def _fake_review_plan(**kwargs):
        return {
            "policy": {"max_retry_per_chunk": 0},
            "review_items": [{"chunk_id": "chunk-001", "reason": "truncated"}],
            "retry_chunks": [{"chunk_id": "chunk-001", "reasons": ["truncated"]}],
            "retry_chunk_ids": ["chunk-001"],
            "should_retry": True,
            "review_item_count": 1,
            "initial_reliability_level": "medium",
        }

    monkeypatch.setattr(
        contract_audit, "build_reliability_review_plan", _fake_review_plan
    )

    result = _run(cfg, llm)

    assert len(llm.calls) == result["meta"]["chunks_total"]
    logs = result["raw"]["review_retry_logs"]
    assert logs and logs[0]["reason"] == "retry_limit_reached"


def test_audit_progress_roundtrip_survives_memory_loss(tmp_path: Path):
    cfg = _cfg(tmp_path)
    upsert_contract_audit_progress(
        cfg,
        audit_id="audit-progress-1",
        status="running",
        stage="auditing",
        percent=42,
        message="chunk-003",
        detail={"execution_path": "classic_v2"},
        document_id="doc-1",
        user_id="user-1",
        plan_hash="plan-abc",
        source_hash="src-abc",
        chunks_total=25,
    )

    row = get_contract_audit_progress(cfg, "audit-progress-1")
    assert row["status"] == "running"
    assert row["percent"] == 42
    assert row["document_id"] == "doc-1"
    assert row["chunks_total"] == 25

    from app.api.routers.contracts import _audit_progress_from_row

    payload = _audit_progress_from_row(row)
    assert payload["status"] == "running"
    assert payload["progress"] == 42
    assert payload["durable"] is True
    assert payload["detail"]["execution_path"] == "classic_v2"

def _progress_cfg(tmp_path: Path) -> dict:
    cfg = {
        "db_path": str(tmp_path / "api.db"),
        "data_dir": str(tmp_path / "data"),
        "files_dir": str(tmp_path / "files"),
        "static_dir": str(tmp_path / "static"),
        "llm_trace_enabled": False,
    }
    init_db(cfg)
    return cfg


def _api_client(cfg):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.dependencies import get_current_user
    from app.api.routers import contracts as contracts_router

    app = FastAPI()
    app.include_router(contracts_router.build_router(cfg))
    app.dependency_overrides[get_current_user] = lambda: {
        "id": "user-1", "role": "admin"}
    return TestClient(app)


def test_progress_endpoint_falls_back_to_database(tmp_path: Path):
    from app.services.crud import insert_document, upsert_contract_audit_progress

    cfg = _progress_cfg(tmp_path)
    doc_id = insert_document(
        cfg,
        doc_id="doc-1",
        filename="contract.docx",
        original_filename="contract.docx",
        file_path=str(tmp_path / "contract.docx"),
        file_size=10,
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        user_id="user-1",
        status="active",
        category="contract",
    )
    upsert_contract_audit_progress(
        cfg,
        audit_id="audit-db-1",
        status="running",
        stage="auditing",
        percent=55,
        message="chunk-007",
        detail={"execution_path": "classic_v2", "chunks_total": 25},
        document_id=doc_id,
        user_id="user-1",
        plan_hash="plan-1",
        source_hash="src-1",
        chunks_total=25,
    )

    client = _api_client(cfg)
    # The in-memory progress map is empty in this process: this proves the DB fallback.
    progress = client.get("/contracts/audit/audit-db-1/progress")
    assert progress.status_code == 200
    payload = progress.json()
    assert payload["progress"] == 55
    assert payload["stage"] == "auditing"
    assert payload["durable"] is True
    assert payload["detail"]["chunks_total"] == 25

    task = client.get("/contracts/audit/audit-db-1/task")
    assert task.status_code == 200
    task_payload = task.json()
    assert task_payload["status"] == "running"
    assert task_payload["document_id"] == doc_id
    assert task_payload["has_result"] is False

    listing = client.get(f"/contracts/{doc_id}/audits")
    assert listing.status_code == 200
    assert [item["audit_id"] for item in listing.json()["items"]] == ["audit-db-1"]


def test_task_endpoint_returns_persisted_result_after_completion(tmp_path: Path):
    from app.services.crud import (
        insert_contract_audit,
        insert_document,
        upsert_contract_audit_progress,
    )

    cfg = _progress_cfg(tmp_path)
    doc_id = insert_document(
        cfg,
        doc_id="doc-2",
        filename="contract.docx",
        original_filename="contract.docx",
        file_path=str(tmp_path / "contract.docx"),
        file_size=10,
        mime_type="application/pdf",
        user_id="user-1",
        status="active",
        category="contract",
    )
    upsert_contract_audit_progress(
        cfg,
        audit_id="audit-db-2",
        status="running",
        stage="auditing",
        percent=40,
        document_id=doc_id,
        user_id="user-1",
    )
    upsert_contract_audit_progress(
        cfg,
        audit_id="audit-db-2",
        status="done",
        stage="done",
        percent=100,
        message="completed",
        document_id=doc_id,
        user_id="user-1",
    )
    insert_contract_audit(
        cfg,
        audit_id="audit-db-2",
        document_id=doc_id,
        status="done",
        result_json=json.dumps({"risk_summary": {"high": 1}, "risks": [{"level": "high"}]}),
        model_provider="llama_cpp",
        model_name="model-x",
        created_at="2026-11-04T00:00:00",
    )

    client = _api_client(cfg)
    task = client.get("/contracts/audit/audit-db-2/task")
    assert task.status_code == 200
    payload = task.json()
    assert payload["status"] == "done"
    assert payload["has_result"] is True
    assert payload["result"]["risk_summary"]["high"] == 1
    assert payload["document_id"] == doc_id


def test_task_endpoint_404_for_unknown_audit(tmp_path: Path):
    cfg = _progress_cfg(tmp_path)
    client = _api_client(cfg)
    response = client.get("/contracts/audit/missing-audit/task")
    assert response.status_code == 404

def test_review_pass_skips_chunks_already_reviewed_in_an_earlier_run(tmp_path: Path, monkeypatch):
    import app.services.contract_audit as contract_audit

    cfg = _cfg(tmp_path)

    def _always_retry_plan(**kwargs):
        return {
            "policy": {"max_retry_per_chunk": 1},
            "review_items": [{"chunk_id": "chunk-001", "reason": "conflict_detected"}],
            "retry_chunks": [{"chunk_id": "chunk-001", "reasons": ["conflict_detected"]}],
            "retry_chunk_ids": ["chunk-001"],
            "should_retry": True,
            "review_item_count": 1,
            "initial_reliability_level": "medium",
        }

    monkeypatch.setattr(
        contract_audit, "build_reliability_review_plan", _always_retry_plan
    )

    first_llm = _FakeLLM()
    first = _run(cfg, first_llm)
    assert first["meta"]["review_retry_applied_count"] == 1
    rows = {row["chunk_id"]: row for row in list_contract_audit_chunks(cfg, "audit-v2-test")}
    assert int(rows["chunk-001"]["reviewed"]) == 1

    second = _run(cfg, _ExplodingLLM(), audit_id="audit-v2-second")
    assert second["meta"]["chunk_reused_count"] == second["meta"]["chunks_total"]
    assert second["meta"]["review_retry_applied_count"] == 0
    reasons = {log["reason"] for log in second["raw"]["review_retry_logs"]}
    assert reasons == {"already_reviewed_reused"}
