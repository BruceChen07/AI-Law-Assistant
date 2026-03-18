import json
import logging
import os
import re
import asyncio
import threading
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, Any, Optional, List, Callable

from app.core.utils import extract_text_with_config
from app.services.audit_utils import _safe_int, _normalize_citation_item, _enrich_citations, _normalize_risk_level
from app.services.audit_tax import _filter_tax_audit_result
from app.services.audit_prompt import _build_prompt, _estimate_prompt_tokens
from app.services.audit_retrieval import _normalize_retrieval_options, _retrieve_regulation_evidence
from app.services.tax_contract_parser import split_contract_clauses
from app.memory_system.indexer import IndexerConfig, MemoryIndexer, SentenceTransformerEmbedder
from app.memory_system.search import HybridSearcher, HybridSearchConfig
from app.memory_system.manager import MemoryLifecycleManager, MemoryManagerConfig

logger = logging.getLogger("law_assistant")
_MEMORY_EMBEDDER = None


def _build_preview_clauses(text: str) -> List[Dict[str, Any]]:
    clauses = split_contract_clauses(text)
    out = []
    for idx, clause in enumerate(clauses, 1):
        cid = f"c{idx}"
        out.append(
            {
                "clause_id": cid,
                "anchor_id": f"clause-{cid}",
                "clause_path": clause.get("clause_path", ""),
                "page_no": int(clause.get("page_no") or 0),
                "paragraph_no": str(clause.get("paragraph_no") or ""),
                "clause_text": clause.get("clause_text", ""),
            }
        )
    return out


def _norm_text(v: Any) -> str:
    s = re.sub(r"\s+", "", str(v or ""))
    return s.strip()


def _attach_risk_locations(audit: Dict[str, Any], clauses: List[Dict[str, Any]]) -> Dict[str, Any]:
    risks = audit.get("risks")
    if not isinstance(risks, list) or not clauses:
        return audit
    prepared_clauses = []
    for clause in clauses:
        c_text = str(clause.get("clause_text", "") or "")
        prepared_clauses.append((clause, _norm_text(c_text), c_text))
    for idx, risk in enumerate(risks):
        if not isinstance(risk, dict):
            continue
        queries = []
        for key in ["evidence", "issue", "suggestion"]:
            q = str(risk.get(key, "") or "").strip()
            if len(q) >= 6:
                queries.append(q)
        best_clause = None
        best_score = -1.0
        matched_quote = ""
        for clause, normalized_clause_text, raw_clause_text in prepared_clauses:
            local_best = 0.0
            local_quote = ""
            for q in queries:
                qn = _norm_text(q)
                if len(qn) < 6:
                    continue
                if qn in normalized_clause_text:
                    score = 1.0
                else:
                    score = SequenceMatcher(
                        None,
                        qn[:200],
                        normalized_clause_text[:500],
                    ).ratio()
                if score > local_best:
                    local_best = score
                    local_quote = q if qn in normalized_clause_text else raw_clause_text[:120]
            if local_best > best_score:
                best_score = local_best
                best_clause = clause
                matched_quote = local_quote
        if (not best_clause) or best_score < 0.15:
            risk["location"] = {
                "risk_id": f"r{idx + 1}",
                "clause_id": "",
                "anchor_id": "",
                "page_no": 0,
                "paragraph_no": "",
                "clause_path": "",
                "quote": "",
                "score": 0.0,
            }
            continue
        risk["location"] = {
            "risk_id": f"r{idx + 1}",
            "clause_id": best_clause.get("clause_id", ""),
            "anchor_id": best_clause.get("anchor_id", ""),
            "page_no": int(best_clause.get("page_no") or 0),
            "paragraph_no": str(best_clause.get("paragraph_no") or ""),
            "clause_path": best_clause.get("clause_path", ""),
            "quote": matched_quote,
            "score": round(max(0.0, best_score), 4),
        }
    return audit


def _normalize_audit_result(
    parsed: Any,
    raw_text: str,
    evidence_items: List[Dict[str, Any]],
    lang: str,
    tax_only: bool = True
) -> Dict[str, Any]:
    if not isinstance(parsed, dict):
        parsed = {}
    summary = str(parsed.get("summary", "") or "")
    risks = parsed.get("risks")
    if not isinstance(risks, list):
        risks = []
    executive_opinion = parsed.get("executive_opinion")
    if not isinstance(executive_opinion, list):
        executive_opinion = []
    risk_summary = parsed.get("risk_summary")
    if not isinstance(risk_summary, dict):
        risk_summary = {"high": 0, "medium": 0, "low": 0}
    citations = parsed.get("citations")
    if not isinstance(citations, list):
        citations = []
    if not citations and evidence_items:
        citations = [
            _normalize_citation_item(
                {
                    "citation_id": it.get("citation_id", ""),
                    "law_title": it.get("law_title", "") or it.get("title", ""),
                    "title": it.get("title", ""),
                    "article_no": it.get("article_no", ""),
                    "excerpt": it.get("excerpt", ""),
                    "content": it.get("content", ""),
                    "effective_date": it.get("effective_date", ""),
                    "expiry_date": it.get("expiry_date", ""),
                    "region": it.get("region", ""),
                    "industry": it.get("industry", "")
                }
            )
            for it in evidence_items
        ]
    citations = _enrich_citations(citations, evidence_items)
    if tax_only:
        filtered = _filter_tax_audit_result(
            summary=summary,
            executive_opinion=executive_opinion,
            risks=risks,
            citations=citations,
            lang=lang
        )
        summary = filtered["summary"]
        executive_opinion = filtered["executive_opinion"]
        risk_summary = filtered["risk_summary"]
        risks = filtered["risks"]
        citations = filtered["citations"]
    if isinstance(risks, list):
        normalized_summary = {"high": 0, "medium": 0, "low": 0}
        for r in risks:
            if isinstance(r, dict):
                normalized_summary[_normalize_risk_level(r.get("level"))] += 1
        risk_summary = normalized_summary
    out = {
        "summary": summary,
        "executive_opinion": executive_opinion,
        "risk_summary": {
            "high": _safe_int(risk_summary.get("high", 0), 0),
            "medium": _safe_int(risk_summary.get("medium", 0), 0),
            "low": _safe_int(risk_summary.get("low", 0), 0),
        },
        "risks": risks,
        "citations": citations
    }
    if not summary and not risks and raw_text:
        out["raw"] = raw_text
    return out


def _run_coro_sync(coro):
    try:
        asyncio.get_running_loop()
        has_running = True
    except RuntimeError:
        has_running = False
    if not has_running:
        return asyncio.run(coro)
    result_box: Dict[str, Any] = {}
    error_box: Dict[str, Exception] = {}

    def _runner():
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            result_box["value"] = loop.run_until_complete(coro)
        except Exception as e:
            error_box["error"] = e
        finally:
            loop.close()

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join()
    if "error" in error_box:
        raise error_box["error"]
    return result_box.get("value")


def _memory_paths(cfg: Dict[str, Any]) -> tuple[str, str]:
    memory_dir = str(cfg.get("memory_dir") or "").strip()
    if not memory_dir:
        data_dir = str(cfg.get("data_dir") or "").strip()
        if data_dir:
            memory_dir = os.path.join(data_dir, "memory")
        else:
            memory_dir = os.path.abspath(os.path.join(
                os.path.dirname(__file__), "../../memory"))
    memory_db = str(cfg.get("memory_db_path") or "").strip()
    if not memory_db:
        memory_db = os.path.join(memory_dir, "memory.db")
    os.makedirs(memory_dir, exist_ok=True)
    return os.path.abspath(memory_dir), os.path.abspath(memory_db)


def _get_memory_embedder():
    global _MEMORY_EMBEDDER
    if _MEMORY_EMBEDDER is not None:
        return _MEMORY_EMBEDDER
    try:
        _MEMORY_EMBEDDER = SentenceTransformerEmbedder("all-MiniLM-L6-v2")
        logger.info("memory_embedder_ready provider=sentence_transformers")
        return _MEMORY_EMBEDDER
    except Exception as e:
        logger.warning("memory_embedder_fallback reason=%s", str(e))

        class _FallbackEmbedder:
            def encode(self, texts):
                import numpy as np
                out = []
                for text in texts:
                    vec = np.zeros(16, dtype=np.float32)
                    for i, ch in enumerate(str(text)[:256]):
                        vec[i % 16] += (ord(ch) % 37) / 37.0
                    norm = np.linalg.norm(vec) + 1e-12
                    out.append(vec / norm)
                return np.vstack(out) if out else np.zeros((0, 16), dtype=np.float32)
        _MEMORY_EMBEDDER = _FallbackEmbedder()
        return _MEMORY_EMBEDDER


def _normalize_article_no(article: Any) -> str:
    s = str(article or "").strip()
    if not s:
        return ""
    if "条" in s:
        return s
    if s.startswith("第"):
        return f"{s}条"
    return f"第{s}条"


def _citation_match_key(law_title: Any, article_no: Any) -> str:
    law = str(law_title or "").strip().lower()
    article = _normalize_article_no(article_no).lower()
    return f"{law}##{article}"


def _build_citation_lookup(evidence_items: List[Dict[str, Any]]) -> Dict[str, str]:
    lookup: Dict[str, str] = {}
    for it in evidence_items:
        cid = str(it.get("citation_id") or "").strip()
        if not cid:
            continue
        law = str(it.get("law_title") or it.get("title") or "").strip()
        article = str(it.get("article_no") or "").strip()
        key = _citation_match_key(law, article)
        if key:
            lookup[key] = cid
    return lookup


def _build_legal_catalog(evidence_items: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    catalog: Dict[str, set[str]] = {}
    for it in evidence_items:
        law = str(it.get("law_title") or it.get("title") or "").strip()
        article = str(it.get("article_no") or "").strip()
        if not law or not article:
            continue
        if law not in catalog:
            catalog[law] = set()
        catalog[law].add(_normalize_article_no(article))
    return {k: sorted(list(v)) for k, v in catalog.items()}


def _build_evidence_whitelist_text(evidence_items: List[Dict[str, Any]], limit: int = 60) -> str:
    lines: List[str] = []
    for it in evidence_items[:limit]:
        cid = str(it.get("citation_id") or "").strip()
        law = str(it.get("law_title") or it.get("title") or "").strip()
        article = _normalize_article_no(it.get("article_no"))
        if not cid or not law or not article:
            continue
        lines.append(f"- {cid}: {law} {article}")
    return "\n".join(lines)


def _audit_with_memory(
    cfg: Dict[str, Any],
    llm,
    text: str,
    lang: str,
    preview_clauses: List[Dict[str, Any]],
    evidence_items: List[Dict[str, Any]],
    retrieval_opts: Dict[str, Any],
) -> Dict[str, Any]:
    memory_dir, memory_db = _memory_paths(cfg)
    memory_use_long_hits = bool(cfg.get("memory_use_long_hits", True))
    logger.info("memory_pipeline_start memory_dir=%s memory_db=%s evidence=%s",
                memory_dir, memory_db, len(evidence_items))
    embedder = _get_memory_embedder()
    indexer = MemoryIndexer(
        IndexerConfig(memory_root=Path(os.path.abspath(memory_dir)),
                      db_path=Path(os.path.abspath(memory_db))),
        embedder
    )
    indexer.reindex_all()
    searcher = HybridSearcher(indexer, Path(os.path.abspath(
        memory_db)), HybridSearchConfig(vector_weight=0.7, keyword_weight=0.3))
    timeout_sec = min(10.0, max(3.0, float(
        (cfg.get("llm_config") or {}).get("timeout", 8))))
    risk_detection_mode = str(retrieval_opts.get("risk_detection_mode", "relaxed"))
    is_relaxed = risk_detection_mode == "relaxed"
    memory_cfg = MemoryManagerConfig(
        short_memory_token_limit=6000 if is_relaxed else 4000,
        flush_soft_threshold=6000 if is_relaxed else 4000,
        llm_timeout_sec=timeout_sec,
        retrieval_top_k=10 if is_relaxed else 6,
        risk_dedup_similarity_threshold=0.93 if is_relaxed else 0.86,
        risk_dedup_enabled=False,
    )
    manager = MemoryLifecycleManager(
        Path(os.path.abspath(memory_dir)),
        indexer,
        searcher,
        memory_cfg,
    )
    legal_catalog = _build_legal_catalog(evidence_items)
    citation_lookup = _build_citation_lookup(evidence_items)
    allowed_citation_ids = {
        str(it.get("citation_id") or "").strip()
        for it in evidence_items
        if str(it.get("citation_id") or "").strip()
    }
    evidence_whitelist_text = _build_evidence_whitelist_text(evidence_items)

    async def _clause_cb(payload: Dict[str, Any]) -> Dict[str, Any]:
        clause = payload.get("clause") or {}
        short_memory = str(payload.get("short_memory") or "")
        long_memory_hits = str(payload.get("long_memory_hits") or "").strip()
        clause_text = str(clause.get("text") or "")
        clause_title = str(clause.get("title") or "")
        long_memory_block = ""
        if memory_use_long_hits and long_memory_hits:
            long_memory_block = f"长期记忆命中:\n{long_memory_hits}\n\n"
        system = "你是资深合同审计律师。请只输出JSON。"
        user = (
            f"语言: {lang}\n"
            "仅基于以下输入进行审计，不要补充外部事实。\n"
            "仅允许引用下方白名单中的法规条款，不得输出白名单之外的 law_title/article_no。\n"
            "每个风险必须给出 citation_id，且必须来自白名单。\n"
            "尽可能完整识别条款中的显性与隐性风险；不要因为表述相似而主动合并风险。\n"
            f"法规证据白名单:\n{evidence_whitelist_text}\n\n"
            f"短记忆:\n{short_memory}\n\n"
            f"{long_memory_block}"
            f"当前条款标题: {clause_title}\n"
            f"当前条款正文:\n{clause_text}\n\n"
            "输出JSON格式: {\"summary\": \"\", \"risks\": [{\"level\":\"high|medium|low\",\"issue\":\"\",\"suggestion\":\"\",\"citation_id\":\"\",\"law_title\":\"\",\"article_no\":\"\",\"evidence\":\"\",\"confidence\":0.0}]}"
        )
        result_text, _ = llm.chat([{"role": "system", "content": system}, {
                                  "role": "user", "content": user}], overrides={"max_tokens": 1800 if is_relaxed else 1200})
        try:
            parsed = json.loads(result_text)
            if not isinstance(parsed, dict):
                return {"summary": "", "risks": []}
            return parsed
        except Exception:
            return {"summary": "", "risks": []}

    async def _flush_cb(prompt: str) -> str:
        content = f"请把以下上下文压缩为可持久化的Markdown记忆要点：\n\n{prompt}"
        result_text, _ = llm.chat([{"role": "system", "content": "你是记忆压缩助手。"}, {
                                  "role": "user", "content": content}], overrides={"max_tokens": 600})
        return str(result_text or "").strip()

    report = _run_coro_sync(manager.audit_contract(
        text, _clause_cb, _flush_cb, legal_catalog))
    risks = report.get("risks") if isinstance(
        report.get("risks"), list) else []
    clause_map = {str(c.get("clause_id")): c for c in preview_clauses}
    normalized_risks = []
    dropped_non_whitelist = 0
    for r in risks:
        if not isinstance(r, dict):
            continue
        cid = str(r.get("clause_id") or "")
        c = clause_map.get(cid) or {}
        law_title = str(r.get("law_title") or "")
        article_no = str(r.get("article_no") or "")
        citation_id = str(r.get("citation_id") or "").strip()
        if not citation_id:
            citation_id = citation_lookup.get(_citation_match_key(law_title, article_no), "")
        if not citation_id or citation_id not in allowed_citation_ids:
            dropped_non_whitelist += 1
            continue
        basis = f"{law_title} {article_no}".strip()
        normalized_risks.append(
            {
                "level": _normalize_risk_level(r.get("level")),
                "issue": str(r.get("issue") or ""),
                "suggestion": str(r.get("suggestion") or ""),
                "basis": basis,
                "law_reference": basis,
                "citation_id": citation_id,
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
        )
    risk_summary = {"high": 0, "medium": 0, "low": 0}
    for r in normalized_risks:
        risk_summary[r.get("level", "low")] += 1
    audit = {
        "summary": f"条款级长短记忆审核完成，共发现 {len(normalized_risks)} 项风险",
        "executive_opinion": [],
        "risk_summary": risk_summary,
        "risks": normalized_risks,
        "citations": _enrich_citations([], evidence_items),
        "legal_validation": report.get("legal_validation", {"ok": True, "issues": []}),
    }
    logger.info(
        "memory_pipeline_done risks=%s validation_ok=%s",
        len(normalized_risks),
        bool((report.get("legal_validation") or {}).get("ok")),
    )
    return {
        "audit": audit,
        "meta": {
            "memory_mode": True,
            "memory_dir": memory_dir,
            "memory_db": memory_db,
            "memory_use_long_hits": memory_use_long_hits,
            "memory_report_risk_count": len(normalized_risks),
            "memory_validation_ok": bool((report.get("legal_validation") or {}).get("ok", False)),
            "risk_dedup_enabled": False,
            "dropped_non_whitelist_risks": dropped_non_whitelist,
        },
    }


def audit_contract(
    cfg: Dict[str, Any],
    llm,
    file_path: str,
    lang: str = "zh",
    embedder=None,
    reranker=None,
    retrieval_options: Optional[Dict[str, Any]] = None,
    progress_cb: Optional[Callable[[str, int, str], None]] = None
) -> Dict[str, Any]:
    def _report(stage: str, percent: int, message: str = "") -> None:
        if not callable(progress_cb):
            return
        try:
            progress_cb(stage, percent, message)
        except Exception:
            return

    _report("extracting", 15, "extracting text")
    logger.info("audit_extract_start file=%s lang=%s", file_path, lang)
    text, meta = extract_text_with_config(cfg, file_path)
    preview_clauses = _build_preview_clauses(text)
    logger.info(
        "audit_extract_done file=%s text_length=%s ocr_used=%s ocr_engine=%s page_count=%s",
        file_path,
        len(text),
        meta.get("ocr_used"),
        meta.get("ocr_engine"),
        meta.get("page_count")
    )
    _report("extract_done", 30, "extract complete")
    opts = _normalize_retrieval_options(retrieval_options)
    _report("retrieval", 40, "retrieving evidence")
    retrieved = _retrieve_regulation_evidence(
        cfg, text, lang, opts, embedder=embedder, reranker=reranker)
    logger.info(
        "audit_retrieval_done file=%s mode=%s used=%s queries=%s success=%s failed=%s evidence_count=%s",
        file_path,
        opts.get("audit_mode"),
        retrieved.get("used"),
        retrieved.get("queries"),
        retrieved.get("query_success", 0),
        retrieved.get("query_failed", 0),
        len(retrieved.get("items") or [])
    )
    _report("retrieval_done", 55, "evidence ready")
    if opts.get("require_full_coverage") and _safe_int(retrieved.get("query_failed", 0), 0) > 0:
        raise RuntimeError("retrieval coverage incomplete")
    evidence_items = [_normalize_citation_item(
        it) for it in (retrieved.get("items") or [])]
    logger.info("audit_memory_enabled file=%s clauses=%s",
                file_path, len(preview_clauses))
    _report("auditing", 70, "auditing clauses")
    memory_result = _audit_with_memory(
        cfg=cfg,
        llm=llm,
        text=text,
        lang=lang,
        preview_clauses=preview_clauses,
        evidence_items=evidence_items,
        retrieval_opts=opts,
    )
    _report("audit_done", 90, "audit complete")
    memory_meta = memory_result.get("meta") if isinstance(
        memory_result.get("meta"), dict) else {}
    citation_ids = [
        str(it.get("citation_id", "")).strip()
        for it in evidence_items
        if str(it.get("citation_id", "")).strip()
    ]
    return {
        "audit": memory_result.get("audit"),
        "meta": {
            "text_length": len(text),
            "ocr_used": meta.get("ocr_used"),
            "ocr_engine": meta.get("ocr_engine"),
            "page_count": meta.get("page_count"),
            "llm_model": (cfg.get("llm_config") or {}).get("model", ""),
            "retrieval_mode": opts.get("audit_mode"),
            "risk_detection_mode": opts.get("risk_detection_mode"),
            "retrieval_used": retrieved.get("used"),
            "retrieval_queries": retrieved.get("queries"),
            "retrieval_chunk_total": retrieved.get("chunk_total", 0),
            "retrieval_query_success": retrieved.get("query_success", 0),
            "retrieval_query_failed": retrieved.get("query_failed", 0),
            "retrieval_coverage": 0.0 if _safe_int(retrieved.get("chunk_total", 0), 0) == 0 else round(_safe_int(retrieved.get("query_success", 0), 0) / _safe_int(retrieved.get("chunk_total", 0), 0), 4),
            "retrieval_failed_chunks": retrieved.get("failed_chunks", []),
            "retrieved_chunks": len(evidence_items),
            "evidence_count": len(evidence_items),
            "citation_ids": citation_ids,
            "retrieval_filters": {
                "region": opts.get("region"),
                "date": opts.get("date"),
                "industry": opts.get("industry"),
                "tax_focus": opts.get("tax_focus")
            },
            "tax_focus": opts.get("tax_focus"),
            "require_full_coverage": opts.get("require_full_coverage"),
            "tax_evidence_count": len([
                it for it in evidence_items
                if _safe_int(it.get("tax_relevance", 0), 0) > 0
            ]),
            "preview_clause_total": len(preview_clauses),
            **memory_meta,
        },
        "raw": {"mode": "memory"}
    }
