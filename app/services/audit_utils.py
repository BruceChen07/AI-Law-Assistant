import re
from typing import Dict, Any, List

def _safe_float(v: Any, default: float) -> float:
    try:
        return float(v)
    except Exception:
        return float(default)

def _safe_int(v: Any, default: int) -> int:
    try:
        return int(v)
    except Exception:
        return int(default)

def _safe_bool(v: Any, default: bool) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    s = str(v).strip().lower()
    if s in ["1", "true", "yes", "y", "on"]:
        return True
    if s in ["0", "false", "no", "n", "off"]:
        return False
    return default

def _normalize_lang(lang: Any, default: str = "zh") -> str:
    s = str(lang or "").strip().lower().replace("_", "-")
    if not s:
        return str(default or "zh").strip().lower()
    if s.startswith("zh"):
        return "zh"
    if s.startswith("en"):
        return "en"
    return str(default or "zh").strip().lower()

def _normalize_risk_level(level: Any) -> str:
    s = str(level or "").strip().lower()
    if s in ["high", "h", "严重", "高", "高风险"]:
        return "high"
    if s in ["low", "l", "轻微", "低", "低风险"]:
        return "low"
    return "medium"

def _build_excerpt(text: Any, max_len: int = 120) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    if not s:
        return ""
    if len(s) <= max_len:
        return s
    return s[:max_len].rstrip() + "…"

def _normalize_citation_item(item: Dict[str, Any]) -> Dict[str, Any]:
    title = str(item.get("law_title") or item.get("title") or "").strip()
    article_no = str(item.get("article_no") or "").strip()
    content = str(item.get("content") or "").strip()
    excerpt = str(item.get("excerpt") or "").strip()
    if not excerpt:
        excerpt = _build_excerpt(content)
    out = dict(item)
    out["law_title"] = title
    if title and not str(out.get("title") or "").strip():
        out["title"] = title
    out["article_no"] = article_no
    out["content"] = content
    out["excerpt"] = excerpt
    return out

def _enrich_citations(citations: List[Dict[str, Any]], evidence_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    evidence_map = {}
    for it in evidence_items:
        cid = str(it.get("citation_id", "")).strip()
        if cid:
            evidence_map[cid] = _normalize_citation_item(it)
    out = []
    for c in citations:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("citation_id", "")).strip()
        merged = dict(c)
        if cid and cid in evidence_map:
            src = evidence_map[cid]
            for key in ["law_title", "title", "article_no", "content", "excerpt", "effective_date", "expiry_date", "region", "industry"]:
                if not str(merged.get(key, "") or "").strip():
                    merged[key] = src.get(key, "")
        out.append(_normalize_citation_item(merged))
    return out

def _chunk_contract_text(text: str, chunk_size: int, max_chunks: int) -> List[str]:
    if not text.strip():
        return []
    paras = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    if not paras:
        paras = [p.strip() for p in text.split("\n") if p.strip()]
    chunks = []
    buf = ""
    for p in paras:
        merged = p if not buf else (buf + "\n" + p)
        if len(merged) <= chunk_size:
            buf = merged
            continue
        if buf:
            chunks.append(buf)
        if len(p) <= chunk_size:
            buf = p
        else:
            start = 0
            while start < len(p):
                chunks.append(p[start:start + chunk_size])
                start += chunk_size
            buf = ""
        if len(chunks) >= max_chunks:
            break
    if buf and len(chunks) < max_chunks:
        chunks.append(buf)
    return chunks[:max_chunks]
