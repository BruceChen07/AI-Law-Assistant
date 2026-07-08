from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Protocol, Sequence

import numpy as np
from pydantic import BaseModel, Field

logger = logging.getLogger("law_assistant")


class Embedder(Protocol):
    def encode(self, texts: Sequence[str]) -> np.ndarray: ...


class IndexerConfig(BaseModel):
    memory_root: Path
    db_path: Path
    chunk_tokens: int = Field(default=400, ge=64, le=2000)
    chunk_overlap: int = Field(default=80, ge=0, le=800)


@dataclass
class Chunk:
    content: str
    start_line: int
    end_line: int
    content_hash: str


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str = "BAAI/bge-small-zh-v1.5", local_files_only: bool = False):
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(model_name, local_files_only=bool(local_files_only))

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            dim = self.model.get_sentence_embedding_dimension() or 384
            return np.zeros((0, dim), dtype=np.float32)
        emb = self.model.encode(list(texts), normalize_embeddings=True)
        return np.asarray(emb, dtype=np.float32)


class MarkdownChunker:
    def __init__(self, target_tokens: int = 400, overlap_tokens: int = 80):
        self.target_tokens = target_tokens
        self.overlap_tokens = overlap_tokens

    def _tokenize(self, text: str) -> List[str]:
        cjk = re.findall(r"[\u4e00-\u9fff]", text)
        words = re.findall(r"[A-Za-z0-9_]+", text)
        other = re.findall(r"[^\sA-Za-z0-9_\u4e00-\u9fff]", text)
        return cjk + words + other

    def _estimate_tokens(self, text: str) -> int:
        return max(1, len(self._tokenize(text)))

    def split(self, text: str) -> List[Chunk]:
        lines = text.splitlines()
        segments: List[tuple[str, int, int]] = []
        buf: List[str] = []
        start = 1
        for i, line in enumerate(lines, start=1):
            if line.strip():
                if not buf:
                    start = i
                buf.append(line)
            else:
                if buf:
                    segments.append(("\n".join(buf), start, i - 1))
                    buf = []
        if buf:
            segments.append(("\n".join(buf), start, len(lines)))

        chunks: List[Chunk] = []
        wbuf: List[tuple[str, int, int]] = []
        tcnt = 0

        def flush() -> None:
            nonlocal wbuf, tcnt
            if not wbuf:
                return
            content = "\n\n".join(s[0] for s in wbuf).strip()
            if not content:
                wbuf = []
                tcnt = 0
                return
            chunks.append(
                Chunk(
                    content=content,
                    start_line=wbuf[0][1],
                    end_line=wbuf[-1][2],
                    content_hash=hashlib.sha256(
                        content.encode("utf-8")).hexdigest(),
                )
            )
            if self.overlap_tokens <= 0:
                wbuf = []
                tcnt = 0
                return
            keep: List[tuple[str, int, int]] = []
            keep_tokens = 0
            for seg in reversed(wbuf):
                st = self._estimate_tokens(seg[0])
                if keep_tokens + st > self.overlap_tokens:
                    break
                keep.insert(0, seg)
                keep_tokens += st
            wbuf = keep
            tcnt = keep_tokens

        for seg in segments:
            st = self._estimate_tokens(seg[0])
            if st >= self.target_tokens:
                if wbuf:
                    flush()
                chunks.append(
                    Chunk(
                        content=seg[0].strip(),
                        start_line=seg[1],
                        end_line=seg[2],
                        content_hash=hashlib.sha256(
                            seg[0].encode("utf-8")).hexdigest(),
                    )
                )
                continue
            if tcnt + st > self.target_tokens and wbuf:
                flush()
            wbuf.append(seg)
            tcnt += st

        if wbuf:
            flush()
        return chunks


class MemoryIndexer:
    def __init__(self, cfg: IndexerConfig, embedder: Embedder):
        self.cfg = cfg
        self.embedder = embedder
        self.chunker = MarkdownChunker(cfg.chunk_tokens, cfg.chunk_overlap)
        self._write_lock = threading.RLock()
        self.cfg.memory_root.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.cfg.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS chunks(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path TEXT NOT NULL,
                    content TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    start_line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks(file_path)")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_chunks_unique ON chunks(file_path,start_line,end_line)")
            conn.execute(
                """CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
                USING fts5(content, file_path, content='chunks', content_rowid='id')"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS embeddings(
                    chunk_id INTEGER PRIMARY KEY,
                    dim INTEGER NOT NULL,
                    vector_blob BLOB NOT NULL,
                    vector_norm REAL NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(chunk_id) REFERENCES chunks(id) ON DELETE CASCADE
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS embedding_cache(
                    content_hash TEXT PRIMARY KEY,
                    dim INTEGER NOT NULL,
                    vector_blob BLOB NOT NULL,
                    vector_norm REAL NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )

    def _to_blob(self, vec: np.ndarray) -> bytes:
        return np.asarray(vec, dtype=np.float32).tobytes()

    def _from_blob(self, blob: bytes) -> np.ndarray:
        return np.frombuffer(blob, dtype=np.float32)

    def _dedup_chunks(self, chunks: List[Chunk]) -> List[Chunk]:
        uniq: dict[tuple[int, int], Chunk] = {}
        for c in chunks:
            key = (int(c.start_line), int(c.end_line))
            if key not in uniq:
                uniq[key] = c
                continue
            prev = uniq[key]
            if len(c.content) >= len(prev.content):
                uniq[key] = c
        return list(uniq.values())

    def remove_file(self, file_path: Path) -> None:
        abs_file = str(file_path.resolve())
        with self._write_lock:
            with self._conn() as conn:
                ids = [r["id"] for r in conn.execute(
                    "SELECT id FROM chunks WHERE file_path=?", (abs_file,))]
                if ids:
                    q = ",".join(["?"] * len(ids))
                    conn.execute(
                        f"DELETE FROM chunks_fts WHERE rowid IN ({q})", ids)
                    conn.execute(
                        f"DELETE FROM embeddings WHERE chunk_id IN ({q})", ids)
                conn.execute(
                    "DELETE FROM chunks WHERE file_path=?", (abs_file,))
        logger.info("memory_index_remove file=%s removed_chunks=%s",
                    abs_file, len(ids))

    def index_file(self, file_path: Path) -> int:
        path = file_path.resolve()
        if not path.exists() or path.suffix.lower() != ".md":
            return 0
        text = path.read_text(encoding="utf-8", errors="ignore")
        raw_chunks = self.chunker.split(text)
        chunks = self._dedup_chunks(raw_chunks)
        abs_file = str(path)
        now = datetime.utcnow().isoformat()

        with self._write_lock:
            with self._conn() as conn:
                old_ids = [r["id"] for r in conn.execute(
                    "SELECT id FROM chunks WHERE file_path=?", (abs_file,))]
                if old_ids:
                    q = ",".join(["?"] * len(old_ids))
                    conn.execute(
                        f"DELETE FROM chunks_fts WHERE rowid IN ({q})", old_ids)
                    conn.execute(
                        f"DELETE FROM embeddings WHERE chunk_id IN ({q})", old_ids)
                conn.execute(
                    "DELETE FROM chunks WHERE file_path=?", (abs_file,))

                ids: List[int] = []
                for c in chunks:
                    cur = conn.execute(
                        "INSERT INTO chunks(file_path,content,content_hash,start_line,end_line,updated_at) VALUES(?,?,?,?,?,?)",
                        (abs_file, c.content, c.content_hash,
                         c.start_line, c.end_line, now),
                    )
                    cid = int(cur.lastrowid)
                    ids.append(cid)
                    conn.execute(
                        "INSERT INTO chunks_fts(rowid,content,file_path) VALUES(?,?,?)",
                        (cid, c.content, abs_file),
                    )

                missing_hashes: List[str] = []
                hash_to_vec: dict[str, np.ndarray] = {}
                for c in chunks:
                    row = conn.execute(
                        "SELECT vector_blob FROM embedding_cache WHERE content_hash=?",
                        (c.content_hash,),
                    ).fetchone()
                    if row:
                        hash_to_vec[c.content_hash] = self._from_blob(
                            row["vector_blob"])
                    else:
                        missing_hashes.append(c.content_hash)

                if missing_hashes:
                    uniq_contents: dict[str, str] = {}
                    for c in chunks:
                        if c.content_hash in missing_hashes and c.content_hash not in uniq_contents:
                            uniq_contents[c.content_hash] = c.content
                    vectors = self.embedder.encode(
                        list(uniq_contents.values()))
                    for h, v in zip(uniq_contents.keys(), vectors):
                        norm = float(np.linalg.norm(v) + 1e-12)
                        hash_to_vec[h] = v
                        conn.execute(
                            "INSERT OR REPLACE INTO embedding_cache(content_hash,dim,vector_blob,vector_norm,updated_at) VALUES(?,?,?,?,?)",
                            (h, int(v.shape[0]), self._to_blob(v), norm, now),
                        )

                for cid, c in zip(ids, chunks):
                    v = hash_to_vec[c.content_hash]
                    norm = float(np.linalg.norm(v) + 1e-12)
                    conn.execute(
                        "INSERT OR REPLACE INTO embeddings(chunk_id,dim,vector_blob,vector_norm,updated_at) VALUES(?,?,?,?,?)",
                        (cid, int(v.shape[0]), self._to_blob(v), norm, now),
                    )
        logger.info(
            "memory_index_file_done file=%s chunks=%s raw_chunks=%s cache_hits=%s cache_miss=%s",
            abs_file,
            len(chunks),
            len(raw_chunks),
            max(0, len(chunks) - len(set(missing_hashes))),
            len(set(missing_hashes)),
        )
        return len(chunks)

    def reindex_all(self) -> int:
        total = 0
        for p in sorted(self.cfg.memory_root.rglob("*.md")):
            total += self.index_file(p)
        logger.info("memory_reindex_all_done root=%s total_chunks=%s",
                    str(self.cfg.memory_root), total)
        return total

    def fetch_embeddings(self, expected_dim: int | None = None) -> tuple[np.ndarray, list[int]]:
        with self._conn() as conn:
            if expected_dim is not None:
                rows = conn.execute(
                    "SELECT e.chunk_id,e.vector_blob FROM embeddings e JOIN chunks c ON c.id=e.chunk_id WHERE e.dim=? ORDER BY e.chunk_id",
                    (expected_dim,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT e.chunk_id,e.vector_blob FROM embeddings e JOIN chunks c ON c.id=e.chunk_id ORDER BY e.chunk_id"
                ).fetchall()

        if not rows:
            return np.zeros((0, expected_dim or 1), dtype=np.float32), []

        ids = [int(r["chunk_id"]) for r in rows]
        vecs = [self._from_blob(r["vector_blob"]) for r in rows]

        # Safety check for mixed dimensions if expected_dim was not provided
        if not expected_dim and vecs:
            first_dim = vecs[0].shape[0]
            valid_vecs = []
            valid_ids = []
            for i, v in enumerate(vecs):
                if v.shape[0] == first_dim:
                    valid_vecs.append(v)
                    valid_ids.append(ids[i])
            vecs = valid_vecs
            ids = valid_ids

        if not vecs:
            return np.zeros((0, expected_dim or 1), dtype=np.float32), []

        mat = np.vstack(vecs).astype(np.float32)
        return mat, ids

    def fetch_chunks_by_ids(self, ids: List[int]) -> List[sqlite3.Row]:
        if not ids:
            return []
        with self._conn() as conn:
            q = ",".join(["?"] * len(ids))
            rows = conn.execute(
                f"SELECT id,file_path,content,start_line,end_line,content_hash FROM chunks WHERE id IN ({q})",
                ids,
            ).fetchall()
        by = {int(r["id"]): r for r in rows}
        return [by[i] for i in ids if i in by]
