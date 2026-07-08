"""Audit orchestrator.

Stage-3 goal: provide a single, strategy-friendly entrypoint that can dispatch
different audit flows (contract audit vs. tax audit pipeline) while keeping
existing endpoints and outputs stable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional


@dataclass(frozen=True)
class AuditServices:
    """Container for app-level singleton services (typically from app.state)."""

    llm: Any = None
    embedder: Any = None
    reranker: Any = None
    translator: Any = None


def summarize_contract_risks(audit: Dict[str, Any]) -> Dict[str, int]:
    """Build a normalized risk-level summary from contract audit output."""
    summary = {"high": 0, "medium": 0, "low": 0}
    risks = (audit or {}).get("risks")
    if not isinstance(risks, list):
        return summary
    for item in risks:
        if not isinstance(item, dict):
            continue
        level = str(item.get("level") or "medium").strip().lower()
        if level not in summary:
            level = "medium"
        summary[level] += 1
    return summary


def run_contract_audit(
    cfg: Dict[str, Any],
    services: AuditServices,
    *,
    file_path: str,
    lang: str = "zh",
    retrieval_options: Optional[Dict[str, Any]] = None,
    progress_cb: Optional[Callable[[str, int, str], None]] = None,
) -> Dict[str, Any]:
    """Run the contract audit flow (memory/classic depending on config)."""
    from app.services.contract_audit import audit_contract

    return audit_contract(
        cfg=cfg,
        llm=services.llm,
        file_path=file_path,
        lang=lang,
        embedder=services.embedder,
        reranker=services.reranker,
        translator=services.translator,
        retrieval_options=retrieval_options,
        progress_cb=progress_cb,
    )


def run_contract_pipeline_bundle(
    cfg: Dict[str, Any],
    services: AuditServices,
    *,
    file_path: str,
    lang: str = "zh",
    retrieval_options: Optional[Dict[str, Any]] = None,
    progress_cb: Optional[Callable[[str, int, str], None]] = None,
) -> Dict[str, Any]:
    """Run contract audit and return normalized bundle output."""
    result = run_contract_audit(
        cfg,
        services,
        file_path=file_path,
        lang=lang,
        retrieval_options=retrieval_options,
        progress_cb=progress_cb,
    )
    audit = result.get("audit") if isinstance(result, dict) else {}
    if not isinstance(audit, dict):
        audit = {}
    meta = result.get("meta") if isinstance(result, dict) else {}
    if not isinstance(meta, dict):
        meta = {}
    return {
        "audit": audit,
        "meta": meta,
        "risk_summary": summarize_contract_risks(audit),
    }


def tax_analyze_contract(
    cfg: Dict[str, Any],
    services: AuditServices,
    *,
    contract_id: str,
    operator_id: str = "",
) -> Dict[str, Any]:
    """Run tax contract parsing + entity extraction."""
    from app.services.tax_contract_parser import analyze_contract_document

    return analyze_contract_document(
        cfg,
        contract_id=contract_id,
        operator_id=operator_id,
        llm=services.llm,
    )


def tax_match_contract(
    cfg: Dict[str, Any],
    services: AuditServices,
    *,
    contract_id: str,
    operator_id: str = "",
    top_k_per_clause: int = 5,
) -> Dict[str, Any]:
    """Run tax matching (vector retrieval + rule evaluation + LLM fallback)."""
    from app.services.tax_matcher import match_contract_against_rules

    return match_contract_against_rules(
        cfg,
        contract_id=contract_id,
        operator_id=operator_id,
        top_k_per_clause=top_k_per_clause,
        llm=services.llm,
        embedder=services.embedder,
    )


def tax_generate_issues(
    cfg: Dict[str, Any],
    services: AuditServices,
    *,
    contract_id: str,
    operator_id: str = "",
) -> Dict[str, Any]:
    """Generate tax audit issues from match results (LLM-assisted)."""
    from app.services.tax_risk import generate_issues_from_matches

    return generate_issues_from_matches(
        cfg,
        contract_id=contract_id,
        operator_id=operator_id,
        llm=services.llm,
    )


def tax_build_report(cfg: Dict[str, Any], *, contract_id: str) -> Dict[str, Any]:
    """Build tax audit report from persisted issues/traces."""
    from app.services.tax_report import build_tax_audit_report

    return build_tax_audit_report(cfg, contract_id)


def resolve_tax_contract_language(cfg: Dict[str, Any], *, contract_id: str) -> str:
    """Resolve tax contract language from persisted clauses/file info."""
    from app.services.crud import get_tax_contract_document, list_contract_clauses
    from app.services.tax_contract_parser import detect_text_language

    contract = get_tax_contract_document(cfg, contract_id) or {}
    clauses = list_contract_clauses(cfg, contract_id, limit=300)
    source = "\n".join([str(x.get("clause_text") or "") for x in clauses])
    if not source:
        source = str(contract.get("original_filename") or "")
    lang = detect_text_language(source, default="zh")
    return "en" if lang == "en" else "zh"


def run_tax_pipeline_bundle(
    cfg: Dict[str, Any],
    services: AuditServices,
    *,
    contract_id: str,
    operator_id: str = "",
    top_k_per_clause: int = 5,
    include_report: bool = True,
) -> Dict[str, Any]:
    """Run standard tax pipeline: analyze -> match -> issues -> (optional) report."""
    analyze = tax_analyze_contract(
        cfg,
        services,
        contract_id=contract_id,
        operator_id=operator_id,
    )
    match = tax_match_contract(
        cfg,
        services,
        contract_id=contract_id,
        operator_id=operator_id,
        top_k_per_clause=top_k_per_clause,
    )
    issues = tax_generate_issues(
        cfg,
        services,
        contract_id=contract_id,
        operator_id=operator_id,
    )
    report = tax_build_report(
        cfg, contract_id=contract_id) if include_report else {}
    return {
        "contract_id": contract_id,
        "analyze": analyze,
        "match": match,
        "issues": issues,
        "report": report,
    }
