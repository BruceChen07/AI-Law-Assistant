from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from docx import Document
from pypdf import PdfReader

from app.core.utils import extract_text_with_config
from app.services.audit_types import PageSpan, SourceDocument


PARSER_VERSION = "audit-source-v2"


class SourceExtractionError(RuntimeError):
    code = "EXTRACTION_FAILED"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_from_pages(
    *,
    document_id: str,
    source_hash: str,
    pages: Iterable[tuple[Optional[int], str]],
    parser_version: str = PARSER_VERSION,
    extraction_warnings: Iterable[str] = (),
) -> SourceDocument:
    text_parts: list[str] = []
    page_spans: list[PageSpan] = []
    cursor = 0
    for index, (page_no, page_text) in enumerate(pages):
        if index:
            text_parts.append("\f")
            cursor += 1
        value = str(page_text or "")
        start = cursor
        text_parts.append(value)
        cursor += len(value)
        page_spans.append(PageSpan(page_no=page_no, start=start, end=cursor))
    return SourceDocument(
        document_id=str(document_id),
        source_hash=str(source_hash),
        parser_version=str(parser_version),
        text="".join(text_parts),
        pages=tuple(page_spans),
        extraction_warnings=tuple(str(x) for x in extraction_warnings if str(x)),
    )


def source_from_text(
    text: str,
    *,
    document_id: str = "synthetic",
    source_hash: str = "",
    page_no: Optional[int] = None,
    parser_version: str = PARSER_VERSION,
) -> SourceDocument:
    value = str(text or "")
    digest = source_hash or hashlib.sha256(value.encode("utf-8")).hexdigest()
    return source_from_pages(
        document_id=document_id,
        source_hash=digest,
        pages=[(page_no, value)],
        parser_version=parser_version,
    )


def _extract_docx_pages(path: Path) -> list[tuple[Optional[int], str]]:
    document = Document(str(path))
    paragraphs = [str(paragraph.text or "") for paragraph in document.paragraphs]
    return [(None, "\n".join(paragraphs))]


def _extract_pdf_pages(path: Path) -> list[tuple[Optional[int], str]]:
    reader = PdfReader(str(path))
    return [
        (index, page.extract_text() or "")
        for index, page in enumerate(reader.pages, start=1)
    ]


def extract_source(
    cfg: Dict[str, Any],
    file_path: str,
    document_id: str,
) -> SourceDocument:
    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        raise SourceExtractionError("EXTRACTION_FAILED: source file not found")
    ext = path.suffix.lower()
    warnings: list[str] = []
    try:
        if ext == ".pdf":
            pages = _extract_pdf_pages(path)
            extracted_text = "\f".join(text for _, text in pages)
            minimum = int(cfg.get("ocr_min_text_length", 200))
            if bool(cfg.get("ocr_enabled", True)) and len(extracted_text.strip()) < minimum:
                ocr_text, meta = extract_text_with_config(cfg, str(path))
                page_count = max(1, int(meta.get("page_count") or 1))
                # Existing OCR adapters return a flattened stream. Keep page numbers
                # unknown rather than inventing page boundaries.
                pages = [(None, ocr_text)]
                if page_count > 1:
                    warnings.append("OCR_PAGE_BOUNDARIES_UNAVAILABLE")
                warnings.append(
                    f"OCR_USED:{str(meta.get('ocr_engine') or 'unknown')}"
                )
        elif ext == ".docx":
            pages = _extract_docx_pages(path)
        elif ext == ".txt":
            pages = [(None, path.read_text(encoding="utf-8"))]
        else:
            raise SourceExtractionError(
                f"EXTRACTION_FAILED: unsupported file type {ext}"
            )
    except SourceExtractionError:
        raise
    except Exception as exc:
        raise SourceExtractionError(
            f"EXTRACTION_FAILED: {type(exc).__name__}: {exc}"
        ) from exc

    if not any(str(text or "").strip() for _, text in pages):
        warnings.append("EMPTY_SOURCE_TEXT")
    return source_from_pages(
        document_id=document_id,
        source_hash=_sha256_file(path),
        pages=pages,
        extraction_warnings=warnings,
    )


def source_manifest(source: SourceDocument, artifact_path: str = "") -> Dict[str, Any]:
    return {
        "artifact_path": str(artifact_path or ""),
        "artifact_hash": "",
        "source_hash": source.source_hash,
        "parser_version": source.parser_version,
        "text_length": len(source.text),
        "pages": [
            {"page_no": item.page_no, "start": item.start, "end": item.end}
            for item in source.pages
        ],
        "extraction_warnings": list(source.extraction_warnings),
        "nonspace_chars": sum(1 for char in source.text if not char.isspace()),
        "source_filename": os.path.basename(artifact_path) if artifact_path else "",
    }
