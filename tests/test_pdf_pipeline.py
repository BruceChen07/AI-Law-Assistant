import app.services.pdf_pipeline as pipeline


def test_extract_pdf_text_with_pipeline_returns_baseline_text(monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "extract_pdf_pages_text",
        lambda _path: ["First page text", "Second page text"],
    )

    text, meta = pipeline.extract_pdf_text_with_pipeline(
        {"ocr_enabled": False, "pdf_pipeline": {
            "auto_digitize": False, "scan_min_chars_per_page": 5}},
        "dummy.pdf",
    )

    assert "First page text" in text
    assert meta["page_count"] == 2
    assert meta["text_page_count"] == 2
    assert meta["scanned_page_count"] == 0
    assert meta["pdf_pipeline"] == "v2"


def test_extract_pdf_text_with_pipeline_prefers_digitized_docx(monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "extract_pdf_pages_text",
        lambda _path: ["", ""],
    )
    monkeypatch.setattr(
        pipeline,
        "ensure_digitized_docx",
        lambda *_args, **_kwargs: {
            "digitized_docx_path": "dummy_digitized.docx",
            "digitization_used": True,
            "status": "done",
            "ocr_used": 1,
            "ocr_engine": "mineru",
            "page_count": 2,
            "layout_retained": 1,
            "text_match_ratio": 0.92,
            "paragraph_count": 8,
            "table_count": 1,
            "image_count": 2,
        },
    )
    monkeypatch.setattr(pipeline, "_read_digitized_docx_text",
                        lambda _path: "Digitized contract text")

    text, meta = pipeline.extract_pdf_text_with_pipeline(
        {"ocr_enabled": True, "pdf_pipeline": {"auto_digitize": True}},
        "dummy.pdf",
        document_id="doc-1",
        original_filename="contract.pdf",
    )

    assert text == "Digitized contract text"
    assert meta["digitization_used"] is True
    assert meta["pdf_backend"] == "mineru_digitized_docx"
    assert meta["route"] == "digitized_docx"
    assert meta["digitization_table_count"] == 1


def test_resolve_pdf_audit_source_prefers_digitized_docx(monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "ensure_digitized_docx",
        lambda *_args, **_kwargs: {
            "digitized_docx_path": "resolved_digitized.docx",
            "digitization_used": True,
            "status": "done",
        },
    )

    resolved = pipeline.resolve_pdf_audit_source(
        {"pdf_pipeline": {"prefer_digitized_for_audit": True}},
        "contract.pdf",
        document_id="doc-2",
        original_filename="contract.pdf",
    )

    assert resolved["audit_input_path"] == "resolved_digitized.docx"
    assert resolved["used_digitized"] is True
    assert resolved["digitization_record"]["status"] == "done"


def test_build_pdf_pipeline_summary_contains_runtime_and_targets():
    summary = pipeline.build_pdf_pipeline_summary(
        {
            "ocr_enabled": True,
            "pdf_pipeline": {"preview_render_engine": "pdfium"},
        }
    )

    assert summary["current_modules"]["version"] == "pdf_pipeline_v2"
    assert "modules" in summary["current_modules"]
    assert "target_requirements" in summary
    assert summary["replacement_status"]["backend_unified_pipeline"] is True
