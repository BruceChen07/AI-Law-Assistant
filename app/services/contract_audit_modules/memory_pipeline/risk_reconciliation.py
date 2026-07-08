"""Risk reconciliation and post-processing for memory audit pipeline."""

from typing import Any, Dict, List, Set, Tuple
import re

from app.services.audit_tax import _is_tax_related_text
from app.services.audit_utils import _normalize_risk_level
from app.services.contract_audit_modules.risk_suppression import (
    should_suppress_missing_risk,
    reconcile_cross_clause_conflicts,
    detect_zero_risk_fallback_hit,
)
from app.services.contract_audit_modules.memory_pipeline.evidence_builder import (
    _resolve_risk_citation_id,
    _select_fallback_citation,
)


def _risk_level_weight(level: str) -> int:
    lv = _normalize_risk_level(level)
    if lv == "high":
        return 3
    if lv == "medium":
        return 2
    return 1


def _normalize_risk_text(value: Any) -> str:
    text = str(value or "").lower().strip()
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^\w\u4e00-\u9fff ]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _is_invoice_type_conflict_text(text: str) -> bool:
    t = _normalize_risk_text(text)
    if not t:
        return False
    has_invoice = any(k in t for k in ["发票", "invoice", "vat"])
    has_special = any(k in t for k in ["专用", "special"])
    has_general = any(k in t for k in ["普通", "general"])
    has_conflict = any(
        k in t for k in ["冲突", "矛盾", "不一致", "conflict", "inconsistent", "contradict"])
    return has_invoice and has_special and has_general and has_conflict


def _risk_dedupe_key(item: Dict[str, Any]) -> str:
    merged = " ".join([
        str(item.get("issue") or ""),
        str(item.get("suggestion") or ""),
        str(item.get("evidence") or ""),
    ])
    if _is_invoice_type_conflict_text(merged):
        return "topic:invoice_type_conflict"
    issue = _normalize_risk_text(item.get("issue"))
    if not issue:
        return ""
    return f"issue:{issue[:96]}"


def _is_better_risk_candidate(candidate: Dict[str, Any], existing: Dict[str, Any]) -> bool:
    c_loc = candidate.get("location") if isinstance(
        candidate.get("location"), dict) else {}
    e_loc = existing.get("location") if isinstance(
        existing.get("location"), dict) else {}
    c_rank = (
        _risk_level_weight(candidate.get("level")),
        1 if str(candidate.get("citation_status") or "") == "mapped" else 0,
        float(c_loc.get("score") or 0.0),
        len(str(candidate.get("evidence") or "")),
        len(str(candidate.get("suggestion") or "")),
    )
    e_rank = (
        _risk_level_weight(existing.get("level")),
        1 if str(existing.get("citation_status") or "") == "mapped" else 0,
        float(e_loc.get("score") or 0.0),
        len(str(existing.get("evidence") or "")),
        len(str(existing.get("suggestion") or "")),
    )
    return c_rank > e_rank


def _dedupe_similar_risks(risks: List[Dict[str, Any]], log_limit: int = 30) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    if not risks:
        return risks, []
    selected: List[Dict[str, Any]] = []
    index_by_key: Dict[str, int] = {}
    removed_hits: List[Dict[str, Any]] = []
    for item in risks:
        if not isinstance(item, dict):
            continue
        key = _risk_dedupe_key(item)
        if not key:
            selected.append(item)
            continue
        if key not in index_by_key:
            index_by_key[key] = len(selected)
            selected.append(item)
            continue
        idx = index_by_key[key]
        kept = selected[idx]
        dropped = item
        if _is_better_risk_candidate(item, kept):
            selected[idx] = item
            dropped = kept
        if len(removed_hits) < max(1, int(log_limit or 1)):
            dropped_loc = dropped.get("location") if isinstance(
                dropped.get("location"), dict) else {}
            kept_loc = selected[idx].get("location") if isinstance(
                selected[idx].get("location"), dict) else {}
            removed_hits.append(
                {
                    "dedupe_key": key,
                    "dropped_risk_id": str(dropped_loc.get("risk_id") or ""),
                    "dropped_clause_id": str(dropped_loc.get("clause_id") or ""),
                    "kept_risk_id": str(kept_loc.get("risk_id") or ""),
                    "kept_clause_id": str(kept_loc.get("clause_id") or ""),
                    "dropped_issue": str(dropped.get("issue") or ""),
                }
            )
    return selected, removed_hits


def process_report_risks(
    risks: List[Dict[str, Any]],
    preview_clauses: List[Dict[str, Any]],
    clause_map: Dict[str, Dict[str, Any]],
    evidence_items: List[Dict[str, Any]],
    norm_lang: str,
    cfg: Dict[str, Any],
    allowed_citation_ids: Set[str],
    citation_lookup: Dict[str, str],
    citation_alias_map: Dict[str, str],
    citation_id_casefold_map: Dict[str, str],
    article_citation_index: Dict[str, List[str]],
    evidence_by_cid: Dict[str, Dict[str, Any]],
    global_tax_context: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    normalized_risks: List[Dict[str, Any]] = []
    dropped_non_whitelist = 0
    retained_unmapped_risks = 0
    retained_unmapped_risk_hits: List[Dict[str, Any]] = []
    suppressed_missing_risks = 0
    suppressed_missing_risk_hits: List[Dict[str, Any]] = []
    fallback_generated_risks = 0
    fallback_generated_risk_hits: List[Dict[str, Any]] = []
    filtered_unverifiable_risks = 0
    filtered_unverifiable_risk_hits: List[Dict[str, Any]] = []
    dedup_similar_risks = bool(cfg.get("memory_dedupe_similar_risks", True))
    deduped_similar_risks = 0
    deduped_similar_risk_hits: List[Dict[str, Any]] = []
    dedupe_log_limit = max(1, int(cfg.get("memory_dedupe_risk_log_limit") or 30))
    filter_unverifiable_risks = bool(cfg.get("memory_filter_unverifiable_risks", True))
    filtered_risk_log_limit = max(1, int(cfg.get("memory_filtered_risk_log_limit") or 30))

    for r in risks:
        if not isinstance(r, dict):
            continue
        cid = str(r.get("clause_id") or "")
        c = clause_map.get(cid) or {}
        law_title = str(r.get("law_title") or "")
        article_no = str(r.get("article_no") or "")
        input_citation_id = str(r.get("citation_id") or "").strip()
        citation_id = _resolve_risk_citation_id(
            citation_id_raw=input_citation_id,
            law_title=law_title,
            article_no=article_no,
            allowed_citation_ids=allowed_citation_ids,
            citation_lookup=citation_lookup,
            citation_alias_map=citation_alias_map,
            citation_id_casefold_map=citation_id_casefold_map,
            article_citation_index=article_citation_index,
            evidence_by_cid=evidence_by_cid,
        )
        if input_citation_id and input_citation_id not in allowed_citation_ids and not citation_id:
            dropped_non_whitelist += 1
        has_mapping = bool(citation_id and citation_id in allowed_citation_ids)
        if not has_mapping:
            retained_unmapped_risks += 1
            retained_unmapped_risk_hits.append(
                {
                    "risk_id": str(r.get("risk_id") or ""),
                    "clause_id": cid,
                    "law_title": law_title,
                    "article_no": article_no,
                }
            )
        basis = f"{law_title} {article_no}".strip()
        risk_item = {
            "level": _normalize_risk_level(r.get("level")),
            "issue": str(r.get("issue") or ""),
            "suggestion": str(r.get("suggestion") or ""),
            "basis": basis,
            "law_reference": basis,
            "citation_id": citation_id if has_mapping else "",
            "citation_status": "mapped" if has_mapping else "unmapped",
            "evidence": str(r.get("evidence") or ""),
            "law_title": law_title,
            "article_no": article_no,
            "location": {
                "risk_id": str(r.get("risk_id") or ""),
                "clause_id": cid,
                "anchor_id": str(c.get("anchor_id") or ""),
                "page_no": int(c.get("page_no") or 0),
                "paragraph_no": str(c.get("paragraph_no") or ""),
                "clause_path": str(c.get("clause_path") or ""),
                "quote": str(r.get("evidence") or ""),
                "score": round(float(r.get("confidence") or 0.0), 4),
            },
        }
        suppress, hit = should_suppress_missing_risk(
            risk_item, preview_clauses, cid, global_tax_context=global_tax_context
        )
        if suppress:
            suppressed_missing_risks += 1
            suppressed_missing_risk_hits.append(
                {
                    "risk_id": str(r.get("risk_id") or ""),
                    "clause_id": cid,
                    "topic": str(hit.get("topic") or ""),
                    "counter_clause_id": str(hit.get("clause_id") or ""),
                    "counter_clause_path": str(hit.get("clause_path") or ""),
                    "counter_page_no": int(hit.get("page_no") or 0),
                    "counter_paragraph_no": str(hit.get("paragraph_no") or ""),
                    "counter_source": str(hit.get("source") or ""),
                }
            )
            continue
        normalized_risks.append(risk_item)

    normalized_risks, reconciled_removed_hits = reconcile_cross_clause_conflicts(
        normalized_risks, preview_clauses, global_tax_context
    )
    reconciled_suppressed_risks = len(reconciled_removed_hits)

    fallback_enabled = bool(cfg.get("memory_zero_risk_fallback_enabled", True))
    if fallback_enabled and not normalized_risks:
        fallback_hit, hit_info = detect_zero_risk_fallback_hit(
            preview_clauses, global_tax_context
        )
        fallback_citation = _select_fallback_citation(
            evidence_items, allowed_citation_ids, norm_lang
        )
        if fallback_hit and fallback_citation:
            fallback_generated_risks = 1
            fallback_generated_risk_hits.append(hit_info)
            fallback_law_title = str(fallback_citation.get("law_title") or "")
            fallback_article_no = str(fallback_citation.get("article_no") or "")
            mapped_fallback_citation_id = str(fallback_citation.get("citation_id") or "")
            mapped_ok = bool(
                mapped_fallback_citation_id and mapped_fallback_citation_id in allowed_citation_ids
            )
            fallback_quote = str(hit_info.get("quote") or "")
            fallback_clause_id = str(hit_info.get("clause_id") or "")
            fallback_clause = clause_map.get(fallback_clause_id) or {}
            issue = "Tax exemption/rate combined with service scenario requires further validation on applicability and tax basis."
            suggestion = "Add explicit eligibility basis, tax base calculation rules, document retention requirements, and align invoice issuance with tax obligation timing."
            if norm_lang != "en":
                issue = "合同存在免税/税率与服务场景组合，适用条件和计税依据需进一步复核。"
                suggestion = "补充免税适用依据、计税口径和留存资料要求，并确认开票与纳税义务安排。"
            normalized_risks.append(
                {
                    "level": "medium",
                    "issue": issue,
                    "suggestion": suggestion,
                    "basis": f"{fallback_law_title} {fallback_article_no}".strip(),
                    "law_reference": f"{fallback_law_title} {fallback_article_no}".strip(),
                    "citation_id": mapped_fallback_citation_id if mapped_ok else "",
                    "citation_status": "mapped" if mapped_ok else "unmapped",
                    "evidence": fallback_quote,
                    "law_title": fallback_law_title,
                    "article_no": fallback_article_no,
                    "location": {
                        "risk_id": "fallback-r1",
                        "clause_id": fallback_clause_id,
                        "anchor_id": str(fallback_clause.get("anchor_id") or ""),
                        "page_no": int(hit_info.get("page_no") or fallback_clause.get("page_no") or 0),
                        "paragraph_no": str(hit_info.get("paragraph_no") or fallback_clause.get("paragraph_no") or ""),
                        "clause_path": str(hit_info.get("clause_path") or fallback_clause.get("clause_path") or ""),
                        "quote": fallback_quote,
                        "score": 0.6,
                    },
                }
            )

    if filter_unverifiable_risks and normalized_risks:
        display_risks: List[Dict[str, Any]] = []
        for item in normalized_risks:
            cid = str(item.get("citation_id") or "").strip()
            mapped_ok = bool(cid and cid in allowed_citation_ids)
            ev = evidence_by_cid.get(cid) if mapped_ok else {}
            full_text = str((ev or {}).get("content") or (ev or {}).get("excerpt") or "").strip()

            risk_issue = str(item.get("issue") or "")
            law_title = str(item.get("law_title") or "")
            evidence = str(item.get("evidence") or "")
            suggestion = str(item.get("suggestion") or "")
            tax_related = _is_tax_related_text(" ".join(
                [risk_issue, law_title, evidence, suggestion]))
            if mapped_ok and full_text:
                display_risks.append(item)
                continue
            if tax_related:
                display_risks.append(item)
                continue
            filtered_unverifiable_risks += 1
            if len(filtered_unverifiable_risk_hits) < filtered_risk_log_limit:
                filtered_unverifiable_risk_hits.append(
                    {
                        "risk_id": str(((item.get("location") or {}).get("risk_id") or "")),
                        "clause_id": str(((item.get("location") or {}).get("clause_id") or "")),
                        "citation_id": cid,
                        "citation_status": str(item.get("citation_status") or ""),
                        "reason": "unmapped_or_no_fulltext",
                        "law_title": str(item.get("law_title") or ""),
                        "article_no": str(item.get("article_no") or ""),
                    }
                )
        normalized_risks = display_risks

    if dedup_similar_risks and normalized_risks:
        before_count = len(normalized_risks)
        normalized_risks, deduped_similar_risk_hits = _dedupe_similar_risks(
            normalized_risks, log_limit=dedupe_log_limit)
        deduped_similar_risks = max(0, before_count - len(normalized_risks))

    risk_summary = {"high": 0, "medium": 0, "low": 0}
    for r in normalized_risks:
        risk_summary[r.get("level", "low")] += 1

    return {
        "normalized_risks": normalized_risks,
        "risk_summary": risk_summary,
        "dropped_non_whitelist": dropped_non_whitelist,
        "retained_unmapped_risks": retained_unmapped_risks,
        "retained_unmapped_risk_hits": retained_unmapped_risk_hits,
        "suppressed_missing_risks": suppressed_missing_risks,
        "suppressed_missing_risk_hits": suppressed_missing_risk_hits,
        "reconciled_suppressed_risks": reconciled_suppressed_risks,
        "reconciled_removed_hits": reconciled_removed_hits,
        "fallback_enabled": fallback_enabled,
        "fallback_generated_risks": fallback_generated_risks,
        "fallback_generated_risk_hits": fallback_generated_risk_hits,
        "filtered_unverifiable_risks": filtered_unverifiable_risks,
        "filtered_unverifiable_risk_hits": filtered_unverifiable_risk_hits,
        "dedupe_similar_risks": dedup_similar_risks,
        "deduped_similar_risks": deduped_similar_risks,
        "deduped_similar_risk_hits": deduped_similar_risk_hits,
    }
