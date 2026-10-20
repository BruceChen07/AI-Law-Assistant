import base64
import json
import sqlite3
from pathlib import Path

from docx import Document

import app.services.document_digitization as dd
from app.core.utils import extract_text_with_config


_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7Z0eQAAAAASUVORK5CYII="
)


def _base_cfg(tmp_path: Path):
    return {
        "db_path": str(tmp_path / "app.db"),
        "files_dir": str(tmp_path / "files"),
        "ocr_enabled": False,
    }


def _init_digitization_table(db_path: str):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS document_digitization(
                id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                version_no INTEGER NOT NULL,
                status TEXT NOT NULL,
                source_file_path TEXT NOT NULL,
                source_ext TEXT NOT NULL,
                digitized_docx_path TEXT,
                artifact_dir TEXT,
                md_path TEXT,
                middle_path TEXT,
                content_list_path TEXT,
                ocr_engine TEXT,
                ocr_used INTEGER NOT NULL DEFAULT 0,
                layout_retained INTEGER NOT NULL DEFAULT 0,
                source_text_length INTEGER NOT NULL DEFAULT 0,
                digitized_text_length INTEGER NOT NULL DEFAULT 0,
                text_match_ratio REAL NOT NULL DEFAULT 0,
                paragraph_count INTEGER NOT NULL DEFAULT 0,
                table_count INTEGER NOT NULL DEFAULT 0,
                image_count INTEGER NOT NULL DEFAULT 0,
                page_count INTEGER NOT NULL DEFAULT 0,
                note TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def test_rebuild_docx_from_mineru_artifacts_preserves_text_and_table(tmp_path):
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    content_list_path = artifact_dir / "content_list.json"
    middle_path = artifact_dir / "middle.json"
    image_path = artifact_dir / "stamp.png"
    output_docx = tmp_path / "rebuilt.docx"

    image_path.write_bytes(_PNG_1X1)
    content_list_path.write_text(
        json.dumps(
            [
                {
                    "page_idx": 0,
                    "type": "title",
                    "text": "Service Agreement",
                    "text_level": 1,
                    "bbox": [48, 40, 320, 24],
                },
                {
                    "page_idx": 0,
                    "type": "text",
                    "text": "Article 1 Payment Terms",
                    "bbox": [56, 90, 360, 20],
                },
                {
                    "page_idx": 0,
                    "type": "table",
                    "bbox": [56, 130, 380, 120],
                    "table_caption": ["Payment Schedule"],
                    "table_body": (
                        "<table>"
                        "<tr><th>Item</th><th>Amount</th></tr>"
                        "<tr><td>Deposit</td><td>100</td></tr>"
                        "</table>"
                    ),
                    "table_footnote": ["All amounts are in CNY."],
                },
                {
                    "page_idx": 0,
                    "type": "image",
                    "bbox": [56, 280, 180, 120],
                    "img_path": "stamp.png",
                    "image_caption": ["Company Seal"],
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    middle_path.write_text(
        json.dumps({"pdf_info": [{"page_size": [595.0, 842.0]}]}),
        encoding="utf-8",
    )

    stats = dd.rebuild_docx_from_mineru_artifacts(
        content_list_path=str(content_list_path),
        middle_path=str(middle_path),
        output_docx_path=str(output_docx),
        title="Rebuilt Contract",
    )
    text, meta = extract_text_with_config(
        {"ocr_enabled": False}, str(output_docx))

    assert output_docx.exists()
    assert meta["ext"] == ".docx"
    assert stats["table_count"] == 1
    assert stats["image_count"] == 1
    assert "Service Agreement" in text
    assert "Article 1 Payment Terms" in text
    assert "Item\tAmount" in text
    assert "Deposit\t100" in text
    assert "All amounts are in CNY." in text


def test_ensure_digitized_docx_reuses_cached_success_record(tmp_path, monkeypatch):
    cfg = _base_cfg(tmp_path)
    Path(cfg["files_dir"]).mkdir(parents=True, exist_ok=True)
    _init_digitization_table(cfg["db_path"])

    source_pdf = tmp_path / "source.pdf"
    source_pdf.write_bytes(b"%PDF-1.7 fake")
    calls = {"extract": 0}

    def fake_probe_mineru_capabilities(force_refresh=True):
        return {"mineru_cli_found": True}

    def fake_run_mineru_extract(_source_file_path, output_dir):
        calls["extract"] += 1
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        content_list_path = output / "content_list.json"
        middle_path = output / "middle.json"
        md_path = output / "result.md"
        content_list_path.write_text("[]", encoding="utf-8")
        middle_path.write_text("{}", encoding="utf-8")
        md_path.write_text("# result", encoding="utf-8")
        return {
            "content_list_path": str(content_list_path),
            "middle_path": str(middle_path),
            "md_path": str(md_path),
            "text": "Digitized text",
        }

    def fake_rebuild_docx_from_mineru_artifacts(*, output_docx_path, **_kwargs):
        doc = Document()
        doc.add_paragraph("Digitized text")
        doc.save(output_docx_path)
        return {
            "paragraph_count": 1,
            "table_count": 0,
            "image_count": 0,
            "page_count": 1,
        }

    monkeypatch.setattr(dd, "probe_mineru_capabilities",
                        fake_probe_mineru_capabilities)
    monkeypatch.setattr(dd, "run_mineru_extract", fake_run_mineru_extract)
    monkeypatch.setattr(dd, "rebuild_docx_from_mineru_artifacts",
                        fake_rebuild_docx_from_mineru_artifacts)
    monkeypatch.setattr(dd, "extract_text_with_config", lambda _cfg, _path: (
        "Digitized text", {"ext": ".docx"}))

    first = dd.ensure_digitized_docx(
        cfg,
        document_id="doc-1",
        source_file_path=str(source_pdf),
        original_filename="source.pdf",
    )
    second = dd.ensure_digitized_docx(
        cfg,
        document_id="doc-1",
        source_file_path=str(source_pdf),
        original_filename="source.pdf",
    )

    assert first["status"] == "done"
    assert first["digitization_used"] is True
    assert first["reused"] is False
    assert first["text_match_ratio"] == 1.0
    assert Path(first["digitized_docx_path"]).exists()
    assert second["reused"] is True
    assert second["digitization_used"] is True
    assert calls["extract"] == 1
