import os
from typing import List, Tuple

from docx import Document
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader


def safe_decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("gbk", errors="ignore")


def extract_docx_text(path: str) -> str:
    doc = Document(path)
    lines: List[str] = []
    body = getattr(getattr(doc, "element", None), "body", None)
    if body is None:
        return ""

    def _append_paragraph_text(paragraph: Paragraph) -> None:
        text = str(getattr(paragraph, "text", "") or "").strip()
        if text:
            lines.append(text)

    def _append_table_text(table: Table) -> None:
        for row in table.rows:
            row_texts = []
            for cell in row.cells:
                cell_text = str(getattr(cell, "text", "") or "").strip()
                row_texts.append(cell_text)
            merged = "\t".join([item for item in row_texts if item])
            if merged.strip():
                lines.append(merged)

    for child in body.iterchildren():
        if isinstance(child, CT_P):
            _append_paragraph_text(Paragraph(child, doc))
        elif isinstance(child, CT_Tbl):
            _append_table_text(Table(child, doc))
    return "\n".join(lines)


def extract_pdf_pages_text(path: str) -> List[str]:
    reader = PdfReader(path)
    pages: List[str] = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return pages


def extract_pdf_text(path: str) -> Tuple[str, int]:
    pages = extract_pdf_pages_text(path)
    return "\n\n".join(pages), len(pages)


def extract_text_from_extension(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".txt":
        with open(path, "rb") as f:
            return safe_decode(f.read())
    if ext == ".docx":
        return extract_docx_text(path)
    if ext == ".pdf":
        text, _ = extract_pdf_text(path)
        return text
    raise ValueError("unsupported file type")
