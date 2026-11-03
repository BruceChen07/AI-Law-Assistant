import json
from pathlib import Path

from docx import Document
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.routers.contracts as contracts_router
import app.api.routers.contracts.export as export_module
from app.api.dependencies import get_current_user


def _build_client(tmp_path: Path) -> TestClient:
    cfg = {"files_dir": str(tmp_path)}
    app = FastAPI()
    app.include_router(contracts_router.build_router(cfg))
    app.dependency_overrides[get_current_user] = lambda: {
        "id": "u1", "role": "user"}
    return TestClient(app)


def test_export_commented_original_uses_audit_risk_items(tmp_path, monkeypatch):
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
                    "risks": [
                        {
                            "level": "high",
                            "issue": "未明确税率",
                            "suggestion": "补充税率约定",
                            "location": {
                                "clause_id": "c1",
                                "clause_path": "第一条",
                                "quote": "这是合同正文。",
                            },
                        }
                    ],
                    "citations": [],
                },
                ensure_ascii=False,
            )
        }

    def fake_insert(input_path, output_path, risks):
        captured["input_path"] = input_path
        captured["output_path"] = output_path
        captured["risks"] = list(risks)
        Path(output_path).write_bytes(b"fake-docx")

    monkeypatch.setattr(
        export_module, "get_document_by_id_for_user", fake_get_document)
    monkeypatch.setattr(
        export_module, "get_latest_contract_audit_by_document", fake_get_audit)
    monkeypatch.setattr(
        export_module,
        "get_latest_successful_document_digitization_by_document",
        lambda cfg, document_id: None,
    )
    monkeypatch.setattr(
        "app.services.docx_modifier.insert_risk_comments", fake_insert)

    client = _build_client(tmp_path)
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


def test_export_commented_original_falls_back_to_digitized_docx(tmp_path, monkeypatch):
    original_path = tmp_path / "original.pdf"
    original_path.write_bytes(b"%PDF-1.4\nfake")
    digitized_path = tmp_path / "digitized.docx"
    digitized_path.write_bytes(b"fake-docx")

    captured = {}

    def fake_get_document(cfg, document_id, user_id):
        return {
            "id": document_id,
            "file_path": str(original_path),
            "original_filename": "original.pdf",
            "filename": "original.pdf",
        }

    def fake_get_audit(cfg, document_id, status="done"):
        return {
            "result_json": json.dumps({"risks": [], "citations": []}, ensure_ascii=False)
        }

    def fake_insert(input_path, output_path, risks):
        captured["input_path"] = input_path
        Path(output_path).write_bytes(b"fake-docx")

    monkeypatch.setattr(
        export_module, "get_document_by_id_for_user", fake_get_document)
    monkeypatch.setattr(
        export_module, "get_latest_contract_audit_by_document", fake_get_audit)
    monkeypatch.setattr(
        export_module,
        "get_latest_successful_document_digitization_by_document",
        lambda cfg, document_id: {"digitized_docx_path": str(digitized_path)},
    )
    monkeypatch.setattr(
        "app.services.docx_modifier.insert_risk_comments", fake_insert)

    client = _build_client(tmp_path)
    resp = client.post(
        "/contracts/doc-2/report/export",
        json={"export_format": "docx", "export_mode": "comments"},
    )
    assert resp.status_code == 200
    assert captured["input_path"] == str(digitized_path)


def test_export_commented_original_rejects_non_docx_without_digitized(tmp_path, monkeypatch):
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
            "result_json": json.dumps({"risks": [], "citations": []}, ensure_ascii=False)
        }

    monkeypatch.setattr(
        export_module, "get_document_by_id_for_user", fake_get_document)
    monkeypatch.setattr(
        export_module, "get_latest_contract_audit_by_document", fake_get_audit)
    monkeypatch.setattr(
        export_module,
        "get_latest_successful_document_digitization_by_document",
        lambda cfg, document_id: None,
    )

    client = _build_client(tmp_path)
    resp = client.post(
        "/contracts/doc-3/report/export",
        json={"export_format": "docx", "export_mode": "comments"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "digitized word document not found"
