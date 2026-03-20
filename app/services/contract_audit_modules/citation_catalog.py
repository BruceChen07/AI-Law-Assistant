"""
Citation Catalog.
职责: 负责处理法规证据项，构建用于审计的法条目录、白名单文本及匹配字典。
输入输出: 接收检索出的 evidence_items，返回格式化后的字典或字符串。
异常场景: 证据列表为空时返回空字典或空字符串。
"""
import structlog
from typing import List, Dict, Any, Set
from app.services.utils.contract_audit_utils import citation_match_key, normalize_article_no

logger = structlog.get_logger(__name__)

def build_citation_lookup(evidence_items: List[Dict[str, Any]]) -> Dict[str, str]:
    """构建基于法规标题和条款号的 citation_id 查找表。"""
    lookup: Dict[str, str] = {}
    for it in evidence_items:
        cid = str(it.get("citation_id") or "").strip()
        if not cid:
            continue
        law = str(it.get("law_title") or it.get("title") or "").strip()
        article = str(it.get("article_no") or "").strip()
        key = citation_match_key(law, article)
        if key:
            lookup[key] = cid
    return lookup

def build_legal_catalog(evidence_items: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """构建按法规名称分组的条款编号目录。"""
    catalog: Dict[str, Set[str]] = {}
    for it in evidence_items:
        law = str(it.get("law_title") or it.get("title") or "").strip()
        article = str(it.get("article_no") or "").strip()
        if not law or not article:
            continue
        if law not in catalog:
            catalog[law] = set()
        catalog[law].add(normalize_article_no(article))
    return {k: sorted(list(v)) for k, v in catalog.items()}

def build_evidence_whitelist_text(evidence_items: List[Dict[str, Any]], limit: int = 60) -> str:
    """构建用于放入 Prompt 的法规白名单文本。"""
    lines: List[str] = []
    for it in evidence_items[:limit]:
        cid = str(it.get("citation_id") or "").strip()
        law = str(it.get("law_title") or it.get("title") or "").strip()
        article = normalize_article_no(it.get("article_no"))
        if not cid or not law or not article:
            continue
        lines.append(f"- {cid}: {law} {article}")
    return "\n".join(lines)
