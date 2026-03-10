import os
import re
from typing import List
from pypdf import PdfReader
from docx import Document

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resolve_path(p: str, base_dir: str = APP_ROOT) -> str:
    if not p:
        return ""
    if os.path.isabs(p):
        return p
    return os.path.abspath(os.path.join(base_dir, p))


def extract_text(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".txt":
        with open(path, "rb") as f:
            data = f.read()
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("gbk", errors="ignore")
    if ext == ".docx":
        doc = Document(path)
        return "\n".join([p.text for p in doc.paragraphs])
    if ext == ".pdf":
        reader = PdfReader(path)
        pages = []
        for p in reader.pages:
            pages.append(p.extract_text() or "")
        return "\n".join(pages)
    raise ValueError("unsupported file type")


def split_articles(text):
    text = re.sub(r"\r\n", "\n", text)
    parts = re.split(r"(第[一二三四五六七八九十百千0-9]+条)", text)
    items = []
    if len(parts) >= 3:
        i = 1
        while i < len(parts) - 1:
            article_no = parts[i].strip()
            content = parts[i + 1].strip()
            if content:
                items.append((article_no, content))
            i += 2
    if not items:
        paras = [p.strip() for p in text.split("\n") if p.strip()]
        for idx, p in enumerate(paras, 1):
            items.append((f"段{idx}", p))
    return items


def tokenize_query(q: str) -> List[str]:
    words = re.findall(r"[\u4e00-\u9fff]+", q)
    words += re.findall(r"[A-Za-z]+", q)
    words += re.findall(r"[0-9]+", q)
    return [w for w in words if len(w) >= 2][:10]


def best_sentence(text: str, tokens: List[str]) -> tuple[str, int]:
    sents = re.split(r"[。；;\n\r]+", text)
    best = ("", 0)
    for s in sents:
        score = sum(1 for t in tokens if t in s)
        if score > best[1]:
            best = (s.strip(), score)
    return best
