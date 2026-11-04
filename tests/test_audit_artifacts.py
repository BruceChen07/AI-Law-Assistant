import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.database import init_db
from app.services.audit_artifacts import (
    artifact_root,
    audit_dir,
    list_artifacts,
    rebuild_artifacts_from_db,
    write_audit_artifacts,
)
from app.services.crud import (
    insert_contract_audit,
    insert_document,
    upsert_contract_audit_chunk,
    upsert_contract_audit_progress,
)


def _cfg(tmp_path: Path) -> dict:
    cfg = {
        "db_path": str(tmp_path / "app.db"),
        "data_dir": str(tmp_path / "data"),
        "files_dir": str(tmp_path / "files"),
        "static_dir": str(tmp_path / "static"),
        "llm_trace_enabled": False,
    }
    init_db(cfg)
    return cfg


def _seed_audit(cfg, audit_id="audit-art-1", status="done", document_id="doc-1"):
    insert_document(
        cfg,
        doc_id=document_id,
        filename="contract.docx",
        original_filename="MSA.docx",
        file_path=str(Path(cfg["data_dir"]) / "MSA.docx"),
        file_size=10,
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        user_id="user-1",
        status="active",
        category="contract",
    )
    audit = {
        "summary": "聚合完成",
        "risk_summary": {"high": 1, "medium": 1, "low": 0},
        "risks": [
            {
                "level": "high",
                "issue": "未明确含税价",
                "suggestion": "注明含税价与税率",
                "basis": "增值税法 第一条",
                "citation_id": "law-1",
                "location": {"clause_id": "c1", "clause_path": "第1条"},
            },
            {"level": "medium", "issue": "开票时限缺失", "suggestion": "约定开票期限"},
        ],
    }
    if status == "done":
        insert_contract_audit(
            cfg,
            audit_id=audit_id,
            document_id=document_id,
            status="done",
            result_json=json.dumps(audit, ensure_ascii=False),
            model_provider="llama_cpp",
            model_name="m",
            created_at="2026-11-04T10:00:00+00:00",
        )
    upsert_contract_audit_progress(
        cfg,
        audit_id=audit_id,
        status=status,
        stage="done" if status == "done" else "auditing",
        percent=100 if status == "done" else 40,
        message="completed",
        document_id=document_id,
        user_id="user-1",
        chunks_total=1,
    )
    upsert_contract_audit_chunk(
        cfg,
        audit_id=audit_id,
        chunk_id="chunk-001",
        ordinal=1,
        source_hash="src",
        plan_hash="plan",
        clause_ids=["c1"],
        source_spans=[{"start": 0, "end": 12, "unit_id": "u1", "clause_ids": ["c1"]}],
        input_tokens=100,
        output_tokens=50,
        attempts=1,
        status="ok",
        raw_response="{}",
        parsed_json={"chunk_id": "chunk-001", "risk_count": 1},
    )
    return audit


def _client(cfg):
    from app.api.dependencies import get_current_user
    from app.api.routers import contracts as contracts_router

    app = FastAPI()
    app.include_router(contracts_router.build_router(cfg))
    app.dependency_overrides[get_current_user] = lambda: {
        "id": "user-1", "role": "admin"}
    return TestClient(app)


def test_write_audit_artifacts_creates_readable_files(tmp_path: Path):
    cfg = _cfg(tmp_path)
    audit = _seed_audit(cfg)

    written = write_audit_artifacts(
        cfg,
        audit_id="audit-art-1",
        document_id="doc-1",
        status="done",
        audit=audit,
        meta={"execution_path": "classic_v2", "chunks_total": 1},
        chunks=[{"chunk_id": "chunk-001", "ordinal": 1, "status": "ok"}],
    )

    target = Path(written["dir"])
    assert target == Path(audit_dir(cfg, "audit-art-1"))
    for name in ("report.json", "meta.json", "chunks.jsonl", "report.md"):
        assert (target / name).is_file()

    report = json.loads((target / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "done"
    assert report["audit"]["risk_summary"]["high"] == 1
    assert report["filename"] == "MSA.docx"

    markdown = (target / "report.md").read_text(encoding="utf-8")
    assert "未明确含税价" in markdown
    assert "高风险：1" in markdown

    chunks = [json.loads(line) for line in (target / "chunks.jsonl").read_text(encoding="utf-8").splitlines() if line]
    assert chunks and chunks[0]["chunk_id"] == "chunk-001"

    index_path = Path(artifact_root(cfg)) / "index.jsonl"
    index_rows = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines() if line]
    assert index_rows[-1]["audit_id"] == "audit-art-1"
    assert index_rows[-1]["risk_summary"]["high"] == 1


def test_rebuild_artifacts_from_database_only_audit(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _seed_audit(cfg)

    assert list_artifacts(cfg, "audit-art-1") == []
    written = rebuild_artifacts_from_db(cfg, "audit-art-1")

    assert written
    files = {item["name"] for item in list_artifacts(cfg, "audit-art-1")}
    assert {"report.json", "meta.json", "chunks.jsonl", "report.md"} <= files
    report = json.loads((Path(written["dir"]) / "report.json").read_text(encoding="utf-8"))
    assert report["audit"]["summary"] == "聚合完成"


def test_history_endpoint_lists_finished_and_pending_audits(tmp_path: Path):
    cfg = _cfg(tmp_path)
    audit = _seed_audit(cfg, audit_id="audit-done-1", status="done")
    upsert_contract_audit_progress(
        cfg,
        audit_id="audit-running-1",
        status="running",
        stage="auditing",
        percent=42,
        message="chunk-003",
        document_id="doc-1",
        user_id="user-1",
    )
    write_audit_artifacts(
        cfg,
        audit_id="audit-done-1",
        document_id="doc-1",
        status="done",
        audit=audit,
        meta={},
    )

    response = _client(cfg).get("/contracts/audits/history?limit=10")

    assert response.status_code == 200
    payload = response.json()
    ids = {item["audit_id"] for item in payload["items"]}
    assert {"audit-done-1", "audit-running-1"} <= ids
    done_item = next(item for item in payload["items"] if item["audit_id"] == "audit-done-1")
    assert done_item["status"] == "done"
    assert done_item["risk_summary"]["high"] == 1
    assert done_item["has_result"] is True
    running_item = next(item for item in payload["items"] if item["audit_id"] == "audit-running-1")
    assert running_item["status"] == "running"
    assert running_item["percent"] == 42
    assert payload["artifact_root"].endswith("audits")


def test_artifact_endpoints_list_download_and_rebuild(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _seed_audit(cfg)
    client = _client(cfg)

    missing = client.get("/contracts/audits/audit-art-1/artifacts")
    assert missing.status_code == 200
    assert missing.json()["files"] == []

    rebuilt = client.post("/contracts/audits/audit-art-1/artifacts/rebuild")
    assert rebuilt.status_code == 200
    names = {item["name"] for item in rebuilt.json()["files"]}
    assert {"report.json", "report.md"} <= names

    download = client.get("/contracts/audits/audit-art-1/artifacts/report.md")
    assert download.status_code == 200
    assert "合同审计报告" in download.text

    blocked = client.get("/contracts/audits/audit-art-1/artifacts/../app.db")
    assert blocked.status_code == 404


def test_background_runner_writes_artifacts(tmp_path: Path, monkeypatch):
    from app.services import audit_task_runner
    import app.services.audit_orchestrator as orchestrator

    cfg = _cfg(tmp_path)

    def _fake_pipeline(cfg_arg, services, **kwargs):
        return {
            "audit": {"summary": "ok", "risks": [], "risk_summary": {"high": 0}},
            "meta": {"execution_path": "classic_v2", "chunks_total": 1},
        }

    monkeypatch.setattr(orchestrator, "run_contract_pipeline_bundle", _fake_pipeline)

    audit_task_runner._run(
        cfg=cfg,
        services=None,
        file_path=str(tmp_path / "contract.docx"),
        lang="zh",
        retrieval_options={},
        audit_id="audit-runner-art",
        document_id="doc-9",
        user_id="user-1",
        on_finish=None,
    )

    files = {item["name"] for item in list_artifacts(cfg, "audit-runner-art")}
    assert {"report.json", "meta.json", "chunks.jsonl", "report.md"} <= files
    report = json.loads(
        (Path(audit_dir(cfg, "audit-runner-art")) / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "done"
    assert report["audit"]["summary"] == "ok"
