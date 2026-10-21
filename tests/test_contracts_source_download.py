import io
import json
import re
import zipfile
from datetime import datetime
from uuid import uuid4

from fastapi.testclient import TestClient
from docx import Document

import app.api.routers.contracts as contracts_router
from app.api.routers.contracts import export as contracts_export
from app.api.routers.contracts import source as contracts_source
from app.api.routers.contracts import summary as contracts_summary
from app.main import app


client = TestClient(app)


def _login_headers():
    username = f"source_user_{uuid4().hex[:8]}"
    password = "adminpassword"
    login_resp = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    if login_resp.status_code != 200:
        client.post(
            "/api/auth/register",
            json={
                "username": username,
                "password": password,
                "email": f"{username}@example.com",
            },
        )
        login_resp = client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
        )
    token = login_resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_download_contract_source_returns_original_file(tmp_path, monkeypatch):
    source_path = tmp_path / "contract.pdf"
    source_path.write_bytes(b"%PDF-source")
    monkeypatch.setattr(
        contracts_source,
        "get_document_by_id_for_user",
        lambda *_args, **_kwargs: {
            "file_path": str(source_path),
            "original_filename": "contract.pdf",
            "filename": "contract.pdf",
            "mime_type": "application/pdf",
        },
    )

    resp = client.get("/contracts/doc-source/source", headers=_login_headers())

    assert resp.status_code == 200
    assert resp.content == b"%PDF-source"
    assert "contract.pdf" in resp.headers.get("content-disposition", "")


def test_download_contract_source_returns_digitized_variant(tmp_path, monkeypatch):
    original_path = tmp_path / "contract.pdf"
    digitized_path = tmp_path / "contract-digitized.docx"
    original_path.write_bytes(b"%PDF-source")
    digitized_path.write_bytes(b"docx-content")
    monkeypatch.setattr(
        contracts_source,
        "get_document_by_id_for_user",
        lambda *_args, **_kwargs: {
            "file_path": str(original_path),
            "original_filename": "contract.pdf",
            "filename": "contract.pdf",
            "mime_type": "application/pdf",
        },
    )
    monkeypatch.setattr(
        contracts_source,
        "get_latest_successful_document_digitization_by_document",
        lambda *_args, **_kwargs: {
            "digitized_docx_path": str(digitized_path),
        },
    )

    resp = client.get(
        "/contracts/doc-source/source?variant=digitized",
        headers=_login_headers(),
    )

    assert resp.status_code == 200
    assert resp.content == b"docx-content"
    assert "contract_digitized_source_doc-source.docx" in resp.headers.get(
        "content-disposition", ""
    )


def test_export_contract_report_comments_uses_digitized_docx_for_pdf_source(tmp_path, monkeypatch):
    original_path = tmp_path / "contract.pdf"
    digitized_path = tmp_path / "contract-digitized.docx"
    original_path.write_bytes(b"%PDF-source")

    doc = Document()
    doc.add_paragraph("供应商逾期未办理退货，每日按退货金额5%收取仓储保管费。")
    doc.save(digitized_path)

    monkeypatch.setattr(
        contracts_export,
        "get_document_by_id_for_user",
        lambda *_args, **_kwargs: {
            "file_path": str(original_path),
            "original_filename": "contract.pdf",
            "filename": "contract.pdf",
            "mime_type": "application/pdf",
        },
    )
    monkeypatch.setattr(
        contracts_export,
        "get_latest_successful_document_digitization_by_document",
        lambda *_args, **_kwargs: {
            "digitized_docx_path": str(digitized_path),
        },
    )
    monkeypatch.setattr(
        contracts_export,
        "get_latest_contract_audit_by_document",
        lambda *_args, **_kwargs: {
            "result_json": json.dumps(
                {
                    "risks": [
                        {
                            "level": "high",
                            "issue": "仓储保管费费率过高",
                            "suggestion": "建议调整为合理损失区间，并增加处置期限。",
                            "evidence": "每日按退货金额5%收取仓储保管费",
                            "location": {
                                "page_no": 14,
                                "paragraph_no": "129",
                                "clause_id": "c129",
                                "clause_path": "9.2",
                                "quote": "供应商逾期未办理退货，每日按退货金额5%收取仓储保管费。",
                            },
                        }
                    ],
                    "citations": [],
                },
                ensure_ascii=False,
            )
        },
    )
    monkeypatch.setitem(app.state.cfg, "files_dir", str(tmp_path))

    resp = client.post(
        "/contracts/doc-export/report/export",
        headers=_login_headers(),
        json={
            "export_format": "docx",
            "export_mode": "comments",
            "locale": "zh-CN",
        },
    )

    assert resp.status_code == 200
    content_disposition = resp.headers.get("content-disposition", "")
    assert re.search(r'contract_\d{8}_\d{6}\.docx',
                     content_disposition), content_disposition
    assert "doc-export" not in content_disposition
    assert "contract_with_comments" not in content_disposition

    with zipfile.ZipFile(io.BytesIO(resp.content), "r") as zf:
        file_list = zf.namelist()
        assert "word/comments.xml" in file_list
        comments_xml = zf.read("word/comments.xml").decode("utf-8")
        document_xml = zf.read("word/document.xml").decode("utf-8")
        assert "仓储保管费费率过高" in comments_xml
        assert "建议调整为合理损失区间，并增加处置期限。" in comments_xml
        assert "w:commentReference" in document_xml


def test_build_readable_export_filename_strips_ext_and_sanitizes():
    ts = datetime(2026, 10, 20, 14, 30, 25)
    builder = contracts_export._build_readable_export_filename
    assert builder("合同名称.docx", ts, "docx") == "合同名称_20261020_143025.docx"
    assert builder(
        "华润万家购销合同2026年修订-可编辑文字版.docx", ts, "docx"
    ) == "华润万家购销合同2026年修订-可编辑文字版_20261020_143025.docx"
    assert builder("a/b:c*d?.docx", ts,
                   "docx") == "a_b_c_d_20261020_143025.docx"
    assert builder("", ts, "docx") == "contract_20261020_143025.docx"
    assert builder("???", ts, "docx") == "contract_20261020_143025.docx"


def test_get_contract_summary_returns_structured_summary(tmp_path, monkeypatch):
    source_path = tmp_path / "contract.docx"
    source_path.write_bytes(b"docx-source")

    monkeypatch.setattr(
        contracts_summary,
        "get_document_by_id_for_user",
        lambda *_args, **_kwargs: {
            "file_path": str(source_path),
            "original_filename": "demo-contract.docx",
            "filename": "demo-contract.docx",
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        },
    )
    monkeypatch.setattr(
        contracts_summary,
        "extract_text_with_config",
        lambda *_args, **_kwargs: (
            "第一条 供货。第二条 付款与开票。",
            {"page_count": 2},
        ),
    )
    monkeypatch.setattr(
        contracts_summary,
        "_build_preview_clauses",
        lambda _text: [
            {"clause_path": "第一条", "clause_text": "供货。"},
            {"clause_path": "第二条", "clause_text": "付款与开票。"},
        ],
    )
    monkeypatch.setattr(
        contracts_summary,
        "_build_contract_summary",
        lambda **_kwargs: {
            "enabled": True,
            "status": "partial",
            "title": "demo-contract",
            "about": "这是供货合同。",
            "core": "约定付款与开票。",
            "tax": "需核对发票口径。",
            "source_file": "demo-contract.docx",
            "source": "system_default",
            "prompt_id": "contract_audit.contract_summary",
            "issues": ["output_truncated"],
            "truncated": True,
            "cache_hit": False,
            "source_chars": 8000,
            "map_reduce": True,
            "map_chunks": 3,
        },
    )

    resp = client.get(
        "/contracts/doc-summary/summary?language=zh",
        headers=_login_headers(),
    )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["document_id"] == "doc-summary"
    assert payload["summary"]["title"] == "demo-contract"
    assert payload["summary"]["about"] == "这是供货合同。"
    assert payload["summary"]["core"] == "约定付款与开票。"
    assert payload["summary"]["tax"] == "需核对发票口径。"
    assert payload["summary"]["source_file"] == "demo-contract.docx"
    assert payload["meta"]["contract_summary_enabled"] is True
    assert payload["meta"]["contract_summary_status"] == "partial"
    assert payload["meta"]["contract_summary_language"] == "zh"
    assert payload["meta"]["preview_clause_total"] == 2
    assert payload["meta"]["contract_summary_issues"] == ["output_truncated"]
    assert payload["meta"]["contract_summary_truncated"] is True
    assert payload["meta"]["contract_summary_source_chars"] == 8000
    assert payload["meta"]["contract_summary_cache_hit"] is False
    assert payload["meta"]["contract_summary_map_reduce"] is True
    assert payload["meta"]["contract_summary_map_chunks"] == 3
