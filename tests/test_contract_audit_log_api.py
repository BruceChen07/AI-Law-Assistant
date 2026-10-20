import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.routers.contracts as contracts_router
from app.api.dependencies import (
    get_app_embedder,
    get_app_llm,
    get_app_reranker,
    get_app_translator,
    get_current_user,
)
from app.api.routers.contract_audit_logs import build_router as build_contract_audit_logs_router
from app.api.routers.contracts import build_router as build_contracts_router
from app.services.contract_audit_stage_log import (
    init_contract_audit_log_db,
    query_contract_audit_stage_logs,
    register_contract_audit_log_context,
    write_contract_audit_stage_log,
)


def _cfg(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return {
        "data_dir": str(data_dir),
        "db_path": str(data_dir / "app.db"),
        "contract_audit_log_db_path": str(data_dir / "audit_logs" / "contract_audit_stage_logs.db"),
        "contract_audit_log_export_dir": str(data_dir / "exports" / "contract_audit_logs"),
        "llm_config": {"provider": "test", "model": "unit-test"},
        "ui_config": {},
    }


def _auth_overrides(app: FastAPI, user: dict):
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_app_llm] = lambda: object()
    app.dependency_overrides[get_app_embedder] = lambda: object()
    app.dependency_overrides[get_app_reranker] = lambda: object()
    app.dependency_overrides[get_app_translator] = lambda: object()


def test_contract_audit_log_query_and_export_api(tmp_path):
    cfg = _cfg(tmp_path)
    init_contract_audit_log_db(cfg)
    register_contract_audit_log_context(
        "audit-api-1",
        document_id="doc-api-1",
        contract_name="API Visible Contract",
        operator_id="user-api",
        ip_address="127.0.0.1",
        source_endpoint="/contracts/audit",
    )
    write_contract_audit_stage_log(
        cfg,
        audit_id="audit-api-1",
        stage_code="done",
        stage_status="done",
        stage_result="success",
        stage_message="completed",
        audit_opinion="review complete",
        occurred_at_utc="2026-10-16T07:00:00.000Z",
        payload={"progress": 100},
        event_key="api-visible-done",
    )

    app = FastAPI()
    app.include_router(build_contract_audit_logs_router(cfg))
    _auth_overrides(app, {"id": "user-api", "role": "user"})
    client = TestClient(app)

    resp = client.get("/contract-audit-logs",
                      params={"contract_name": "visible", "stage": "done"})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 1
    assert payload["items"][0]["contract_name"] == "API Visible Contract"

    export_resp = client.get("/contract-audit-logs/export",
                             params={"stage": "done", "export_format": "csv"})
    assert export_resp.status_code == 200
    assert "attachment" in export_resp.headers.get("content-disposition", "")
    assert "API Visible Contract" in export_resp.text


def test_contract_audit_route_writes_stage_logs_automatically(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    init_contract_audit_log_db(cfg)
    with sqlite3.connect(cfg["db_path"]) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS upload_log(
                file_id TEXT PRIMARY KEY,
                original_filename TEXT NOT NULL,
                md5_hash TEXT,
                file_path TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                engine TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT
            )
        """)
        conn.commit()

    def _fake_pipeline(*_args, **kwargs):
        progress_cb = kwargs.get("progress_cb")
        if callable(progress_cb):
            progress_cb("extracting", 15, "extracting text")
            progress_cb("retrieval", 40, "retrieving evidence")
            progress_cb("audit_done", 90, "audit complete")
        return {
            "audit": {"summary": "overall acceptable", "risks": []},
            "meta": {"text_length": 256, "page_count": 3},
            "risk_summary": {"high": 0, "medium": 0, "low": 0},
        }

    monkeypatch.setattr(
        contracts_router, "run_contract_pipeline_bundle", _fake_pipeline)
    monkeypatch.setattr(
        contracts_router,
        "insert_document",
        lambda *_args, **kwargs: kwargs.get("doc_id") or "doc-route",
    )
    monkeypatch.setattr(
        contracts_router, "insert_contract_audit", lambda *_args, **_kwargs: None)

    app = FastAPI()
    app.include_router(build_contracts_router(cfg))
    _auth_overrides(app, {"id": "user-route", "role": "user"})
    client = TestClient(app)

    response = client.post(
        "/contracts/audit",
        files={"file": ("demo.docx", b"fake-docx-bytes",
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        data={"title": "Route Audit Contract", "language": "zh"},
    )

    assert response.status_code == 200
    body = response.json()
    result = query_contract_audit_stage_logs(
        cfg, operator_id="user-route", audit_id=body["audit_id"], limit=20)
    stage_codes = {item["stage_code"] for item in result["items"]}
    assert {"received", "saved", "document", "extracting",
            "retrieval", "audit_done", "done"}.issubset(stage_codes)
    done_rows = [item for item in result["items"]
                 if item["stage_code"] == "done"]
    assert done_rows
    assert done_rows[0]["audit_opinion"] == "overall acceptable"
