import logging
from typing import Dict, Any, Optional

from app.api.schemas import SearchQuery
from app.services.search import search_regulations
from app.services.audit_utils import _safe_bool, _safe_float, _safe_int, _chunk_contract_text
from app.services.audit_tax import _tax_query_prefix, _tax_relevance_score

logger = logging.getLogger("law_assistant")


def _normalize_retrieval_options(opts: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    o = dict(opts or {})
    mode = str(o.get("audit_mode", "rag")).strip().lower()
    if mode not in ["rag", "baseline"]:
        mode = "rag"
    risk_detection_mode = str(o.get("risk_detection_mode", "relaxed")).strip().lower()
    if risk_detection_mode not in ["relaxed", "balanced", "strict"]:
        risk_detection_mode = "relaxed"
    relaxed = risk_detection_mode == "relaxed"
    return {
        "audit_mode": mode,
        "risk_detection_mode": risk_detection_mode,
        "region": str(o.get("region", "")).strip(),
        "industry": str(o.get("industry", "")).strip(),
        "date": str(o.get("date", "")).strip(),
        "use_semantic": _safe_bool(o.get("use_semantic"), True),
        "semantic_weight": _safe_float(o.get("semantic_weight"), 0.6),
        "bm25_weight": _safe_float(o.get("bm25_weight"), 0.4),
        "candidate_size": max(10, min(_safe_int(o.get("candidate_size"), 80 if relaxed else 50), 200)),
        "rerank_enabled": _safe_bool(o.get("rerank_enabled"), True),
        "rerank_top_n": max(1, min(_safe_int(o.get("rerank_top_n"), 80 if relaxed else 50), 200)),
        "rerank_mode": str(o.get("rerank_mode", "on")).strip() or "on",
        "top_k_evidence": max(1, min(_safe_int(o.get("top_k_evidence"), 20 if relaxed else 12), 30)),
        "query_char_limit": max(100, min(_safe_int(o.get("query_char_limit"), 520 if relaxed else 360), 2000)),
        "chunk_size": max(300, min(_safe_int(o.get("contract_chunk_size"), 1400 if relaxed else 1200), 6000)),
        "max_chunks": max(1, min(_safe_int(o.get("contract_chunk_max"), 10 if relaxed else 6), 30)),
        "per_chunk_top_k": max(1, min(_safe_int(o.get("per_chunk_top_k"), 8 if relaxed else 5), 20)),
        "tax_focus": _safe_bool(o.get("tax_focus"), True),
        "tax_boost": max(0.0, min(_safe_float(o.get("tax_boost"), 0.35 if relaxed else 0.25), 1.0)),
        "tax_filter_to_tax_only": _safe_bool(o.get("tax_filter_to_tax_only"), not relaxed),
        "require_full_coverage": _safe_bool(o.get("require_full_coverage"), False)
    }


def _retrieve_regulation_evidence(
    cfg: Dict[str, Any],
    text: str,
    lang: str,
    retrieval_opts: Dict[str, Any],
    embedder=None,
    reranker=None
) -> Dict[str, Any]:
    if retrieval_opts["audit_mode"] != "rag":
        return {"used": False, "queries": 0, "items": [], "chunk_total": 0, "query_success": 0, "query_failed": 0, "failed_chunks": []}
    if embedder is None:
        return {"used": False, "queries": 0, "items": [], "chunk_total": 0, "query_success": 0, "query_failed": 0, "failed_chunks": []}
    chunks = _chunk_contract_text(
        text, retrieval_opts["chunk_size"], retrieval_opts["max_chunks"])
    if not chunks:
        return {"used": False, "queries": 0, "items": [], "chunk_total": 0, "query_success": 0, "query_failed": 0, "failed_chunks": []}
    merged = {}
    query_count = 0
    success_count = 0
    failed_chunks = []
    for chunk in chunks:
        query = chunk[:retrieval_opts["query_char_limit"]].strip()
        if not query:
            continue
        query_text = query
        queries_to_run = [query_text]

        # 如果开启了税务聚焦，我们采用"双路召回"：
        # 一路用原文查（保证常规法律风险不丢失）
        # 一路加税务前缀查（强化税务风险的召回）
        if retrieval_opts["tax_focus"]:
            tax_query_text = _tax_query_prefix(lang) + "\n" + query
            queries_to_run.append(tax_query_text)

        for q_text in queries_to_run:
            q = SearchQuery(
                query=q_text,
                language=lang,
                top_k=retrieval_opts["per_chunk_top_k"],
                date=retrieval_opts["date"] or None,
                region=retrieval_opts["region"] or None,
                industry=retrieval_opts["industry"] or None,
                use_semantic=retrieval_opts["use_semantic"],
                semantic_weight=retrieval_opts["semantic_weight"],
                bm25_weight=retrieval_opts["bm25_weight"],
                candidate_size=retrieval_opts["candidate_size"],
                rerank_enabled=retrieval_opts["rerank_enabled"],
                rerank_top_n=retrieval_opts["rerank_top_n"],
                rerank_mode=retrieval_opts["rerank_mode"]
            )
            try:
                rows = search_regulations(cfg, q, embedder, reranker)
                success_count += 1
            except Exception as e:
                logger.exception(
                    "audit_retrieval_failed query_prefix=%s", q_text[:80])
                failed_chunks.append({
                    "query_prefix": q_text[:80],
                    "error": str(e)
                })
                rows = []
            query_count += 1
            for r in rows:
                cid = str(r.get("citation_id", "")).strip()
                if not cid:
                    continue
                score = _safe_float(r.get("final_score", 0.0), 0.0)
                tax_score = _tax_relevance_score(
                    r) if retrieval_opts["tax_focus"] else 0
                rank_score = score + \
                    (retrieval_opts["tax_boost"] * float(tax_score))
                row = dict(r)
                row["tax_relevance"] = tax_score
                row["rank_score"] = rank_score
                found = merged.get(cid)
                if found is None or rank_score > _safe_float(found.get("rank_score", 0.0), 0.0):
                    merged[cid] = row
    items = list(merged.values())
    items.sort(
        key=lambda x: (
            _safe_float(x.get("rank_score", 0.0), 0.0),
            _safe_float(x.get("final_score", 0.0), 0.0)
        ),
        reverse=True
    )
    if retrieval_opts["tax_focus"] and retrieval_opts.get("tax_filter_to_tax_only"):
        tax_items = [
            x for x in items
            if _safe_int(x.get("tax_relevance", 0), 0) > 0
        ]
        if tax_items:
            items = tax_items
    items = items[:retrieval_opts["top_k_evidence"]]
    return {
        "used": len(items) > 0,
        "queries": query_count,
        "items": items,
        "chunk_total": len(chunks),
        "query_success": success_count,
        "query_failed": max(0, query_count - success_count),
        "failed_chunks": failed_chunks
    }
