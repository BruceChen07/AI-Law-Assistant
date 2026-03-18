from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path
from typing import Dict, List

import numpy as np
from pydantic import BaseModel, Field

from app.memory_system.indexer import MemoryIndexer

logger = logging.getLogger("law_assistant")


class SearchHit(BaseModel):
    chunk_id: int
    file_path: str
    content: str
    start_line: int
    end_line: int
    vector_score: float = 0.0
    keyword_score: float = 0.0
    fused_score: float = 0.0


class HybridSearchConfig(BaseModel):
    vector_weight: float = Field(default=0.7, ge=0.0, le=1.0)
    keyword_weight: float = Field(default=0.3, ge=0.0, le=1.0)


class HybridSearcher:
    def __init__(self, indexer: MemoryIndexer, db_path: Path, cfg: HybridSearchConfig | None = None):
        self.indexer = indexer
        self.db_path = db_path
        self.cfg = cfg or HybridSearchConfig()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _normalize(self, scores: Dict[int, float]) -> Dict[int, float]:
        if not scores:
            return {}
        vals = list(scores.values())
        lo, hi = min(vals), max(vals)
        if hi - lo < 1e-12:
            return {k: 1.0 for k in scores}
        return {k: (v - lo) / (hi - lo) for k, v in scores.items()}

    def _fts_query(self, q: str) -> str:
        tokens = re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9_]+", q.strip())
        if not tokens:
            return ""
        return " OR ".join(f'"{t}"' for t in tokens[:24])

    def search(self, query: str, top_k: int = 8) -> List[SearchHit]:
        query = str(query or "").strip()
        if not query:
            return []
        qv = self.indexer.embedder.encode([query])
        if qv.size == 0:
            return []
        qvec = qv[0]
        qnorm = float(np.linalg.norm(qvec) + 1e-12)

        mat, ids = self.indexer.fetch_embeddings(expected_dim=qvec.shape[0])
        vec_scores: Dict[int, float] = {}
        if mat.shape[0] > 0:
            if mat.shape[1] == qvec.shape[0]:
                mnorm = np.linalg.norm(mat, axis=1) + 1e-12
                sims = (mat @ qvec) / (mnorm * qnorm)
                for cid, s in zip(ids, sims):
                    vec_scores[int(cid)] = float(s)

        kw_scores_raw: Dict[int, float] = {}
        fts_q = self._fts_query(query)
        if fts_q:
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT rowid AS chunk_id, bm25(chunks_fts) AS bm FROM chunks_fts WHERE chunks_fts MATCH ? LIMIT ?",
                    (fts_q, max(20, top_k * 5)),
                ).fetchall()
            for r in rows:
                cid = int(r["chunk_id"])
                kw_scores_raw[cid] = -float(r["bm"])

        vec_n = self._normalize(vec_scores)
        kw_n = self._normalize(kw_scores_raw)
        all_ids = set(vec_n.keys()) | set(kw_n.keys())
        fused: Dict[int, float] = {}
        for cid in all_ids:
            fused[cid] = self.cfg.vector_weight * vec_n.get(cid, 0.0) + self.cfg.keyword_weight * kw_n.get(cid, 0.0)

        ranked = sorted(fused.items(), key=lambda x: x[1], reverse=True)[:top_k]
        ranked_ids = [cid for cid, _ in ranked]
        rows = self.indexer.fetch_chunks_by_ids(ranked_ids)
        by = {int(r["id"]): r for r in rows}

        out: List[SearchHit] = []
        for cid, fs in ranked:
            r = by.get(cid)
            if not r:
                continue
            out.append(
                SearchHit(
                    chunk_id=cid,
                    file_path=str(r["file_path"]),
                    content=str(r["content"]),
                    start_line=int(r["start_line"]),
                    end_line=int(r["end_line"]),
                    vector_score=vec_n.get(cid, 0.0),
                    keyword_score=kw_n.get(cid, 0.0),
                    fused_score=fs,
                )
            )
        logger.info(
            "memory_search_done query_len=%s top_k=%s vec_candidates=%s kw_candidates=%s hits=%s",
            len(query),
            top_k,
            len(vec_scores),
            len(kw_scores_raw),
            len(out),
        )
        return out
