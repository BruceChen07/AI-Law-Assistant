import logging
import os
import json
import shutil
import time
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple

from app.api.schemas import SearchQuery
from app.services.search import search_regulations
from app.services.audit_utils import _safe_bool, _safe_float, _safe_int, _chunk_contract_text
from app.services.audit_tax import _tax_query_prefix, _tax_relevance_score

logger = logging.getLogger("law_assistant")
_LAST_RAG_DEBUG_CLEANUP_AT: Dict[str, float] = {}


def _build_global_embedder_adapter():
    try:
        from app.core import embedding as core_embedding
    except Exception:
        return None

    class _GlobalEmbedderAdapter:
        def get_registry_status(self):
            return core_embedding.get_registry_status()

        def get_embed_profile(self, lang: Optional[str]):
            return core_embedding.get_embed_profile(lang)

        def compute_embedding(self, text: str, is_query: bool = False, lang: Optional[str] = None):
            return core_embedding.compute_embedding(text, is_query=is_query, lang=lang)

    return _GlobalEmbedderAdapter()


def _resolve_embedder(embedder):
    if embedder is None:
        return _build_global_embedder_adapter(), "missing_embedder"
    required_methods = ("get_registry_status",
                        "get_embed_profile", "compute_embedding")
    if all(callable(getattr(embedder, m, None)) for m in required_methods):
        return embedder, ""
    return _build_global_embedder_adapter(), "invalid_embedder_interface"


def _embedder_has_profile(embedder, lang: str) -> bool:
    getter = getattr(embedder, "get_embed_profile", None)
    if not callable(getter):
        return False
    try:
        return bool(getter(lang))
    except Exception:
        return False


def _rag_trace_options(cfg: Dict[str, Any]) -> Dict[str, Any]:
    enabled = bool(cfg.get("rag_trace_enabled", True))
    trace_dir = str(cfg.get("rag_trace_dir") or "").strip()
    if not trace_dir:
        data_dir = str(cfg.get("data_dir") or "").strip()
        base = data_dir if data_dir else os.path.abspath(
            os.path.join(os.path.dirname(__file__), "../../data"))
        trace_dir = os.path.join(base, "memory", "debug", "rag_retrieval")
    return {
        "enabled": enabled,
        "dir": os.path.abspath(trace_dir),
        "max_chars": max(400, int(cfg.get("rag_trace_max_chars", 3000) or 3000)),
        "max_rows": max(1, int(cfg.get("rag_trace_max_rows", 20) or 20)),
    }


def _clip_for_trace(v: Any, max_chars: int) -> Any:
    if isinstance(v, str):
        s = str(v)
        return s if len(s) <= max_chars else (s[:max_chars] + f"...<truncated:{len(s)-max_chars}>")
    if isinstance(v, dict):
        return {k: _clip_for_trace(val, max_chars) for k, val in v.items()}
    if isinstance(v, list):
        return [_clip_for_trace(x, max_chars) for x in v]
    return v


def _safe_trace_tag(v: Any) -> str:
    s = str(v or "").strip()
    if not s:
        return ""
    out = []
    for ch in s:
        if ch.isalnum() or ch in ("-", "_"):
            out.append(ch)
        else:
            out.append("_")
    return "".join(out).strip("_")


def _cleanup_rag_debug_tree(cfg: Dict[str, Any], base_dir: str) -> None:
    root = os.path.abspath(str(base_dir or "").strip())
    if not root:
        return
    interval = max(
        60, int(cfg.get("contract_audit_debug_cleanup_interval_sec") or 1800))
    now_ts = time.time()
    last_ts = float(_LAST_RAG_DEBUG_CLEANUP_AT.get(root) or 0.0)
    if now_ts - last_ts < interval:
        return
    _LAST_RAG_DEBUG_CLEANUP_AT[root] = now_ts
    retention_days = max(
        1, int(cfg.get("contract_audit_debug_retention_days") or 7))
    archive_enabled = bool(
        cfg.get("contract_audit_debug_archive_before_delete", False))
    archive_dir = str(
        cfg.get("contract_audit_debug_archive_dir") or "").strip()
    archive_root = archive_dir if archive_dir else os.path.join(
        root, "_archive")
    if archive_enabled:
        os.makedirs(archive_root, exist_ok=True)
    now = datetime.utcnow()
    cutoff = now.toordinal() - retention_days
    if not os.path.isdir(root):
        return
    try:
        names = os.listdir(root)
    except Exception:
        return
    for name in names:
        path = os.path.join(root, name)
        if not os.path.isdir(path):
            continue
        try:
            day = datetime.strptime(name, "%Y-%m-%d")
        except Exception:
            continue
        if day.toordinal() <= cutoff:
            if archive_enabled:
                archive_base = os.path.join(
                    archive_root, f"{os.path.basename(root)}_{name}")
                try:
                    shutil.make_archive(
                        archive_base, "zip", root_dir=root, base_dir=name)
                except Exception:
                    pass
            shutil.rmtree(path, ignore_errors=True)


def _write_rag_trace(cfg: Dict[str, Any], trace_id: str, event: str, payload: Dict[str, Any]) -> None:
    opts = _rag_trace_options(cfg)
    if not opts["enabled"]:
        return
    _cleanup_rag_debug_tree(cfg, opts["dir"])
    day = datetime.utcnow().strftime("%Y-%m-%d")
    target_dir = os.path.join(opts["dir"], day)
    os.makedirs(target_dir, exist_ok=True)
    row = _clip_for_trace({"ts": datetime.utcnow().isoformat(
    ), "trace_id": trace_id, "event": str(event or ""), **(payload or {})}, opts["max_chars"])
    file_path = os.path.join(target_dir, "rag_retrieval.jsonl")
    trace_tag = _safe_trace_tag(trace_id)
    try:
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        if trace_tag:
            trace_file_path = os.path.join(target_dir, f"{trace_tag}.jsonl")
            with open(trace_file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("rag_trace_write_failed err=%s", str(e))


def _normalize_retrieval_options(opts: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    o = dict(opts or {})
    mode = str(o.get("audit_mode", "rag")).strip().lower()
    if mode not in ["rag", "baseline"]:
        mode = "rag"
    risk_detection_mode = str(
        o.get("risk_detection_mode", "relaxed")).strip().lower()
    if risk_detection_mode not in ["relaxed", "balanced", "strict"]:
        risk_detection_mode = "relaxed"
    relaxed = risk_detection_mode == "relaxed"
    balanced = risk_detection_mode == "balanced"
    default_candidate = 80 if relaxed else (25 if balanced else 20)
    default_rerank_top_n = 80 if relaxed else (25 if balanced else 20)
    default_top_k_evidence = 20 if relaxed else (12 if balanced else 10)
    default_query_char_limit = 520 if relaxed else (420 if balanced else 360)
    default_chunk_size = 1400 if relaxed else (1200 if balanced else 1000)
    default_max_chunks = 10 if relaxed else (5 if balanced else 3)
    default_per_chunk_top_k = 8 if relaxed else 5
    return {
        "audit_mode": mode,
        "risk_detection_mode": risk_detection_mode,
        "region": str(o.get("region", "")).strip(),
        "industry": str(o.get("industry", "")).strip(),
        "date": str(o.get("date", "")).strip(),
        "use_semantic": _safe_bool(o.get("use_semantic"), True),
        "semantic_weight": _safe_float(o.get("semantic_weight"), 0.6),
        "bm25_weight": _safe_float(o.get("bm25_weight"), 0.4),
        "candidate_size": max(10, min(_safe_int(o.get("candidate_size"), default_candidate), 200)),
        "rerank_enabled": _safe_bool(o.get("rerank_enabled"), True),
        "rerank_top_n": max(1, min(_safe_int(o.get("rerank_top_n"), default_rerank_top_n), 200)),
        "rerank_mode": str(o.get("rerank_mode", "on")).strip() or "on",
        "top_k_evidence": max(1, min(_safe_int(o.get("top_k_evidence"), default_top_k_evidence), 30)),
        "query_char_limit": max(100, min(_safe_int(o.get("query_char_limit"), default_query_char_limit), 2000)),
        "chunk_size": max(300, min(_safe_int(o.get("contract_chunk_size"), default_chunk_size), 6000)),
        "max_chunks": max(1, min(_safe_int(o.get("contract_chunk_max"), default_max_chunks), 30)),
        "per_chunk_top_k": max(1, min(_safe_int(o.get("per_chunk_top_k"), default_per_chunk_top_k), 20)),
        "tax_focus": _safe_bool(o.get("tax_focus"), True),
        "tax_boost": max(0.0, min(_safe_float(o.get("tax_boost"), 0.35 if relaxed else 0.25), 1.0)),
        "tax_filter_to_tax_only": _safe_bool(o.get("tax_filter_to_tax_only"), not relaxed),
        "require_full_coverage": _safe_bool(o.get("require_full_coverage"), False)
    }


def _normalize_lang(v: Any, default: str = "zh") -> str:
    s = str(v or "").strip().lower()
    if s.startswith("en"):
        return "en"
    if s.startswith("zh"):
        return "zh"
    return default


def _build_query_routes(
    query_text: str,
    source_lang: str,
    target_lang: str,
    retrieval_opts: Dict[str, Any],
    cfg: Dict[str, Any],
    translator=None,
) -> List[Tuple[str, str, str]]:
    routes: List[Tuple[str, str, str]] = []
    norm_lang = _normalize_lang(source_lang, default="zh")
    retrieval_lang = _normalize_lang(target_lang, default="zh")
    trans_cfg = cfg.get("translation_config") if isinstance(
        cfg.get("translation_config"), dict) else {}
    translation_enabled = bool(trans_cfg.get("enabled", False))
    mode = str(trans_cfg.get("mode", "dual")).strip().lower()
    if mode not in {"dual", "translate_only"}:
        mode = "dual"
    cross_lang = norm_lang != retrieval_lang
    origin_routes: List[Tuple[str, str, str]] = [
        (query_text, norm_lang, "origin")]
    if retrieval_opts["tax_focus"]:
        origin_routes.append(
            (_tax_query_prefix(norm_lang) + "\n" + query_text, norm_lang, "origin_tax"))
    origin_enabled = mode != "translate_only"
    if translation_enabled and cross_lang and mode == "dual":
        origin_enabled = bool(trans_cfg.get(
            "cross_lang_source_query_enabled", False))
    if origin_enabled:
        routes.extend(origin_routes)
    if translator is None or not translation_enabled or not cross_lang:
        return routes
    translated = translator.translate_query(
        query_text, src_lang=norm_lang, target_lang=retrieval_lang)
    translated_text = str(translated.get("text", "")).strip()
    if translated_text and translated.get("ok"):
        routes.append((translated_text, retrieval_lang, "translated"))
        if retrieval_opts["tax_focus"]:
            routes.append((_tax_query_prefix(retrieval_lang) + "\n" +
                          translated_text, retrieval_lang, "translated_tax"))
    elif not routes:
        routes.extend(origin_routes)
    return routes


def _split_tax_prefixed_query(route: str, query_text: str) -> Tuple[str, str]:
    text = str(query_text or "").strip()
    if not route.endswith("_tax"):
        return text, text
    if "\n" not in text:
        return text, text
    semantic_query = text.split("\n", 1)[1].strip()
    if not semantic_query:
        return text, text
    return text, semantic_query


def _resolve_rag_search_languages(cfg: Dict[str, Any], source_lang: str) -> List[str]:
    src = _normalize_lang(source_lang, default="zh")
    if not bool(cfg.get("rag_dual_search_enabled", False)):
        default_target = _normalize_lang(
            cfg.get("retrieval_regulation_language", src), default=src)
        return [default_target]
    out: List[str] = [src]
    raw_targets = cfg.get("rag_search_languages")
    if isinstance(raw_targets, list):
        for item in raw_targets:
            norm = _normalize_lang(item, default="")
            if norm and norm not in out:
                out.append(norm)
    else:
        db_paths = cfg.get("rag_db_paths") if isinstance(
            cfg.get("rag_db_paths"), dict) else {}
        for k in db_paths.keys():
            norm = _normalize_lang(k, default="")
            if norm and norm not in out:
                out.append(norm)
    fallback = _normalize_lang(
        cfg.get("retrieval_regulation_language", "zh"), default="zh")
    if fallback not in out:
        out.append(fallback)
    return out


def _retrieve_regulation_evidence(
    cfg: Dict[str, Any],
    text: str,
    lang: str,
    retrieval_opts: Dict[str, Any],
    embedder=None,
    reranker=None,
    translator=None
) -> Dict[str, Any]:
    if retrieval_opts["audit_mode"] != "rag":
        return {"used": False, "queries": 0, "items": [], "chunk_total": 0, "query_success": 0, "query_failed": 0, "failed_chunks": [], "retrieval_degraded": False, "retrieval_degraded_reasons": []}
    retrieval_degraded_reasons: List[str] = []
    active_embedder, degraded_reason = _resolve_embedder(embedder)
    if degraded_reason:
        retrieval_degraded_reasons.append(degraded_reason)
    if active_embedder is None:
        return {"used": False, "queries": 0, "items": [], "chunk_total": 0, "query_success": 0, "query_failed": 0, "failed_chunks": [], "retrieval_degraded": True, "retrieval_degraded_reasons": retrieval_degraded_reasons + ["embedder_unavailable"]}
    chunks = _chunk_contract_text(
        text, retrieval_opts["chunk_size"], retrieval_opts["max_chunks"])
    if not chunks:
        return {"used": False, "queries": 0, "items": [], "chunk_total": 0, "query_success": 0, "query_failed": 0, "failed_chunks": [], "retrieval_degraded": len(retrieval_degraded_reasons) > 0, "retrieval_degraded_reasons": retrieval_degraded_reasons}
    merged = {}
    query_count = 0
    success_count = 0
    failed_chunks = []
    trace_id = datetime.utcnow().strftime("%Y%m%d-%H%M%S-%f")
    _write_rag_trace(cfg, trace_id, "rag_retrieval_start", {
        "lang": _normalize_lang(lang, default="zh"),
        "chunk_total": len(chunks),
        "retrieval_opts": retrieval_opts,
        "rag_search_languages": _resolve_rag_search_languages(cfg, lang),
    })
    source_lang = _normalize_lang(lang, default="zh")
    rag_targets = _resolve_rag_search_languages(cfg, source_lang)
    for chunk_idx, chunk in enumerate(chunks, start=1):
        query = chunk[:retrieval_opts["query_char_limit"]].strip()
        if not query:
            continue
        query_text = query
        for target_lang in rag_targets:
            queries_to_run = _build_query_routes(
                query_text, source_lang, target_lang, retrieval_opts, cfg, translator=translator)
            dedup = {}
            for q_text, q_lang, q_route in queries_to_run:
                dedup[(q_text, q_lang, q_route)] = True
            for q_text, q_lang, q_route in dedup.keys():
                bm25_query_text, semantic_query_text = _split_tax_prefixed_query(
                    q_route, q_text)
                q = SearchQuery(
                    query=semantic_query_text,
                    bm25_query=bm25_query_text,
                    semantic_query=semantic_query_text,
                    language=q_lang,
                    top_k=retrieval_opts["per_chunk_top_k"],
                    date=retrieval_opts["date"] or None,
                    region=retrieval_opts["region"] or None,
                    industry=retrieval_opts["industry"] or None,
                    use_semantic=bool(retrieval_opts["use_semantic"]) and _embedder_has_profile(
                        active_embedder, q_lang),
                    semantic_weight=retrieval_opts["semantic_weight"],
                    bm25_weight=retrieval_opts["bm25_weight"],
                    candidate_size=retrieval_opts["candidate_size"],
                    rerank_enabled=retrieval_opts["rerank_enabled"],
                    rerank_top_n=retrieval_opts["rerank_top_n"],
                    rerank_mode=retrieval_opts["rerank_mode"]
                )
                if bool(retrieval_opts["use_semantic"]) and not bool(q.use_semantic):
                    reason = f"semantic_disabled_no_profile:{_normalize_lang(q_lang, default='zh')}"
                    if reason not in retrieval_degraded_reasons:
                        retrieval_degraded_reasons.append(reason)
                _write_rag_trace(cfg, trace_id, "rag_query", {
                    "chunk_index": chunk_idx,
                    "route": q_route,
                    "query": q_text,
                    "query_lang": q_lang,
                    "target_rag_lang": target_lang,
                    "search_query": q.model_dump(),
                })
                try:
                    try:
                        rows = search_regulations(
                            cfg, q, active_embedder, reranker, target_rag_lang=target_lang)
                    except TypeError:
                        rows = search_regulations(
                            cfg, q, active_embedder, reranker)
                    success_count += 1
                    _write_rag_trace(cfg, trace_id, "rag_query_result", {
                        "chunk_index": chunk_idx,
                        "route": q_route,
                        "query": q_text,
                        "query_lang": q_lang,
                        "target_rag_lang": target_lang,
                        "hit_count": len(rows),
                        "hits": list(rows or [])[:_rag_trace_options(cfg)["max_rows"]],
                    })
                except Exception as e:
                    logger.exception("audit_retrieval_failed query_prefix=%s route=%s lang=%s target_rag_lang=%s",
                                     q_text[:80], q_route, q_lang, target_lang)
                    failed_chunks.append({
                        "query_prefix": q_text[:80],
                        "route": q_route,
                        "lang": q_lang,
                        "target_rag_lang": target_lang,
                        "error": str(e)
                    })
                    _write_rag_trace(cfg, trace_id, "rag_query_error", {
                        "chunk_index": chunk_idx,
                        "route": q_route,
                        "query": q_text,
                        "query_lang": q_lang,
                        "target_rag_lang": target_lang,
                        "error": str(e),
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
    _write_rag_trace(cfg, trace_id, "rag_retrieval_done", {
        "queries": query_count,
        "query_success": success_count,
        "query_failed": max(0, query_count - success_count),
        "final_item_count": len(items),
        "final_items": items,
    })
    return {
        "used": len(items) > 0,
        "queries": query_count,
        "items": items,
        "chunk_total": len(chunks),
        "query_success": success_count,
        "query_failed": max(0, query_count - success_count),
        "failed_chunks": failed_chunks,
        "retrieval_degraded": len(retrieval_degraded_reasons) > 0,
        "retrieval_degraded_reasons": retrieval_degraded_reasons,
    }
