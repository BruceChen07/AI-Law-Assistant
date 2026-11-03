import hashlib
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from app.core.ocr import OCREngineManager
from app.services.document_digitization import ensure_digitized_docx
from app.services.document_text_extractors import (
    extract_docx_text,
    extract_pdf_pages_text,
)


logger = logging.getLogger("law_assistant")

try:
    import pypdfium2 as pdfium
except Exception:  # pragma: no cover - optional dependency
    pdfium = None


def get_pdf_pipeline_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    raw = cfg.get("pdf_pipeline") if isinstance(
        cfg.get("pdf_pipeline"), dict) else {}
    out = {
        "enabled": bool(raw.get("enabled", True)),
        "hybrid_enabled": bool(raw.get("hybrid_enabled", True)),
        "force_scanned": bool(raw.get("force_scanned", False)),
        "scan_min_chars_per_page": int(raw.get("scan_min_chars_per_page", 24)),
        "scan_ratio_for_ocr": float(raw.get("scan_ratio_for_ocr", 0.55)),
        "scan_ratio_for_digitize": float(raw.get("scan_ratio_for_digitize", 0.7)),
        "auto_digitize": bool(raw.get("auto_digitize", False)),
        "prefer_digitized_for_audit": bool(raw.get("prefer_digitized_for_audit", True)),
        "preview_render_enabled": bool(raw.get("preview_render_enabled", True)),
        "preview_render_engine": str(raw.get("preview_render_engine") or "pdfium").strip().lower(),
        "preview_max_edge": int(raw.get("preview_max_edge", 1800)),
    }
    out["render_engine_available"] = bool(pdfium is not None)
    out["digitizer"] = "mineru"
    return out


def _normalize_text(value: Any) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n")


def _page_stats_from_texts(page_texts: List[str], min_chars: int, force_scanned: bool = False) -> Dict[str, Any]:
    pages = len(page_texts)
    scanned_indices: List[int] = []
    text_indices: List[int] = []
    for idx, text in enumerate(page_texts, start=1):
        if force_scanned:
            scanned_indices.append(idx)
            continue
        if len(str(text or "").strip()) < max(1, int(min_chars)):
            scanned_indices.append(idx)
        else:
            text_indices.append(idx)
    scanned_count = len(scanned_indices)
    text_count = pages - scanned_count
    scanned_ratio = 0.0 if pages <= 0 else round(scanned_count / pages, 4)
    return {
        "page_count": pages,
        "scanned_page_count": scanned_count,
        "text_page_count": text_count,
        "scanned_ratio": scanned_ratio,
        "scanned_page_indices": scanned_indices,
        "text_page_indices": text_indices,
    }


def _hash_document_identity(path: str, original_filename: str = "") -> str:
    seed = f"{os.path.abspath(path)}|{original_filename}|{os.path.getsize(path) if os.path.exists(path) else 0}"
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()


def _build_meta(cfg: Dict[str, Any], path: str, page_texts: List[str]) -> Dict[str, Any]:
    pipeline_cfg = get_pdf_pipeline_config(cfg)
    stats = _page_stats_from_texts(
        page_texts,
        min_chars=int(pipeline_cfg.get("scan_min_chars_per_page", 24)),
        force_scanned=bool(pipeline_cfg.get("force_scanned")),
    )
    return {
        "ext": ".pdf",
        "ocr_used": False,
        "ocr_engine": "",
        "page_count": int(stats["page_count"]),
        "text_page_count": int(stats["text_page_count"]),
        "scanned_page_count": int(stats["scanned_page_count"]),
        "scanned_ratio": float(stats["scanned_ratio"]),
        "scanned_page_indices": list(stats["scanned_page_indices"]),
        "text_page_indices": list(stats["text_page_indices"]),
        "pdf_pipeline": "v2",
        "pdf_backend": "pypdf",
        "pdf_render_engine": str(pipeline_cfg.get("preview_render_engine") or "pdfium"),
        "digitization_used": False,
        "digitization_status": "",
        "digitized_docx_path": "",
        "digitization_layout_retained": False,
        "route": "text",
        "source_file_path": path,
    }


def _read_digitized_docx_text(path: str) -> str:
    if not path or not os.path.exists(path):
        return ""
    ext = str(os.path.splitext(path)[1] or "").lower()
    if ext == ".docx":
        return extract_docx_text(path)
    return ""


def resolve_pdf_audit_source(
    cfg: Dict[str, Any],
    source_file_path: str,
    *,
    document_id: str = "",
    original_filename: str = "",
    prefer_digitized: Optional[bool] = None,
) -> Dict[str, Any]:
    ext = str(os.path.splitext(source_file_path)[1] or "").lower()
    out = {
        "audit_input_path": source_file_path,
        "source_ext": ext,
        "used_digitized": False,
        "digitization_record": None,
    }
    if ext != ".pdf":
        return out

    pipeline_cfg = get_pdf_pipeline_config(cfg)
    should_digitize = bool(pipeline_cfg.get(
        "prefer_digitized_for_audit", True))
    if prefer_digitized is not None:
        should_digitize = bool(prefer_digitized)
    if not should_digitize:
        return out

    digitization_record = ensure_digitized_docx(
        cfg,
        document_id=document_id or _hash_document_identity(
            source_file_path, original_filename or os.path.basename(source_file_path)),
        source_file_path=source_file_path,
        original_filename=original_filename or os.path.basename(
            source_file_path),
    )
    digitized_path = str(digitization_record.get(
        "digitized_docx_path") or "").strip()
    if not digitized_path:
        return out

    out.update({
        "audit_input_path": digitized_path,
        "used_digitized": True,
        "digitization_record": digitization_record,
    })
    return out


def extract_pdf_text_with_pipeline(
    cfg: Dict[str, Any],
    path: str,
    *,
    document_id: str = "",
    original_filename: str = "",
    prefer_digitized: Optional[bool] = None,
    raise_on_ocr_failure: bool = True,
) -> Tuple[str, Dict[str, Any]]:
    page_texts = extract_pdf_pages_text(path)
    baseline_text = "\n\n".join(page_texts)
    meta = _build_meta(cfg, path, page_texts)
    pipeline_cfg = get_pdf_pipeline_config(cfg)

    scanned_ratio = float(meta.get("scanned_ratio") or 0.0)
    ocr_enabled = bool(cfg.get("ocr_enabled", True))
    ocr_min_len = int(cfg.get("ocr_min_text_length", 200))
    ocr_langs = str(cfg.get("ocr_languages", "chi_sim+eng"))
    ocr_dpi = int(cfg.get("ocr_dpi", 220))

    should_digitize = bool(pipeline_cfg.get("auto_digitize", False))
    if prefer_digitized is not None:
        should_digitize = bool(prefer_digitized)
    elif should_digitize and scanned_ratio < float(pipeline_cfg.get("scan_ratio_for_digitize", 0.7)):
        should_digitize = False

    if should_digitize:
        fallback_document_id = document_id or _hash_document_identity(
            path, original_filename or os.path.basename(path))
        try:
            digitization = ensure_digitized_docx(
                cfg,
                document_id=fallback_document_id,
                source_file_path=path,
                original_filename=original_filename or os.path.basename(path),
            )
            digitized_path = str(digitization.get("digitized_docx_path") or "")
            digitized_text = _read_digitized_docx_text(digitized_path)
            if digitized_text.strip():
                meta.update({
                    "ocr_used": bool(digitization.get("ocr_used")),
                    "ocr_engine": str(digitization.get("ocr_engine") or "mineru"),
                    "page_count": int(digitization.get("page_count") or meta.get("page_count") or 0),
                    "digitization_used": bool(digitization.get("digitization_used", True)),
                    "digitization_status": str(digitization.get("status") or "done"),
                    "digitized_docx_path": digitized_path,
                    "digitization_layout_retained": bool(digitization.get("layout_retained")),
                    "digitization_text_match_ratio": float(digitization.get("text_match_ratio") or 0),
                    "digitization_paragraph_count": int(digitization.get("paragraph_count") or 0),
                    "digitization_table_count": int(digitization.get("table_count") or 0),
                    "digitization_image_count": int(digitization.get("image_count") or 0),
                    "pdf_backend": "mineru_digitized_docx",
                    "route": "digitized_docx",
                })
                return digitized_text, meta
        except Exception as exc:
            meta["digitization_status"] = "failed"
            meta["digitization_error"] = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "pdf_pipeline_digitization_failed file=%s err=%s", path, exc, exc_info=True)

    should_ocr = ocr_enabled and (
        len(baseline_text.strip()) < ocr_min_len
        or scanned_ratio >= float(pipeline_cfg.get("scan_ratio_for_ocr", 0.55))
        or bool(pipeline_cfg.get("force_scanned"))
    )
    if should_ocr:
        logger.info(
            "pdf_pipeline_ocr_trigger file=%s text_length=%s scanned_ratio=%s min_len=%s langs=%s dpi=%s",
            path,
            len(baseline_text.strip()),
            scanned_ratio,
            ocr_min_len,
            ocr_langs,
            ocr_dpi,
        )
        manager = OCREngineManager(cfg)
        try:
            ocr_text, ocr_pages, engine = manager.ocr_pdf(
                path, ocr_langs, ocr_dpi, doc_type="pdf")
            if ocr_text.strip():
                meta["ocr_used"] = True
                meta["ocr_engine"] = engine or ""
                meta["page_count"] = max(
                    int(meta.get("page_count") or 0), int(ocr_pages or 0))
                meta["pdf_backend"] = "ocr"
                meta["route"] = "ocr"
                return ocr_text, meta
            if raise_on_ocr_failure:
                raise RuntimeError("OCR returned empty text")
        except Exception as exc:
            logger.exception("pdf_pipeline_ocr_failed file=%s", path)
            if raise_on_ocr_failure:
                raise
            meta["ocr_error"] = f"{type(exc).__name__}: {exc}"

    return baseline_text, meta


def build_pdf_page_blocks(file_path: str, max_pages: int = 30) -> Tuple[Dict[int, List[Dict[str, Any]]], int]:
    page_texts = extract_pdf_pages_text(file_path)
    out: Dict[int, List[Dict[str, Any]]] = {}
    total = min(len(page_texts), max(1, int(max_pages)))
    for page_no in range(1, total + 1):
        text = page_texts[page_no - 1]
        lines = [ln.strip()
                 for ln in _normalize_text(text).split("\n") if ln.strip()]
        blocks: List[Dict[str, Any]] = []
        denom = max(len(lines), 1)
        for idx, line in enumerate(lines, start=1):
            y = (idx - 1) / denom
            h = 1 / denom
            blocks.append({
                "block_id": f"p{page_no}-l{idx}",
                "text": line,
                "bbox": [0.04, round(y, 6), 0.92, round(h, 6)],
                "is_heading": len(line) <= 40 and any(token in line for token in ("第", "条", "章", "Article", "Section")),
            })
        out[page_no] = blocks
    return out, total


def _resize_for_preview(image, max_edge: int):
    width, height = image.size
    edge = max(width, height)
    if edge <= max_edge or max_edge <= 0:
        return image
    ratio = max_edge / float(edge)
    return image.resize((max(1, int(width * ratio)), max(1, int(height * ratio))))


def render_pdf_preview_pages(file_path: str, out_dir: str, dpi: int, max_pages: int, max_edge: int = 1800) -> List[Dict[str, Any]]:
    if pdfium is None:
        raise RuntimeError("pypdfium2 is not installed")
    os.makedirs(out_dir, exist_ok=True)
    pdf = pdfium.PdfDocument(file_path)
    pages: List[Dict[str, Any]] = []
    scale = max(1, int(dpi)) / 72.0
    total = min(len(pdf), max(1, int(max_pages)))
    try:
        for index in range(total):
            page = pdf[index]
            try:
                bitmap = page.render(scale=scale)
                image = bitmap.to_pil()
                image = _resize_for_preview(image, max_edge=max_edge)
                output = os.path.join(out_dir, f"page_{index + 1}.png")
                image.save(output, "PNG")
                pages.append({
                    "page_no": index + 1,
                    "width": int(image.size[0]),
                    "height": int(image.size[1]),
                    "blocks": [],
                    "image_file": output,
                })
            finally:
                close = getattr(page, "close", None)
                if close:
                    close()
    finally:
        close_pdf = getattr(pdf, "close", None)
        if close_pdf:
            close_pdf()
    return pages


def describe_current_pdf_modules(cfg: Dict[str, Any]) -> Dict[str, Any]:
    pipeline_cfg = get_pdf_pipeline_config(cfg)
    return {
        "version": "pdf_pipeline_v2",
        "runtime": {
            "render_engine_available": bool(pipeline_cfg.get("render_engine_available")),
            "digitizer": "mineru",
            "ocr_enabled": bool(cfg.get("ocr_enabled", True)),
            "preview_render_enabled": bool(pipeline_cfg.get("preview_render_enabled")),
            "internal_network_ready": True,
        },
        "modules": [
            {
                "name": "text_extraction",
                "entry": "app.core.utils.extract_text_with_config",
                "service": "app.services.pdf_pipeline.extract_pdf_text_with_pipeline",
                "inputs": [".pdf"],
                "outputs": ["text", "meta"],
                "dependencies": ["pypdf", "app.core.ocr"],
                "business_links": ["contract_audit", "tax_parser", "importer", "vector_store_migration"],
            },
            {
                "name": "digitization",
                "entry": "app.services.document_digitization.ensure_digitized_docx",
                "service": "app.services.document_digitization",
                "inputs": [".pdf"],
                "outputs": ["digitized_docx_path", "text_match_ratio", "layout_retained"],
                "dependencies": ["mineru", "python-docx"],
                "business_links": ["contracts_router", "audit_export", "preview"],
            },
            {
                "name": "preview",
                "entry": "app.services.contract_preview_assets.build_contract_preview_manifest",
                "service": "app.services.pdf_pipeline.render_pdf_preview_pages",
                "inputs": [".pdf", ".docx"],
                "outputs": ["preview_manifest", "page_images", "blocks"],
                "dependencies": ["pypdfium2", "Pillow", "mineru"],
                "business_links": ["contract_preview_ui"],
            },
        ],
        "effective_config": pipeline_cfg,
    }


def summarize_target_pdf_requirements() -> Dict[str, Any]:
    return {
        "core_features": [
            "hybrid text/scanned page routing",
            "bounded PDF raster preview",
            "digitized DOCX export for editable review",
            "unified metadata for audit, preview, and export",
            "internal-network deployment without cloud fallback",
        ],
        "performance": {
            "prefer_text_route_for_native_pdf": True,
            "limit_render_edge_px": 1800,
            "limit_preview_pages_default": 30,
            "ocr_is_last_resort": True,
        },
        "compatibility": {
            "formats": [".pdf", ".docx"],
            "pdf_kinds": ["text", "scanned", "hybrid"],
            "ui_capabilities": ["upload", "preview", "download", "audit export"],
        },
    }


def build_pdf_pipeline_summary(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "current_modules": describe_current_pdf_modules(cfg),
        "target_requirements": summarize_target_pdf_requirements(),
        "replacement_status": {
            "backend_unified_pipeline": True,
            "frontend_preview_enabled": True,
            "digitized_export_enabled": True,
            "legacy_scattered_logic_retained_as_adapters": True,
        },
    }
