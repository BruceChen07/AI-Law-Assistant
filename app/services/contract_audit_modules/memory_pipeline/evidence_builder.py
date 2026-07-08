"""Evidence construction helpers for memory audit pipeline."""

from typing import Any, Dict, List, Set
import re

from app.services.contract_audit_modules.citation_catalog import (
    build_citation_lookup,
    build_evidence_whitelist_text,
    build_legal_catalog,
)
from app.services.utils.contract_audit_utils import (
    citation_match_key,
    normalize_article_no,
    normalize_law_title,
)


def _build_compact_whitelist(evidence_items: List[Dict[str, Any]], limit: int = 40):
    lines: List[str] = []
    alias_to_cid: Dict[str, str] = {}
    n = 0
    for it in evidence_items:
        cid = str(it.get("citation_id") or "").strip()
        if not cid:
            continue
        law = str(it.get("law_title") or it.get("title") or "").strip()
        article = str(it.get("article_no") or "").strip()
        if not law or not article:
            continue
        n += 1
        alias = f"C{n}"
        alias_to_cid[alias] = cid
        lines.append(f"- {alias}: {law} {article}")
        if n >= max(1, int(limit or 1)):
            break
    return "\n".join(lines), alias_to_cid


def _select_fallback_citation(
    evidence_items: List[Dict[str, Any]],
    allowed_citation_ids: Set[str],
    norm_lang: str,
) -> Dict[str, str]:
    kw = ["税", "发票", "纳税", "免税", "tax", "vat",
          "invoice", "withholding", "exempt"]
    best: Dict[str, str] = {}
    best_score = -1
    for it in evidence_items:
        cid = str(it.get("citation_id") or "").strip()
        if not cid or cid not in allowed_citation_ids:
            continue
        law = str(it.get("law_title") or it.get("title") or "").strip()
        article = str(it.get("article_no") or "").strip()
        if not law or not article:
            continue
        content = " ".join([
            law,
            article,
            str(it.get("content") or ""),
            str(it.get("excerpt") or ""),
        ]).lower()
        score = 0
        if str(it.get("content") or it.get("excerpt") or "").strip():
            score += 3
        if any(k in content for k in kw):
            score += 3
        if norm_lang == "en" and re.search(r"[a-zA-Z]", law):
            score += 1
        if norm_lang != "en" and re.search(r"[\u4e00-\u9fff]", law):
            score += 1
        if score > best_score:
            best_score = score
            best = {"citation_id": cid, "law_title": law, "article_no": article}
    return best


def _build_article_citation_index(
    evidence_items: List[Dict[str, Any]],
    allowed_citation_ids: Set[str],
) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for it in evidence_items:
        cid = str(it.get("citation_id") or "").strip()
        if not cid or cid not in allowed_citation_ids:
            continue
        article = normalize_article_no(it.get("article_no"))
        if not article:
            continue
        if article not in out:
            out[article] = []
        if cid not in out[article]:
            out[article].append(cid)
    return out


def _resolve_risk_citation_id(
    citation_id_raw: str,
    law_title: str,
    article_no: str,
    allowed_citation_ids: Set[str],
    citation_lookup: Dict[str, str],
    citation_alias_map: Dict[str, str],
    citation_id_casefold_map: Dict[str, str],
    article_citation_index: Dict[str, List[str]],
    evidence_by_cid: Dict[str, Dict[str, Any]],
) -> str:
    cid = str(citation_id_raw or "").strip()
    if not cid:
        pass
    elif cid in allowed_citation_ids:
        return cid
    else:
        alias_hit = str(citation_alias_map.get(cid, "")).strip()
        if alias_hit and alias_hit in allowed_citation_ids:
            return alias_hit
        case_hit = str(citation_id_casefold_map.get(cid.lower(), "")).strip()
        if case_hit and case_hit in allowed_citation_ids:
            return case_hit

    mapped = str(citation_lookup.get(
        citation_match_key(law_title, article_no), "")).strip()
    if mapped and mapped in allowed_citation_ids:
        return mapped

    article = normalize_article_no(article_no)
    if not article:
        return ""
    candidates = list(article_citation_index.get(article) or [])
    if not candidates:
        return ""
    if len(candidates) == 1:
        only = str(candidates[0]).strip()
        return only if only in allowed_citation_ids else ""

    norm_risk_law = normalize_law_title(law_title)
    if not norm_risk_law:
        return ""
    for candidate_cid in candidates:
        ev = evidence_by_cid.get(str(candidate_cid).strip()) or {}
        ev_law = normalize_law_title(
            ev.get("law_title") or ev.get("title") or "")
        if not ev_law:
            continue
        if ev_law == norm_risk_law or ev_law in norm_risk_law or norm_risk_law in ev_law:
            return str(candidate_cid).strip()
    return ""


def prepare_evidence_context(
    evidence_items: List[Dict[str, Any]],
    whitelist_limit: int,
) -> Dict[str, Any]:
    legal_catalog = build_legal_catalog(evidence_items)
    citation_lookup = build_citation_lookup(evidence_items)
    allowed_citation_ids = {
        str(it.get("citation_id") or "").strip()
        for it in evidence_items
        if str(it.get("citation_id") or "").strip()
    }
    evidence_by_cid = {
        str(it.get("citation_id") or "").strip(): it
        for it in evidence_items
        if str(it.get("citation_id") or "").strip()
    }
    citation_id_casefold_map = {
        str(it.get("citation_id") or "").strip().lower(): str(it.get("citation_id") or "").strip()
        for it in evidence_items
        if str(it.get("citation_id") or "").strip()
    }
    article_citation_index = _build_article_citation_index(
        evidence_items, allowed_citation_ids)
    evidence_whitelist_text = build_evidence_whitelist_text(
        evidence_items, limit=max(1, int(whitelist_limit or 1)))
    return {
        "legal_catalog": legal_catalog,
        "citation_lookup": citation_lookup,
        "allowed_citation_ids": allowed_citation_ids,
        "evidence_by_cid": evidence_by_cid,
        "citation_id_casefold_map": citation_id_casefold_map,
        "article_citation_index": article_citation_index,
        "evidence_whitelist_text": evidence_whitelist_text,
    }
