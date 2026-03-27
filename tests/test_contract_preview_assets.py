from pathlib import Path

import app.services.contract_preview_assets as cpa


def _base_cfg(tmp_path: Path):
    return {
        "files_dir": str(tmp_path / "files"),
        "contract_preview": {
            "enabled": True,
            "cache_dir": str(tmp_path / "cache"),
            "pdf_dpi": 120,
            "pdf_max_pages": 3,
            "text_lines_per_page": 40,
            "docx_visual_enabled": True,
            "coord_provider": "mineru",
            "docx_coord_from_pdf": True,
            "docx_pdf_min_text_ratio": 0.35,
            "docx_pdf_gate_min_chars": 20,
            "docx_pdf_keep_file": False,
        },
    }


def test_docx_pdf_mineru_path_used_when_quality_gate_passes(tmp_path, monkeypatch):
    cfg = _base_cfg(tmp_path)
    files_dir = Path(cfg["files_dir"])
    files_dir.mkdir(parents=True, exist_ok=True)
    docx_path = files_dir / "demo.docx"
    docx_path.write_bytes(b"fake-docx")

    def fake_convert(docx_path, output_pdf):
        Path(output_pdf).write_bytes(b"%PDF-1.7")
        return output_pdf, "docx2pdf"

    def fake_render_pdf_pages(_file_path, _out_dir, _dpi, _max_pages):
        return [{"page_no": 1, "width": 800, "height": 1200, "blocks": [], "image_file": ""}]

    def fake_build_blocks_from_mineru(file_path, out_dir, max_pages):
        blocks = {
            1: [{
                "block_id": "p1-m1",
                "text": "invoice clause",
                "bbox": [0.1, 0.1, 0.4, 0.05],
                "bbox_pt": [60, 60, 240, 30],
                "coord_unit": "pt",
                "coord_origin": "top_left",
                "is_heading": False,
            }]
        }
        pages_meta = {1: {"page_width_pt": 595.0, "page_height_pt": 842.0}}
        mineru_meta = {"page_count": 1,
                       "block_count": 1, "markdown_text_length": 80}
        return blocks, pages_meta, mineru_meta

    monkeypatch.setattr(cpa, "_convert_docx_to_pdf", fake_convert)
    monkeypatch.setattr(cpa, "_render_pdf_pages", fake_render_pdf_pages)
    monkeypatch.setattr(cpa, "_build_blocks_from_mineru",
                        fake_build_blocks_from_mineru)
    monkeypatch.setattr(cpa, "_extract_docx_plain_text", lambda _p: "x" * 100)

    manifest = cpa.build_contract_preview_manifest(
        cfg=cfg,
        document_id="d1",
        file_path=str(docx_path),
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    assert manifest["mode"] == "visual"
    assert manifest["source"] == "docx_pdf_mineru"
    assert manifest["meta"]["coord_provider"] == "mineru"
    assert manifest["meta"]["coord_unit"] == "pt"
    assert manifest["meta"]["docx_pdf_conversion"]["method"] == "docx2pdf"
    assert manifest["pages"][0]["coord_unit"] == "pt"
    assert len(manifest["pages"][0]["blocks"]) == 1


def test_docx_pdf_quality_gate_fail_falls_back_to_docx_raster(tmp_path, monkeypatch):
    cfg = _base_cfg(tmp_path)
    files_dir = Path(cfg["files_dir"])
    files_dir.mkdir(parents=True, exist_ok=True)
    docx_path = files_dir / "demo.docx"
    docx_path.write_bytes(b"fake-docx")

    def fake_convert(docx_path, output_pdf):
        Path(output_pdf).write_bytes(b"%PDF-1.7")
        return output_pdf, "win32com"

    def fake_render_pdf_pages(_file_path, _out_dir, _dpi, _max_pages):
        return [{"page_no": 1, "width": 800, "height": 1200, "blocks": [], "image_file": ""}]

    def fake_build_blocks_from_mineru(file_path, out_dir, max_pages):
        return {}, {1: {"page_width_pt": 595.0, "page_height_pt": 842.0}}, {"page_count": 1, "block_count": 0, "markdown_text_length": 5}

    def fake_render_docx_pages(*_args, **_kwargs):
        return [{"page_no": 1, "width": 900, "height": 1400, "blocks": [{"block_id": "p1-l1", "text": "line"}], "image_file": "fake.png"}], 1

    monkeypatch.setattr(cpa, "_convert_docx_to_pdf", fake_convert)
    monkeypatch.setattr(cpa, "_render_pdf_pages", fake_render_pdf_pages)
    monkeypatch.setattr(cpa, "_build_blocks_from_mineru",
                        fake_build_blocks_from_mineru)
    monkeypatch.setattr(cpa, "_extract_docx_plain_text", lambda _p: "x" * 120)
    monkeypatch.setattr(cpa, "_render_docx_pages", fake_render_docx_pages)

    manifest = cpa.build_contract_preview_manifest(
        cfg=cfg,
        document_id="d2",
        file_path=str(docx_path),
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    assert manifest["mode"] == "visual"
    assert manifest["source"] == "docx_raster"
    assert manifest["meta"]["coord_provider"] == "docx_layout"
    assert manifest["meta"]["docx_pdf_conversion"]["method"] == "win32com"
    assert manifest["meta"]["docx_pdf_conversion"]["fallback"] == "docx_raster"
