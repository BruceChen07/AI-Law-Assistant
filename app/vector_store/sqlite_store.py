import sqlite3
import numpy as np
from typing import List, Dict, Any, Optional
from app.vector_store.base import VectorStore, Chunk, SearchResult
from app.core.database import get_conn


class SQLiteVectorStore(VectorStore):
    def __init__(self, db_path: str, embedder=None):
        self.db_path = db_path
        self.embedder = embedder
        self.collection_name = "default"

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self, collection_name: str) -> None:
        self.collection_name = collection_name
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
            CREATE TABLE IF NOT EXISTS vector_chunks(
                id TEXT PRIMARY KEY,
                file_id TEXT NOT NULL,
                collection_name TEXT NOT NULL,
                text_content TEXT NOT NULL,
                vec BLOB,
                metadata_json TEXT
            )
            """)
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_vector_chunks_file_id ON vector_chunks(file_id)")
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_vector_chunks_collection ON vector_chunks(collection_name)")

            # FTS table for BM25 if needed
            cur.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS vector_chunks_fts
            USING fts5(text_content, id, file_id, collection_name, tokenize='unicode61')
            """)
            conn.commit()

    def insert_chunks(self, chunks: List[Chunk]) -> None:
        import json
        with self._get_conn() as conn:
            cur = conn.cursor()
            for chunk in chunks:
                vec_blob = np.array(
                    chunk.vector, dtype=np.float32).tobytes() if chunk.vector else None
                meta_json = json.dumps(chunk.metadata)
                cur.execute("""
                    INSERT OR REPLACE INTO vector_chunks (id, file_id, collection_name, text_content, vec, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (chunk.id, chunk.file_id, self.collection_name, chunk.text, vec_blob, meta_json))

                # Update FTS
                cur.execute(
                    "DELETE FROM vector_chunks_fts WHERE id = ?", (chunk.id,))
                cur.execute("""
                    INSERT INTO vector_chunks_fts (text_content, id, file_id, collection_name)
                    VALUES (?, ?, ?, ?)
                """, (chunk.text, chunk.id, chunk.file_id, self.collection_name))
            conn.commit()

    def search_vectors(self, query: str, top_k: int, **kwargs) -> List[SearchResult]:
        import json
        query_vector = kwargs.get('query_vector')
        if not query_vector and self.embedder:
            query_vector = self.embedder.compute_embedding(
                query, is_query=True)

        if query_vector is None:
            # Fallback to BM25 if no vector provided
            return self._search_bm25(query, top_k)

        qv = np.array(query_vector, dtype=np.float32)

        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, file_id, text_content, vec, metadata_json 
                FROM vector_chunks 
                WHERE collection_name = ? AND vec IS NOT NULL
            """, (self.collection_name,))

            results = []
            for row in cur.fetchall():
                v = np.frombuffer(row['vec'], dtype=np.float32)
                # Cosine similarity (assuming vectors are normalized, otherwise dot product is just dot)
                score = float(np.dot(qv, v))
                meta = json.loads(row['metadata_json']
                                  ) if row['metadata_json'] else {}
                results.append(SearchResult(
                    id=row['id'],
                    file_id=row['file_id'],
                    text=row['text_content'],
                    score=score,
                    metadata=meta
                ))

            results.sort(key=lambda x: x.score, reverse=True)
            return results[:top_k]

    def _search_bm25(self, query: str, top_k: int) -> List[SearchResult]:
        import json
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT v.id, v.file_id, v.text_content, v.metadata_json, bm25(vector_chunks_fts) as score
                FROM vector_chunks_fts
                JOIN vector_chunks v ON vector_chunks_fts.id = v.id
                WHERE vector_chunks_fts MATCH ? AND vector_chunks_fts.collection_name = ?
                ORDER BY score LIMIT ?
            """, (query, self.collection_name, top_k))

            results = []
            for row in cur.fetchall():
                meta = json.loads(row['metadata_json']
                                  ) if row['metadata_json'] else {}
                results.append(SearchResult(
                    id=row['id'],
                    file_id=row['file_id'],
                    text=row['text_content'],
                    # BM25 scores are usually negative in SQLite, normalize to positive if needed, but we keep it simple
                    score=-row['score'],
                    metadata=meta
                ))
            return results

    def delete_by_file_id(self, file_id: str) -> None:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM vector_chunks_fts WHERE file_id = ?", (file_id,))
            cur.execute(
                "DELETE FROM vector_chunks WHERE file_id = ?", (file_id,))
            conn.commit()

    def health_check(self) -> bool:
        try:
            with self._get_conn() as conn:
                conn.execute("SELECT 1").fetchone()
            return True
        except Exception:
            return False
