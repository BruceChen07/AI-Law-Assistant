"""
Contract Markdown Export Service
=================================
Export contract text (from PDF/DOCX/TXT sources) as a structured Markdown file.

Features:
- Preserves document structure: headings, clauses, paragraphs
- Supports all import formats: PDF, DOCX, TXT (with OCR fallback for scanned PDFs)
- Detects clause hierarchy (Chapter > Section > Article > Clause)
- Outputs clean, readable Markdown with proper heading levels
- Includes metadata header (source file, export time, page count, OCR status)
"""

import os
import re
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("law_assistant")


# ============================================================================
# Heading level detection
# ============================================================================

# Chinese contract heading patterns with hierarchy levels
_CN_HEADING_PATTERNS = [
    # Level 1: 第X章 / 第X部分
    (1, re.compile(r"^(第[一二三四五六七八九十百千\d]+[章部分编])\s*(.*)$")),
    # Level 2: 第X节
    (2, re.compile(r"^(第[一二三四五六七八九十百千\d]+节)\s*(.*)$")),
    # Level 3: 第X条
    (3, re.compile(r"^(第[一二三四五六七八九十百千\d]+条)\s*(.*)$")),
    # Level 4: X、(一)、(1) numbered items
    (4, re.compile(r"^([一二三四五六七八九十]+、)\s*(.*)$")),
    (4, re.compile(r"^(\([一二三四五六七八九十]+\))\s*(.*)$")),
    # Level 5: 1. 2. 3. or 1.1 1.2
    (5, re.compile(r"^(\d+(?:\.\d+){0,3})\s+(.*)$")),
    (5, re.compile(r"^(\d+(?:\.\d+){0,3}[\.、])\s*(.*)$")),
]

# English contract heading patterns
_EN_HEADING_PATTERNS = [
    # Level 1: Part / Chapter
    (1, re.compile(
        r"^((?:Part|Chapter)\s+[IVXLCM\d]+)\s*[:.]?\s*(.*)$", re.IGNORECASE)),
    # Level 2: Article / Section
    (2, re.compile(
        r"^((?:Article|Section)\s+[IVXLCM\d]+(?:\.\d+)*)\s*[:.]?\s*(.*)$", re.IGNORECASE)),
    # Level 3: Clause
    (3, re.compile(
        r"^((?:Clause)\s+\d+(?:\.\d+)*)\s*[:.]?\s*(.*)$", re.IGNORECASE)),
    # Level 4: (a) (b) or a. b.
    (4, re.compile(r"^(\([a-zA-Z]\))\s*(.*)$")),
    (4, re.compile(r"^([a-zA-Z][\.\)])\s+(.*)$")),
    # Level 5: 1. 2. 3.
    (5, re.compile(r"^(\d+(?:\.\d+){0,3})\s+(.*)$")),
]


def _is_english_text(text: str) -> bool:
    """Detect if text is primarily English."""
    latin = len(re.findall(r"[A-Za-z]", text))
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    if latin <= 0:
        return False
    return latin >= max(30, int(cjk * 1.2))


def _detect_heading(line: str, patterns: list) -> Optional[Tuple[int, str, str]]:
    """
    Detect if a line is a heading.
    Returns (level, heading_text, body_text) or None.
    """
    stripped = line.strip()
    if not stripped:
        return None

    for level, pattern in patterns:
        m = pattern.match(stripped)
        if m:
            heading_part = m.group(1).strip()
            body_part = m.group(2).strip() if m.group(2) else ""
            return (level, heading_part, body_part)

    return None


# ============================================================================
# Markdown generation
# ============================================================================

def _heading_to_markdown(level: int, heading_text: str, body_text: str = "") -> str:
    """Convert a detected heading to Markdown heading syntax."""
    # Map internal levels (1-5) to Markdown heading levels (# to ######)
    md_level = min(level + 1, 6)  # Level 1 -> ##, Level 2 -> ###, etc.
    prefix = "#" * md_level

    if body_text:
        return f"{prefix} {heading_text} {body_text}"
    return f"{prefix} {heading_text}"


def contract_text_to_markdown(
    text: str,
    source_filename: str = "",
    meta: Optional[Dict[str, Any]] = None,
    include_metadata_header: bool = True,
) -> str:
    """
    Convert raw contract text to structured Markdown.

    Args:
        text: Raw extracted contract text
        source_filename: Original file name for metadata
        meta: Extraction metadata (page_count, ocr_used, etc.)
        include_metadata_header: Whether to include YAML-style metadata header

    Returns:
        Structured Markdown string
    """
    if not text or not text.strip():
        return "# (Empty Document)\n"

    meta = meta or {}
    english_mode = _is_english_text(text)
    patterns = _EN_HEADING_PATTERNS if english_mode else _CN_HEADING_PATTERNS

    # Normalize line endings
    normalized = re.sub(r"\r\n", "\n", text)
    lines = normalized.split("\n")

    md_lines: List[str] = []

    # --- Metadata header ---
    if include_metadata_header:
        md_lines.append("---")
        md_lines.append(f"title: \"{source_filename or 'Contract'}\"")
        md_lines.append(
            f"exported_at: \"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\"")
        if meta.get("page_count"):
            md_lines.append(f"pages: {meta['page_count']}")
        if meta.get("ocr_used"):
            md_lines.append(
                f"ocr_engine: \"{meta.get('ocr_engine', 'unknown')}\"")
        if meta.get("ext"):
            md_lines.append(f"source_format: \"{meta['ext'].lstrip('.')}\"")
        md_lines.append(f"language: \"{'en' if english_mode else 'zh'}\"")
        md_lines.append("---")
        md_lines.append("")

    # --- Title (first non-empty line as document title) ---
    title_set = False
    content_started = False

    for line in lines:
        stripped = line.strip()

        # Skip empty lines but preserve paragraph breaks
        if not stripped:
            if content_started:
                md_lines.append("")
            continue

        # First meaningful line becomes the document title
        if not title_set and len(stripped) < 100:
            # Check if it looks like a title (short, no trailing punctuation)
            is_title_like = (
                not stripped.endswith(("。", ".", "；", ";", "，", ","))
                and not re.match(r"^\d+\.", stripped)
            )
            if is_title_like:
                md_lines.append(f"# {stripped}")
                md_lines.append("")
                title_set = True
                content_started = True
                continue

        if not title_set:
            md_lines.append("# Contract")
            md_lines.append("")
            title_set = True

        content_started = True

        # Detect headings
        heading = _detect_heading(stripped, patterns)
        if heading:
            level, heading_text, body_text = heading
            md_lines.append("")
            md_lines.append(_heading_to_markdown(
                level, heading_text, body_text))
            md_lines.append("")
        else:
            # Regular paragraph text
            md_lines.append(stripped)

    # Clean up excessive blank lines
    result = "\n".join(md_lines)
    result = re.sub(r"\n{3,}", "\n\n", result)

    return result.strip() + "\n"


# ============================================================================
# File export
# ============================================================================

def export_contract_to_markdown(
    file_path: str,
    output_path: str = "",
    cfg: Optional[Dict[str, Any]] = None,
    include_metadata_header: bool = True,
) -> Dict[str, Any]:
    """
    Export a contract file (PDF/DOCX/TXT) to a Markdown file.

    Args:
        file_path: Path to the source contract file
        output_path: Output .md file path (auto-generated if empty)
        cfg: Application config (for OCR settings)
        include_metadata_header: Include YAML metadata header

    Returns:
        {
            "success": bool,
            "output_path": str,
            "source_file": str,
            "text_length": int,
            "markdown_length": int,
            "meta": {...}
        }
    """
    from app.core.utils import extract_text_with_config
    from app.core.config import get_config

    if not os.path.exists(file_path):
        return {"success": False, "error": f"File not found: {file_path}"}

    if cfg is None:
        cfg = get_config()

    source_filename = os.path.basename(file_path)
    ext = os.path.splitext(file_path)[1].lower()

    # Validate supported formats
    supported_exts = {".pdf", ".docx", ".txt"}
    if ext not in supported_exts:
        return {
            "success": False,
            "error": f"Unsupported format: {ext}. Supported: {', '.join(sorted(supported_exts))}",
        }

    # Extract text
    try:
        text, meta = extract_text_with_config(cfg, file_path)
    except Exception as e:
        logger.exception("markdown_export_extract_failed file=%s", file_path)
        return {"success": False, "error": f"Text extraction failed: {str(e)}"}

    if not text or not text.strip():
        return {"success": False, "error": "Extracted text is empty"}

    # Generate Markdown
    markdown_content = contract_text_to_markdown(
        text=text,
        source_filename=source_filename,
        meta=meta,
        include_metadata_header=include_metadata_header,
    )

    # Determine output path
    if not output_path:
        base_name = os.path.splitext(source_filename)[0]
        output_dir = os.path.join(os.path.dirname(file_path))
        output_path = os.path.join(output_dir, f"{base_name}.md")

    # Ensure output directory exists
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    # Write file
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(markdown_content)
    except Exception as e:
        return {"success": False, "error": f"Write failed: {str(e)}"}

    logger.info(
        "markdown_export_done source=%s output=%s text_len=%d md_len=%d",
        source_filename, output_path, len(text), len(markdown_content),
    )

    return {
        "success": True,
        "output_path": os.path.abspath(output_path),
        "source_file": source_filename,
        "source_format": ext.lstrip("."),
        "text_length": len(text),
        "markdown_length": len(markdown_content),
        "meta": meta,
    }


def export_contract_text_to_markdown(
    text: str,
    source_filename: str = "contract",
    output_path: str = "",
    meta: Optional[Dict[str, Any]] = None,
    include_metadata_header: bool = True,
) -> Dict[str, Any]:
    """
    Export already-extracted contract text to a Markdown file.
    Useful when text has already been extracted by the audit pipeline.

    Args:
        text: Pre-extracted contract text
        source_filename: Original file name
        output_path: Output .md file path
        meta: Extraction metadata
        include_metadata_header: Include YAML metadata header

    Returns:
        Export result dict
    """
    if not text or not text.strip():
        return {"success": False, "error": "Text is empty"}

    markdown_content = contract_text_to_markdown(
        text=text,
        source_filename=source_filename,
        meta=meta,
        include_metadata_header=include_metadata_header,
    )

    if not output_path:
        base_name = os.path.splitext(source_filename)[0]
        output_path = f"{base_name}.md"

    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(markdown_content)
    except Exception as e:
        return {"success": False, "error": f"Write failed: {str(e)}"}

    return {
        "success": True,
        "output_path": os.path.abspath(output_path),
        "source_file": source_filename,
        "text_length": len(text),
        "markdown_length": len(markdown_content),
    }
