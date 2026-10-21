"""Small request-scoped helpers for the contract routes."""
from typing import Optional, Dict, Any

from fastapi import Request

from app.services.audit_utils import _normalize_lang

def _build_digitization_meta(record: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    row = record if isinstance(record, dict) else {}
    return {
        "digitization_used": bool(row.get("digitization_used")),
        "digitization_reused": bool(row.get("reused")),
        "digitization_status": str(row.get("status") or ""),
        "digitization_version": int(row.get("version_no") or 0),
        "digitization_source_sha256": str(row.get("source_sha256") or ""),
        "digitized_docx_available": bool(str(row.get("digitized_docx_path") or "").strip()),
        "digitized_docx_path": str(row.get("digitized_docx_path") or ""),
        "digitization_text_match_ratio": float(row.get("text_match_ratio") or 0),
        "digitization_paragraph_count": int(row.get("paragraph_count") or 0),
        "digitization_table_count": int(row.get("table_count") or 0),
        "digitization_image_count": int(row.get("image_count") or 0),
        "digitization_page_count": int(row.get("page_count") or 0),
        "digitization_layout_retained": bool(row.get("layout_retained")),
        "digitization_ocr_engine": str(row.get("ocr_engine") or ""),
    }


def _resolve_request_ip(request: Optional[Request]) -> str:
    if request is None:
        return ""
    forwarded = str(request.headers.get("x-forwarded-for") or "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = str(request.headers.get("x-real-ip") or "").strip()
    if real_ip:
        return real_ip
    client = getattr(request, "client", None)
    return str(getattr(client, "host", "") or "")


def _build_retrieval_options(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "audit_mode": payload.get("audit_mode", "rag"),
        "risk_detection_mode": payload.get("risk_detection_mode", "balanced"),
        "region": payload.get("region", ""),
        "date": payload.get("date", ""),
        "industry": payload.get("industry", ""),
        "tax_focus": payload.get("tax_focus", "true"),
        "use_semantic": payload.get("use_semantic"),
        "semantic_weight": payload.get("semantic_weight"),
        "bm25_weight": payload.get("bm25_weight"),
        "candidate_size": payload.get("candidate_size", "25"),
        "rerank_enabled": payload.get("rerank_enabled"),
        "rerank_top_n": payload.get("rerank_top_n", "25"),
        "rerank_mode": payload.get("rerank_mode", "on"),
        "top_k_evidence": payload.get("top_k_evidence"),
        "query_char_limit": payload.get("query_char_limit"),
        "contract_chunk_size": payload.get("contract_chunk_size"),
        "contract_chunk_max": payload.get("contract_chunk_max", "5"),
        "per_chunk_top_k": payload.get("per_chunk_top_k", "5"),
    }
