import logging
import numpy as np
from fastapi import HTTPException
from app.core.database import get_conn
from app.core.utils import tokenize_query, best_sentence
from app.api.schemas import SearchQuery

logger = logging.getLogger("law_assistant")


def search_regulations(cfg, q: SearchQuery, embedder):
    if not q.query.strip():
        return []
    default_lang = embedder.get_registry_status()["default_language"]
    lang = (q.language or default_lang).lower()
    prof = embedder.get_embed_profile(lang)
    active_lang = (prof or {}).get("lang", lang)
    if q.use_semantic and not prof:
        # Raise HTTPException or return error? Service usually raises specific exceptions or returns error.
        # But here let's propagate the logic from main.py
        # main.py raised HTTPException(503)
        # I'll raise RuntimeError and handle in router, or import HTTPException
        # Better to keep service framework-agnostic if possible, but for now let's raise a custom error or just HTTPException
        raise HTTPException(status_code=503, detail=f"semantic search enabled but embedding model is not ready for language={lang}")

    logger.info("search_start query=%s lang=%s active_lang=%s top_k=%s semantic=%s model_id=%s",
                q.query[:80], lang, active_lang, q.top_k, q.use_semantic, (prof or {}).get("model_id", "none"))
    tokens = tokenize_query(q.query)
    conn = get_conn(cfg)
    cur = conn.cursor()
    candidate_n = max(q.top_k, q.candidate_size)

    bm_sql = """
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
    bm_params = [q.query]
    if q.region:
        bm_sql += " AND (v.region='' OR v.region=?)"
        bm_params.append(q.region)
    if q.industry:
        bm_sql += " AND (v.industry='' OR v.industry=?)"
        bm_params.append(q.industry)
    if q.date:
        bm_sql += " AND (v.effective_date='' OR v.effective_date<=?) AND (v.expiry_date='' OR v.expiry_date>=?)"
        bm_params.extend([q.date, q.date])
    bm_sql += " ORDER BY bm25_raw LIMIT ?"
    bm_params.append(candidate_n)

    cur.execute(bm_sql, bm_params)
    bm_rows = [dict(r) for r in cur.fetchall()]
    logger.info("bm25_candidates query=%s count=%s",
                q.query[:80], len(bm_rows))
    for idx, r in enumerate(bm_rows):
        r["bm25_score"] = 1.0 - (idx / max(1, len(bm_rows)))

    merged = {r["article_id"]: r for r in bm_rows}

    if q.use_semantic:
        qe = embedder.compute_embedding(q.query, is_query=True, lang=active_lang)
        if qe is not None:
            sem_sql = """
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
            sem_params = [active_lang]
            if q.region:
                sem_sql += " AND (v.region='' OR v.region=?)"
                sem_params.append(q.region)
            if q.industry:
                sem_sql += " AND (v.industry='' OR v.industry=?)"
                sem_params.append(q.industry)
            if q.date:
                sem_sql += " AND (v.effective_date='' OR v.effective_date<=?) AND (v.expiry_date='' OR v.expiry_date>=?)"
                sem_params.extend([q.date, q.date])
            cur.execute(sem_sql, sem_params)
            sem_rows = []
            for row in cur.fetchall():
                v = np.frombuffer(row[1], dtype=np.float32)
                sim = float(np.dot(qe, v))
                sem_rows.append({
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
                    "semantic_raw": sim
                })
            sem_rows.sort(key=lambda x: x["semantic_raw"], reverse=True)
            sem_rows = sem_rows[:candidate_n]
            logger.info("semantic_candidates query=%s count=%s",
                        q.query[:80], len(sem_rows))
            for idx, r in enumerate(sem_rows):
                r["semantic_score"] = 1.0 - (idx / max(1, len(sem_rows)))
                found = merged.get(r["article_id"])
                if found:
                    found["semantic_raw"] = r["semantic_raw"]
                    found["semantic_score"] = r["semantic_score"]
                else:
                    merged[r["article_id"]] = r
        else:
            logger.warning(
                "semantic_enabled_but_embedder_unavailable query=%s lang=%s", q.query[:80], lang)

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
    rows = rows[:q.top_k]

    conn.close()
    for r in rows:
        r["effective_status"] = "active"
        if q.date and r.get("effective_date") and r["effective_date"] > q.date:
            r["effective_status"] = "not_effective"
        if q.date and r.get("expiry_date") and r["expiry_date"] < q.date:
            r["effective_status"] = "expired"
        ans, score = best_sentence(r["content"], tokens) if tokens else ("", 0)
        r["answer"] = ans
        r["answer_score"] = score
        r["match_tokens"] = [t for t in tokens if t in r["content"]]
        r["citation_id"] = f"{r['regulation_id']}:{r['version_id']}:{r['article_id']}"
    logger.info("search_done query=%s results=%s", q.query[:80], len(rows))
    return rows
