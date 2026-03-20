import hashlib
import json
import logging
import os
import re
import shutil
from datetime import datetime
from typing import Dict, Any, List, Tuple

from docx import Document as DocxDocument
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader

from app.core.utils import extract_text_with_config

try:
    from pdf2image import convert_from_path
except Exception:
    convert_from_path = None


logger = logging.getLogger("law_assistant")


def _safe_int(v: Any, default: int) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _resolve_cache_dir(cfg: Dict[str, Any], preview_cfg: Dict[str, Any]) -> str:
    raw = str(preview_cfg.get("cache_dir") or "").strip()
    if not raw:
        return os.path.join(str(cfg.get("files_dir") or ""), "preview_cache")
    if os.path.isabs(raw):
        return raw
    return os.path.abspath(os.path.join(str(cfg.get("files_dir") or ""), raw))


def _file_signature(path: str) -> str:
    st = os.stat(path)
    size = int(st.st_size or 0)
    sample_size = 1024 * 1024
    head_size = min(size, sample_size)
    tail_size = min(size, sample_size)
    h = hashlib.sha1()
    h.update(str(size).encode("utf-8"))
    with open(path, "rb") as f:
        if head_size > 0:
            h.update(f.read(head_size))
        if size > tail_size:
            f.seek(max(0, size - tail_size))
            h.update(f.read(tail_size))
    return h.hexdigest()


def _settings_signature(settings: Dict[str, Any]) -> str:
    payload = json.dumps(settings or {}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _build_cache_key(file_signature: str, settings_signature: str, ext: str) -> str:
    seed = f"{file_signature}|{settings_signature}|{ext}"
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()


def _is_valid_preview_image(path: str) -> bool:
    if not path or not os.path.exists(path):
        return False
    try:
        if os.path.getsize(path) < 64:
            return False
        with open(path, "rb") as f:
            sig = f.read(8)
        return sig == b"\x89PNG\r\n\x1a\n"
    except Exception:
        return False


def _build_text_pages(text: str, lines_per_page: int) -> List[Dict[str, Any]]:
    lines = [ln for ln in str(text or "").splitlines()]
    if not lines:
        return [{"page_no": 1, "width": 0, "height": 0, "blocks": [], "image_file": ""}]
    size = max(1, _safe_int(lines_per_page, 80))
    pages: List[Dict[str, Any]] = []
    for idx in range(0, len(lines), size):
        chunk = lines[idx: idx + size]
        blocks = []
        denom = max(len(chunk), 1)
        for j, line in enumerate(chunk, 1):
            txt = str(line).strip()
            if not txt:
                continue
            y = (j - 1) / denom
            h = 1 / denom
            blocks.append({
                "block_id": f"p{(idx // size) + 1}-l{j}",
                "text": txt,
                "bbox": [0.04, round(y, 6), 0.92, round(h, 6)],
            })
        pages.append({
            "page_no": (idx // size) + 1,
            "width": 0,
            "height": 0,
            "blocks": blocks,
            "image_file": "",
        })
    return pages


def _render_pdf_pages(file_path: str, out_dir: str, dpi: int, max_pages: int) -> List[Dict[str, Any]]:
    if convert_from_path is None:
        raise RuntimeError("pdf2image not available")
    images = convert_from_path(
        file_path,
        dpi=max(72, _safe_int(dpi, 160)),
        first_page=1,
        last_page=max(1, _safe_int(max_pages, 30)),
        fmt="png",
    )
    pages: List[Dict[str, Any]] = []
    for i, image in enumerate(images, 1):
        image_file = os.path.join(out_dir, f"page_{i}.png")
        image.save(image_file, "PNG")
        pages.append({
            "page_no": i,
            "width": int(getattr(image, "width", 0) or 0),
            "height": int(getattr(image, "height", 0) or 0),
            "blocks": [],
            "image_file": image_file,
        })
    return pages


def _extract_pdf_text_blocks(file_path: str, max_pages: int) -> Tuple[Dict[int, List[Dict[str, Any]]], int]:
    reader = PdfReader(file_path)
    total = min(len(reader.pages), max(1, _safe_int(max_pages, 30)))
    out: Dict[int, List[Dict[str, Any]]] = {}
    for i in range(total):
        text = reader.pages[i].extract_text() or ""
        lines = [ln.strip() for ln in text.splitlines() if ln and ln.strip()]
        blocks = []
        denom = max(len(lines), 1)
        for j, line in enumerate(lines, 1):
            y = (j - 1) / denom
            h = 1 / denom
            blocks.append({
                "block_id": f"p{i + 1}-l{j}",
                "text": line,
                "bbox": [0.04, round(y, 6), 0.92, round(h, 6)],
            })
        out[i + 1] = blocks
    return out, total


def _load_preview_font(size: int) -> Any:
    candidates = [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simsun.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    for path in candidates:
        try:
            if os.path.exists(path):
                return ImageFont.truetype(path, size=size)
        except Exception:
            pass
    return ImageFont.load_default()


def _normalize_line_text(text: str) -> str:
    return " ".join(str(text or "").replace("\t", " ").split()).strip()


def _wrap_text_by_width(text: str, draw: Any, font: Any, max_width: int, hard_limit: int) -> List[str]:
    raw = _normalize_line_text(text)
    if not raw:
        return []
    limit = max(10, _safe_int(hard_limit, 44))
    out: List[str] = []
    buf = ""
    for ch in raw:
        nxt = f"{buf}{ch}"
        if len(nxt) <= limit and float(draw.textlength(nxt, font=font)) <= max_width:
            buf = nxt
            continue
        if buf.strip():
            out.append(buf.rstrip())
        buf = "" if ch == " " else ch
    if buf.strip():
        out.append(buf.rstrip())
    return out


def _paragraph_profile(paragraph: Any) -> Dict[str, Any]:
    style_name = str(getattr(getattr(paragraph, "style", None), "name", "") or "").strip().lower()
    txt = _normalize_line_text(getattr(paragraph, "text", ""))
    is_heading = style_name.startswith("heading") or style_name.startswith("标题") or bool(re.match(r"^(第[一二三四五六七八九十百千0-9]+[章节条款]|[0-9]+(?:\.[0-9]+){0,3})", txt))
    level = 1
    m = re.search(r"(\d+)", style_name)
    if m:
        level = max(1, min(6, _safe_int(m.group(1), 1)))
    if is_heading:
        scale = max(1.0, 1.34 - (level - 1) * 0.07)
        return {"line_scale": scale, "gap_before": 12 if level <= 2 else 8, "gap_after": 8 if level <= 2 else 6, "indent": 0}
    is_list = style_name.startswith("list") or txt.startswith(("-", "•", "*", "1.", "2.", "3."))
    if is_list:
        return {"line_scale": 1.0, "gap_before": 2, "gap_after": 4, "indent": 24}
    return {"line_scale": 1.0, "gap_before": 2, "gap_after": 8, "indent": 0}


def _render_docx_pages(file_path: str, out_dir: str, max_pages: int, page_width: int, page_height: int, margin: int, line_height: int, chars_per_line: int, paragraph_spacing: int) -> Tuple[List[Dict[str, Any]], int]:
    doc = DocxDocument(file_path)
    line_h = max(16, _safe_int(line_height, 36))
    base_gap = max(4, _safe_int(paragraph_spacing, int(line_h * 0.45)))
    raw_margin = _safe_int(margin, 72)
    safe_margin = min(max(24, raw_margin), max(36, page_width // 6), max(36, page_height // 8))
    font_cache: Dict[int, Any] = {}

    def _font_for(scale: float) -> Any:
        size = max(12, int(line_h * 0.72 * max(0.85, float(scale))))
        if size not in font_cache:
            font_cache[size] = _load_preview_font(size)
        return font_cache[size]

    measure_img = Image.new("RGB", (8, 8), color=(255, 255, 255))
    measure = ImageDraw.Draw(measure_img)
    flow: List[Dict[str, Any]] = []
    for p in doc.paragraphs:
        txt = _normalize_line_text(p.text)
        profile = _paragraph_profile(p)
        if not txt:
            if flow and flow[-1].get("kind") != "gap":
                flow.append({"kind": "gap", "h": base_gap})
            continue
        line_scale = float(profile.get("line_scale") or 1.0)
        indent = max(0, _safe_int(profile.get("indent"), 0))
        line_h_local = max(16, int(line_h * max(0.9, line_scale)))
        gap_before = max(0, _safe_int(profile.get("gap_before"), 0))
        gap_after = max(0, _safe_int(profile.get("gap_after"), base_gap))
        font = _font_for(line_scale)
        max_text_width = max(120, page_width - safe_margin * 2 - indent)
        hard_limit = max(10, int(chars_per_line * (1.08 if line_scale > 1.08 else 1.0)))
        wrapped = _wrap_text_by_width(txt, measure, font, max_text_width, hard_limit)
        if flow and gap_before > 0:
            flow.append({"kind": "gap", "h": gap_before})
        for ln in wrapped:
            flow.append({"kind": "line", "text": ln, "line_h": line_h_local, "line_scale": line_scale, "indent": indent, "max_w": max_text_width})
        flow.append({"kind": "gap", "h": gap_after})
    while flow and flow[-1].get("kind") == "gap":
        flow.pop()

    line_total = sum(1 for it in flow if it.get("kind") == "line")
    if line_total == 0:
        return [{"page_no": 1, "width": page_width, "height": page_height, "blocks": [], "image_file": ""}], 0

    pages: List[Dict[str, Any]] = []
    cursor = 0
    page_no = 1
    max_page_count = max(1, _safe_int(max_pages, 30))
    while cursor < len(flow) and page_no <= max_page_count:
        image = Image.new("RGB", (page_width, page_height), color=(255, 255, 255))
        draw = ImageDraw.Draw(image)
        y = safe_margin
        line_idx = 0
        blocks = []
        while cursor < len(flow):
            item = flow[cursor]
            kind = str(item.get("kind") or "")
            if kind == "gap":
                h = max(0, _safe_int(item.get("h"), base_gap))
                if blocks and y + h <= page_height - safe_margin:
                    y += h
                cursor += 1
                continue
            local_h = max(16, _safe_int(item.get("line_h"), line_h))
            if y + local_h > page_height - safe_margin:
                break
            line = str(item.get("text") or "")
            line_scale = float(item.get("line_scale") or 1.0)
            indent = max(0, _safe_int(item.get("indent"), 0))
            max_w = max(120, _safe_int(item.get("max_w"), page_width - safe_margin * 2 - indent))
            x = safe_margin + indent
            draw.text((x, y), line, fill=(25, 25, 25), font=_font_for(line_scale))
            line_idx += 1
            blocks.append({
                "block_id": f"p{page_no}-l{line_idx}",
                "text": line,
                "bbox": [round(x / page_width, 6), round(y / page_height, 6), round(max_w / page_width, 6), round(local_h / page_height, 6)],
            })
            y += local_h
            cursor += 1
        image_file = os.path.join(out_dir, f"page_{page_no}.png")
        image.save(image_file, "PNG")
        pages.append({"page_no": page_no, "width": page_width, "height": page_height, "blocks": blocks, "image_file": image_file})
        page_no += 1
    return pages, line_total


def build_contract_preview_manifest(cfg: Dict[str, Any], document_id: str, file_path: str, mime_type: str = "") -> Dict[str, Any]:
    if not file_path or not os.path.exists(file_path):
        raise FileNotFoundError("document file not found")

    preview_cfg = cfg.get("contract_preview") if isinstance(
        cfg.get("contract_preview"), dict) else {}
    enabled = bool(preview_cfg.get("enabled", True))
    dpi = _safe_int(preview_cfg.get("pdf_dpi"), 160)
    max_pages = _safe_int(preview_cfg.get("pdf_max_pages"), 30)
    lines_per_page = _safe_int(preview_cfg.get("text_lines_per_page"), 80)
    docx_visual_enabled = bool(preview_cfg.get("docx_visual_enabled", True))
    docx_page_width = _safe_int(preview_cfg.get("docx_page_width"), 1240)
    docx_page_height = _safe_int(preview_cfg.get("docx_page_height"), 1754)
    docx_margin = _safe_int(preview_cfg.get("docx_margin"), 72)
    docx_line_height = _safe_int(preview_cfg.get("docx_line_height"), 36)
    docx_chars_per_line = _safe_int(preview_cfg.get("docx_chars_per_line"), 44)
    docx_paragraph_spacing = _safe_int(preview_cfg.get("docx_paragraph_spacing"), 14)
    ext = os.path.splitext(file_path)[1].lower()

    cache_root = _resolve_cache_dir(cfg, preview_cfg)
    os.makedirs(cache_root, exist_ok=True)

    signature = _file_signature(file_path)
    settings = {
        "pdf_dpi": dpi,
        "pdf_max_pages": max_pages,
        "text_lines_per_page": lines_per_page,
        "docx_visual_enabled": docx_visual_enabled,
        "docx_page_width": docx_page_width,
        "docx_page_height": docx_page_height,
        "docx_margin": docx_margin,
        "docx_line_height": docx_line_height,
        "docx_chars_per_line": docx_chars_per_line,
        "docx_paragraph_spacing": docx_paragraph_spacing,
    }
    settings_sig = _settings_signature(settings)
    cache_key = _build_cache_key(signature, settings_sig, ext)
    cache_dir = os.path.join(cache_root, cache_key)
    manifest_path = os.path.join(cache_dir, "manifest.json")

    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            same = (
                str(cached.get("file_signature") or "") == signature
                and cached.get("settings") == settings
            )
            if same:
                valid = True
                for p in cached.get("pages") or []:
                    image_file = str(p.get("image_file") or "")
                    if image_file and not _is_valid_preview_image(image_file):
                        valid = False
                        break
                if valid:
                    cached["document_id"] = document_id
                    cached["mime_type"] = str(mime_type or cached.get("mime_type") or "")
                    return cached
        except Exception:
            pass

    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir, ignore_errors=True)
    os.makedirs(cache_dir, exist_ok=True)

    mode = "text"
    source = "text_fallback"
    pages: List[Dict[str, Any]] = []
    text = ""
    meta = {
        "ocr_used": False,
        "ocr_engine": "",
        "line_total": 0,
        "page_count": 0,
    }

    if enabled and ext == ".pdf":
        try:
            pages = _render_pdf_pages(file_path, cache_dir, dpi, max_pages)
            blocks_map, page_count = _extract_pdf_text_blocks(
                file_path, max_pages)
            for p in pages:
                p["blocks"] = blocks_map.get(int(p.get("page_no") or 0), [])
            mode = "visual"
            source = "pdf_raster"
            meta["page_count"] = page_count
            meta["line_total"] = sum(len(p.get("blocks") or []) for p in pages)
        except Exception:
            logger.exception("preview_visual_build_failed file=%s", file_path)

    if enabled and mode != "visual" and ext == ".docx" and docx_visual_enabled:
        try:
            pages, line_total = _render_docx_pages(
                file_path=file_path,
                out_dir=cache_dir,
                max_pages=max_pages,
                page_width=docx_page_width,
                page_height=docx_page_height,
                margin=docx_margin,
                line_height=docx_line_height,
                chars_per_line=docx_chars_per_line,
                paragraph_spacing=docx_paragraph_spacing,
            )
            has_image = any(str(p.get("image_file") or "").strip() for p in pages)
            if has_image:
                mode = "visual"
                source = "docx_raster"
                meta["page_count"] = len(pages)
                meta["line_total"] = int(line_total)
        except Exception:
            logger.exception("preview_docx_visual_build_failed file=%s", file_path)

    if mode != "visual":
        text, text_meta = extract_text_with_config(cfg, file_path)
        pages = _build_text_pages(text, lines_per_page)
        meta["ocr_used"] = bool(text_meta.get("ocr_used"))
        meta["ocr_engine"] = str(text_meta.get("ocr_engine") or "")
        meta["line_total"] = len(str(text).splitlines())
        meta["page_count"] = int(text_meta.get("page_count") or len(pages))

    manifest = {
        "document_id": document_id,
        "mode": mode,
        "source": source,
        "mime_type": str(mime_type or ""),
        "ext": ext,
        "generated_at": datetime.utcnow().isoformat(),
        "file_signature": signature,
        "settings": settings,
        "cache_key": cache_key,
        "meta": {
            "page_total": len(pages),
            "page_count": int(meta.get("page_count") or len(pages)),
            "line_total": int(meta.get("line_total") or 0),
            "ocr_used": bool(meta.get("ocr_used")),
            "ocr_engine": str(meta.get("ocr_engine") or ""),
        },
        "pages": pages,
        "text": text if mode == "text" else "",
    }

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return manifest


def find_preview_page(manifest: Dict[str, Any], page_no: int) -> Dict[str, Any]:
    target = int(page_no)
    for p in manifest.get("pages") or []:
        if int(p.get("page_no") or 0) == target:
            return p
    raise ValueError("preview page not found")
