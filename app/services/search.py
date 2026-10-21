import logging
import hashlib
import sqlite3
import re
import time
import numpy as np
from fastapi import HTTPException
from app.core.database import get_conn, get_rag_conn, get_rag_db_path
from app.core.logger import get_pipeline_logger
from app.core.utils import tokenize_query, best_sentence
from app.api.schemas import SearchQuery
from app.services.tax_contract_parser import detect_text_language

logger = logging.getLogger("law_assistant")


def _build_fts_match_query(raw_query: str) -> str:
    tokens = tokenize_query(raw_query)
    if tokens:
        return " OR ".join([f'"{t.replace("\"", "\"\"")}"' for t in tokens])
    cleaned = " ".join(raw_query.replace("\n", " ").replace("\r", " ").split())
    cleaned = "".join(ch if ("\u4e00" <= ch <= "\u9fff")
                      or ch.isalnum() or ch.isspace() else " " for ch in cleaned)
    simple = [x for x in cleaned.split(" ") if x]
    if simple:
        return " OR ".join([f'"{x.replace("\"", "\"\"")}"' for x in simple[:10]])
    english_mode = len(re.findall(r"[A-Za-z]", raw_query)) > len(
        re.findall(r"[\u4e00-\u9fff]", raw_query))
    return '"contract"' if english_mode else '"合同"'


def _table_exists(cur, table_name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table_name,),
    )
    return cur.fetchone() is not None



def _build_bm25_sql(q, bm_query: str, candidate_n: int):
    """Build the BM25 (FTS5) candidate query with the optional filters."""
    sql = """
    SELECT
      a.id as article_id,
      a.article_no,
      a.content,
      v.id as version_id,
      r.id as regulation_id,
      r.title,
      v.effective_date,
      v.expiry_date,
      v.region,
      v.industry,
      bm25(article_fts) as bm25_raw
    FROM article_fts
    JOIN article a ON a.id=article_fts.article_id
    JOIN regulation_version v ON v.id=article_fts.regulation_version_id
    JOIN regulation r ON r.id=v.regulation_id
    WHERE article_fts MATCH ?
    """
    params = [bm_query]
    if q.region:
        sql += " AND (v.region='' OR v.region=?)"
        params.append(q.region)
    if q.industry:
        sql += " AND (v.industry='' OR v.industry=?)"
        params.append(q.industry)
    if q.date:
        sql += " AND (v.effective_date='' OR v.effective_date<=?) AND (v.expiry_date='' OR v.expiry_date>=?)"
        params.extend([q.date, q.date])
    sql += " ORDER BY bm25_raw LIMIT ?"
    params.append(candidate_n)
    return sql, params


def _fetch_bm25_rows(cur, sql, params, bm25_query_text, bm_query, class_name, rag_logger):
    """Run the BM25 query and assign a simple rank-based score."""
    t_bm25 = time.perf_counter()
    rows = []
    try:
        cur.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        logger.info("bm25_candidates query=%s count=%s",
                    bm25_query_text[:80], len(rows))
        rag_logger.info(
            "class=%s stage=bm25_done query=%s count=%s cost_ms=%s",
            class_name, bm25_query_text[:80], len(rows),
            int((time.perf_counter() - t_bm25) * 1000))
    except sqlite3.OperationalError as e:
        logger.warning("bm25_failed query=%s match=%s err=%s",
                       bm25_query_text[:80], bm_query[:120], str(e))
        rag_logger.warning(
            "class=%s stage=bm25_failed query=%s err=%s",
            class_name, bm25_query_text[:80], str(e))
    for idx, r in enumerate(rows):
        r["bm25_score"] = 1.0 - (idx / max(1, len(rows)))
    return rows


def _build_semantic_sql(q, active_lang: str):
    """Build the embedding candidate query with the optional filters."""
    sql = """
    SELECT
      ae.article_id,
      ae.vec,
      a.article_no,
      a.content,
      v.id as version_id,
      r.id as regulation_id,
      r.title,
      v.effective_date,
      v.expiry_date,
      v.region,
      v.industry
    FROM article_embedding ae
    JOIN article a ON a.id=ae.article_id
    JOIN regulation_version v ON v.id=a.regulation_version_id
    JOIN regulation r ON r.id=v.regulation_id
    WHERE ae.lang=?
    """
    params = [active_lang]
    if q.region:
        sql += " AND (v.region='' OR v.region=?)"
        params.append(q.region)
    if q.industry:
        sql += " AND (v.industry='' OR v.industry=?)"
        params.append(q.industry)
    if q.date:
        sql += " AND (v.effective_date='' OR v.effective_date<=?) AND (v.expiry_date='' OR v.expiry_date>=?)"
        params.extend([q.date, q.date])
    return sql, params


def _fetch_semantic_rows(cur, sql, params, query_vec, candidate_n: int):
    """Run the embedding query, score by dot product, keep the best N.

    Raises sqlite3.OperationalError so the caller keeps its own warning text.
    """
    cur.execute(sql, params)
    rows = []
    for row in cur.fetchall():
        vec = np.frombuffer(row[1], dtype=np.float32)
        sim = float(np.dot(query_vec, vec))
        rows.append({
            "article_id": row[0],
            "article_no": row[2],
            "content": row[3],
            "version_id": row[4],
            "regulation_id": row[5],
            "title": row[6],
            "effective_date": row[7],
            "expiry_date": row[8],
            "region": row[9],
            "industry": row[10],
            "semantic_raw": sim,
        })
    rows.sort(key=lambda x: x["semantic_raw"], reverse=True)
    rows = rows[:candidate_n]
    for idx, r in enumerate(rows):
        r["semantic_score"] = 1.0 - (idx / max(1, len(rows)))
    return rows


def _merge_and_score(bm_rows, sem_rows, q):
    """Merge BM25 and semantic candidates, then compute the fused score."""
    merged = {r["article_id"]: r for r in bm_rows}
    for r in sem_rows:
        found = merged.get(r["article_id"])
        if found:
            found["semantic_raw"] = r["semantic_raw"]
            found["semantic_score"] = r["semantic_score"]
        else:
            merged[r["article_id"]] = r

    rows = list(merged.values())
    for r in rows:
        r.setdefault("bm25_score", 0.0)
        r.setdefault("semantic_score", 0.0)
        r.setdefault("semantic_raw", 0.0)
        if q.use_semantic:
            r["final_score"] = q.bm25_weight * r["bm25_score"] + \
                q.semantic_weight * r["semantic_score"]
        else:
            r["final_score"] = r["bm25_score"]
    rows.sort(key=lambda x: x.get("final_score", 0), reverse=True)
    return rows


def _resolve_rerank_enabled(q, cfg, semantic_query_text: str) -> bool:
    """`rerank_mode` wins over `rerank_enabled`; `ab` hashes the query."""
    rerank_enabled = q.rerank_enabled if q.rerank_enabled is not None else cfg.get(
        "reranker_enabled", True)
    if q.rerank_mode == "off":
        rerank_enabled = False
    elif q.rerank_mode == "on":
        rerank_enabled = True
    elif q.rerank_mode == "ab":
        h = hashlib.md5(semantic_query_text.encode("utf-8")).hexdigest()
        rerank_enabled = int(h, 16) % 2 == 0
    return rerank_enabled


def _apply_rerank(rows, q, cfg, reranker, semantic_query_text, class_name,
                  rag_logger, candidate_size: int, candidate_n: int, lang: str):
    """Optionally rerank; falls back to truncation when disabled or failing."""
    rerank_enabled = _resolve_rerank_enabled(q, cfg, semantic_query_text)
    rerank_top_n = q.rerank_top_n or candidate_size
    rerank_top_n = max(1, min(rerank_top_n, candidate_n))
    rerank_fallback = cfg.get("rerank_fallback", True)
    rerank_threshold = cfg.get("rerank_score_threshold", 0.0) or 0.0

    if not (rerank_enabled and reranker):
        return rows[:q.top_k]

    candidates = rows[:max(1, min(rerank_top_n, len(rows)))]
    t_rerank = time.perf_counter()
    try:
        reranked = reranker.rerank(semantic_query_text, candidates,
                                   top_k=q.top_k, lang=lang)
        rag_logger.info(
            "class=%s stage=rerank_done query=%s in_count=%s out_count=%s cost_ms=%s",
            class_name, semantic_query_text[:80], len(candidates), len(reranked),
            int((time.perf_counter() - t_rerank) * 1000))
        if rerank_threshold > 0:
            reranked = [r for r in reranked if r.get(
                "rerank_score", 0.0) >= rerank_threshold]
        return reranked
    except Exception:
        rag_logger.exception(
            "class=%s stage=rerank_failed query=%s fallback=%s",
            class_name, semantic_query_text[:80], bool(rerank_fallback))
        if not rerank_fallback:
            raise
        return rows[:q.top_k]


def _decorate_rows(rows, q, tokens, rag_lang: str):
    """Attach effective status, answer snippet, matched tokens and citation id."""
    for r in rows:
        r["effective_status"] = "active"
        if q.date and r.get("effective_date") and r["effective_date"] > q.date:
            r["effective_status"] = "not_effective"
        if q.date and r.get("expiry_date") and r["expiry_date"] < q.date:
            r["effective_status"] = "expired"
        ans, score = best_sentence(r["content"], tokens) if tokens else ("", 0)
        r["answer"] = ans
        r["answer_score"] = score
        content_low = str(r["content"]).lower()
        r["match_tokens"] = [t for t in tokens if (
            t in r["content"] or t.lower() in content_low)]
        r["citation_lang"] = rag_lang
        r["citation_id"] = f"{rag_lang}:{r['regulation_id']}:{r['version_id']}:{r['article_id']}"
    return rows


def search_regulations(cfg, q: SearchQuery, embedder, reranker=None, target_rag_lang: str = ""):
    class_name = "RegulationSearchService"
    rag_logger = get_pipeline_logger(
        cfg, name="rag_pipeline", filename="rag_pipeline.log")
    t0 = time.perf_counter()
    if not q.query.strip():
        rag_logger.info(
            "class=%s stage=search_skip_empty_query",
            class_name)
        return []
    default_lang = embedder.get_registry_status()["default_language"]

    # Auto-detect query language to prevent mismatch when UI language differs
    detected_lang = detect_text_language(q.query, default=default_lang)
    lang = (q.language or default_lang).lower()

    if q.query.strip() and detected_lang != lang:
        logger.info("search_language_override query=%s original_lang=%s detected_lang=%s",
                    q.query[:20], lang, detected_lang)
        rag_logger.info(
            "class=%s stage=search_language_override query=%s original_lang=%s detected_lang=%s",
            class_name, q.query[:20], lang, detected_lang)
        lang = detected_lang

    prof = embedder.get_embed_profile(lang)
    active_lang = (prof or {}).get("lang", lang)
    if q.use_semantic and not prof:
        rag_logger.warning(
            "class=%s stage=search_semantic_model_unavailable query=%s lang=%s",
            class_name, q.query[:80], lang)
        raise HTTPException(
            status_code=503, detail=f"semantic search enabled but embedding model is not ready for language={lang}")

    rag_lang = str(target_rag_lang or lang).strip().lower()
    rag_db_path = get_rag_db_path(cfg, rag_lang)
    rag_logger.info(
        "class=%s stage=search_start query=%s lang=%s rag_lang=%s rag_db=%s top_k=%s semantic=%s",
        class_name, q.query[:80], lang, rag_lang, rag_db_path, q.top_k, q.use_semantic)
    logger.info("search_start query=%s lang=%s active_lang=%s rag_lang=%s rag_db=%s top_k=%s semantic=%s model_id=%s",
                q.query[:80], lang, active_lang, rag_lang, rag_db_path, q.top_k, q.use_semantic, (prof or {}).get("model_id", "none"))
    bm25_query_text = str(q.bm25_query or q.query or "").strip()
    semantic_query_text = str(q.semantic_query or q.query or "").strip()
    tokens = tokenize_query(semantic_query_text)
    bm_query = _build_fts_match_query(bm25_query_text)
    conn = get_rag_conn(cfg, rag_lang) if target_rag_lang else get_conn(cfg)
    cur = conn.cursor()
    required_tables = ("article", "article_fts", "regulation", "regulation_version")
    if any(not _table_exists(cur, t) for t in required_tables):
        logger.info("search_skip_missing_tables rag_lang=%s rag_db=%s", rag_lang, rag_db_path)
        rag_logger.warning(
            "class=%s stage=search_skip_missing_tables rag_lang=%s rag_db=%s",
            class_name, rag_lang, rag_db_path)
        conn.close()
        return []

    candidate_min = 10
    candidate_max = 200
    candidate_size = q.candidate_size or candidate_min
    candidate_size = max(candidate_min, min(candidate_size, candidate_max))
    candidate_n = max(q.top_k, candidate_size)

    bm_sql, bm_params = _build_bm25_sql(q, bm_query, candidate_n)
    bm_rows = _fetch_bm25_rows(
        cur, bm_sql, bm_params, bm25_query_text, bm_query, class_name, rag_logger)

    sem_rows = []
    if q.use_semantic:
        t_sem = time.perf_counter()
        qe = embedder.compute_embedding(
            semantic_query_text, is_query=True, lang=active_lang)
        if qe is not None:
            sem_sql, sem_params = _build_semantic_sql(q, active_lang)
            try:
                sem_rows = _fetch_semantic_rows(
                    cur, sem_sql, sem_params, qe, candidate_n)
                logger.info("semantic_candidates query=%s count=%s",
                            semantic_query_text[:80], len(sem_rows))
                rag_logger.info(
                    "class=%s stage=semantic_done query=%s count=%s cost_ms=%s",
                    class_name, semantic_query_text[:80], len(sem_rows),
                    int((time.perf_counter() - t_sem) * 1000))
            except sqlite3.OperationalError as e:
                logger.warning("semantic_failed query=%s rag_lang=%s err=%s",
                               semantic_query_text[:80], rag_lang, str(e))
                rag_logger.warning(
                    "class=%s stage=semantic_failed query=%s rag_lang=%s err=%s",
                    class_name, semantic_query_text[:80], rag_lang, str(e))
        else:
            logger.warning(
                "semantic_enabled_but_embedder_unavailable query=%s lang=%s", semantic_query_text[:80], lang)
            rag_logger.warning(
                "class=%s stage=semantic_skip_no_embedding query=%s lang=%s",
                class_name, semantic_query_text[:80], lang)

    rows = _merge_and_score(bm_rows, sem_rows, q)
    rows = _apply_rerank(rows, q, cfg, reranker, semantic_query_text, class_name,
                         rag_logger, candidate_size, candidate_n, lang)

    conn.close()
    rows = _decorate_rows(rows, q, tokens, rag_lang)
    logger.info("search_done query=%s results=%s", q.query[:80], len(rows))
    rag_logger.info(
        "class=%s stage=search_done query=%s results=%s total_ms=%s",
        class_name, q.query[:80], len(rows), int((time.perf_counter() - t0) * 1000))
    return rows
