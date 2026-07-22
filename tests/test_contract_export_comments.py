import json
from pathlib import Path

from docx import Document
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.routers.contracts as contracts_router
from app.api.dependencies import get_current_user


def _build_client(tmp_path: Path, monkeypatch):
    cfg = {"files_dir": str(tmp_path)}
    app = FastAPI()
    app.include_router(contracts_router.build_router(cfg))
    app.dependency_overrides[get_current_user] = lambda: {
        "id": "u1", "role": "user"}
    return TestClient(app)


def test_export_commented_original_uses_final_report_risk_items(tmp_path, monkeypatch):
    original_path = tmp_path / "original.docx"
    doc = Document()
    doc.add_paragraph("这是合同正文。")
    doc.save(str(original_path))

    captured = {}

    def fake_get_document(cfg, document_id, user_id):
        return {
            "id": document_id,
            "file_path": str(original_path),
            "original_filename": "original.docx",
            "filename": "original.docx",
        }

    def fake_get_audit(cfg, document_id, status="done"):
        return {
            "result_json": json.dumps(
                {
                    "final_report": {
                        "contract_id": document_id,
                        "risk_items": [
                            {
                                "issue_id": "r1",
                                "risk_level": "high",
                                "issue_text": "未明确税率",
                                "suggestion": "补充税率约定",
                                "clause": {
                                    "clause_id": "c1",
                                    "clause_path": "第一条",
                                    "clause_text": "这是合同正文。",
                                },
                            }
                        ],
                    }
                },
                ensure_ascii=False,
            )
        }

    def fake_insert(input_path, output_path, risks):
        captured["input_path"] = input_path
        captured["output_path"] = output_path
        captured["risks"] = list(risks)
        Path(output_path).write_bytes(b"fake-docx")

    monkeypatch.setattr(contracts_router, "get_document_by_id_for_user", fake_get_document)
    monkeypatch.setattr(contracts_router, "get_latest_contract_audit_by_document", fake_get_audit)
    monkeypatch.setattr("app.services.docx_modifier.insert_risk_comments", fake_insert)

    client = _build_client(tmp_path, monkeypatch)
    resp = client.post(
        "/contracts/doc-1/report/export",
        json={"export_format": "docx", "export_mode": "comments"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert captured["input_path"] == str(original_path)
    assert len(captured["risks"]) == 1
    assert captured["risks"][0]["issue_text"] == "未明确税率"


def test_export_commented_original_rejects_non_docx_source(tmp_path, monkeypatch):
    original_path = tmp_path / "original.pdf"
    original_path.write_bytes(b"%PDF-1.4\nfake")

    def fake_get_document(cfg, document_id, user_id):
        return {
            "id": document_id,
            "file_path": str(original_path),
            "original_filename": "original.pdf",
            "filename": "original.pdf",
        }

    def fake_get_audit(cfg, document_id, status="done"):
        return {
            "result_json": json.dumps(
                {"final_report": {"contract_id": document_id, "risk_items": []}},
                ensure_ascii=False,
            )
        }

    monkeypatch.setattr(contracts_router, "get_document_by_id_for_user", fake_get_document)
    monkeypatch.setattr(contracts_router, "get_latest_contract_audit_by_document", fake_get_audit)

    client = _build_client(tmp_path, monkeypatch)
    resp = client.post(
        "/contracts/doc-2/report/export",
        json={"export_format": "docx", "export_mode": "comments"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "commented original export only supports docx source files"
