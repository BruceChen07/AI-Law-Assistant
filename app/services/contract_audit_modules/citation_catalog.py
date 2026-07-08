"""
Citation Catalog.
Responsibilities: Responsible for processing legal evidence items, building audit-ready legal catalog, whitelist text, and match dictionary.
Input/Output: Accepts evidence_items and returns formatted dictionaries or strings.
Exception Handling: Returns empty dictionaries or strings when the evidence list is empty.
"""
import structlog
from typing import List, Dict, Any, Set
from app.services.utils.contract_audit_utils import citation_match_key, normalize_article_no

logger = structlog.get_logger(__name__)


def build_citation_lookup(evidence_items: List[Dict[str, Any]]) -> Dict[str, str]:
    """Build a lookup table for citation_id."""
    lookup: Dict[str, str] = {}
    for it in evidence_items:
        cid = str(it.get("citation_id") or "").strip()
        if not cid:
            continue
        law = str(it.get("law_title") or it.get("title") or "").strip()
        article = str(it.get("article_no") or "").strip()
        key = citation_match_key(law, article)
        if key and key not in lookup:
            lookup[key] = cid
    return lookup


def build_legal_catalog(evidence_items: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Build a catalog of article numbers grouped by law name."""
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
    """Build whitelist text for legal evidence items to be put into the prompt."""
    lines: List[str] = []
    for it in evidence_items[:limit]:
        cid = str(it.get("citation_id") or "").strip()
        law = str(it.get("law_title") or it.get("title") or "").strip()
        article = normalize_article_no(it.get("article_no"))
        if not cid or not law or not article:
            continue
        lines.append(f"- {cid}: {law} {article}")
    return "\n".join(lines)
