from __future__ import annotations

import csv
import io
import json
from typing import Any, Dict, List

from app.services.audit_utils import _normalize_risk_level
from app.services.contract_audit_modules.risk_suppression import (
    build_global_tax_context,
    reconcile_cross_clause_conflicts,
)
from app.services.risk_rule_engine import (
    are_risks_duplicates,
    build_risk_signature,
    choose_preferred_risk,
    get_risk_rule_engine_config,
)


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def aggregate_chunk_audit_results(
    *,
    cfg: Dict[str, Any],
    chunk_audit_results: List[Dict[str, Any]],
    preview_clauses: List[Dict[str, Any]],
    lang: str,
) -> Dict[str, Any]:
    rule_config = get_risk_rule_engine_config(cfg)
    deduped_risks: List[Dict[str, Any]] = []
    duplicate_items: List[Dict[str, Any]] = []
    citations: List[Dict[str, Any]] = []
    citation_seen = set()
    review_items: List[Dict[str, Any]] = []

    for chunk in list(chunk_audit_results or []):
        if not isinstance(chunk, dict):
            continue
        chunk_id = str(chunk.get("chunk_id") or "")
        if chunk.get("parse_failed_flag"):
            review_items.append(
                {"chunk_id": chunk_id, "reason": "parse_failed", "severity": "high"})
        if chunk.get("truncated_flag"):
            review_items.append(
                {"chunk_id": chunk_id, "reason": "truncated", "severity": "high"})
        for citation in list(chunk.get("evidence_links") or []):
            if not isinstance(citation, dict):
                continue
            key = (
                str(citation.get("citation_id") or "").strip(),
                str(citation.get("law_title") or "").strip(),
                str(citation.get("article_no") or "").strip(),
            )
            if key in citation_seen:
                continue
            citation_seen.add(key)
            citations.append(citation)
        for risk in list(chunk.get("risk_items") or []):
            if not isinstance(risk, dict):
                continue
            matched_index = -1
            matched_reason = ""
            for index, current in enumerate(deduped_risks):
                is_dup, dup_reason = are_risks_duplicates(
                    current, risk, rule_config)
                if is_dup:
                    matched_index = index
                    matched_reason = dup_reason
                    break
            if matched_index < 0:
                deduped_risks.append(risk)
                continue
            preferred, removed = choose_preferred_risk(
                deduped_risks[matched_index], risk, rule_config)
            deduped_risks[matched_index] = preferred
            duplicate_items.append(
                {
                    "reason": matched_reason,
                    "kept_signature": build_risk_signature(preferred),
                    "removed_signature": build_risk_signature(removed),
                    "chunk_id": chunk_id,
                }
            )

    global_tax_context = build_global_tax_context(preview_clauses)
    kept_risks, removed_conflicts = reconcile_cross_clause_conflicts(
        deduped_risks, preview_clauses, global_tax_context)

    if rule_config.get("require_citation_for_high_risk", True):
        for risk in kept_risks:
            if _normalize_risk_level(risk.get("level")) != "high":
                continue
            if str(risk.get("citation_id") or "").strip():
                continue
            if str(risk.get("law_title") or risk.get("law_reference") or "").strip():
                continue
            location = risk.get("location") if isinstance(
                risk.get("location"), dict) else {}
            review_items.append(
                {
                    "chunk_id": "",
                    "reason": "high_risk_missing_citation",
                    "severity": "high",
                    "risk_id": str(location.get("risk_id") or ""),
                    "clause_id": str(location.get("clause_id") or ""),
                }
            )

    risk_summary = {"high": 0, "medium": 0, "low": 0}
    for risk in kept_risks:
        risk_summary[_normalize_risk_level(risk.get("level"))] += 1

    summary = (
        f"多轮聚合完成，共保留 {len(kept_risks)} 项风险，去重 {len(duplicate_items)} 项，"
        f"冲突抑制 {len(removed_conflicts)} 项"
        if str(lang or "").lower() != "en"
        else f"Aggregation completed, kept {len(kept_risks)} risks, deduped {len(duplicate_items)} and suppressed {len(removed_conflicts)} conflicts"
    )

    csv_buf = io.StringIO()
    writer = csv.writer(csv_buf)
    writer.writerow(["level", "issue", "law_title", "article_no",
                    "citation_id", "clause_id", "page_no", "confidence"])
    for risk in kept_risks:
        location = risk.get("location") if isinstance(
            risk.get("location"), dict) else {}
        writer.writerow([
            risk.get("level", ""),
            risk.get("issue", ""),
            risk.get("law_title", ""),
            risk.get("article_no", ""),
            risk.get("citation_id", ""),
            location.get("clause_id", ""),
            location.get("page_no", 0),
            location.get("score", 0.0),
        ])

    export_payloads = {
        "json": json.dumps(
            {
                "summary": summary,
                "risk_summary": risk_summary,
                "risks": kept_risks,
                "citations": citations,
                "review_items": review_items,
            },
            ensure_ascii=False,
        ),
        "csv": csv_buf.getvalue(),
    }

    return {
        "audit": {
            "summary": summary,
            "executive_opinion": [],
            "risk_summary": risk_summary,
            "risks": kept_risks,
            "citations": citations,
            "legal_validation": {
                "ok": len(review_items) == 0,
                "issues": review_items,
            },
        },
        "meta": {
            "duplicate_items_removed": len(duplicate_items),
            "conflict_items_removed": len(removed_conflicts),
            "review_item_count": len(review_items),
            "aggregation_rule_config": rule_config,
            "removed_conflicts": removed_conflicts,
            "duplicate_items": duplicate_items,
        },
        "exports": export_payloads,
    }
