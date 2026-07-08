import os
import pytest
from app.vector_store.base import Chunk
from app.vector_store.sqlite_store import SQLiteVectorStore
from app.vector_store.chroma_store import ChromaDBVectorStore, CHROMA_AVAILABLE


@pytest.fixture
def sqlite_store(tmp_path):
    db_path = tmp_path / "test_vector.db"
    store = SQLiteVectorStore(db_path=str(db_path))
    store.initialize("test_collection")
    return store


def test_sqlite_vector_store(sqlite_store):
    chunks = [
        Chunk(id="c1", file_id="f1", text="tax regulation 101",
              vector=[0.1, 0.2, 0.3], metadata={"year": 2023}),
        Chunk(id="c2", file_id="f1", text="contract clause 5",
              vector=[0.9, 0.8, 0.7], metadata={"type": "clause"}),
        Chunk(id="c3", file_id="f2", text="irrelevant text",
              vector=[0.0, 0.0, 0.1], metadata={}),
    ]

    sqlite_store.insert_chunks(chunks)

    # Test vector search
    # [1.0, 0.0, 0.0] will match c2 [0.9, 0.8, 0.7] with score 0.9, and c1 [0.1, 0.2, 0.3] with score 0.1
    results = sqlite_store.search_vectors(
        query="tax", top_k=2, query_vector=[1.0, 0.0, 0.0])
    assert len(results) == 2
    assert results[0].id == "c2"

    # Test BM25 fallback
    bm25_results = sqlite_store.search_vectors(query="clause", top_k=1)
    assert len(bm25_results) == 1
    assert bm25_results[0].id == "c2"

    # Test delete
    sqlite_store.delete_by_file_id("f1")
    results_after_delete = sqlite_store.search_vectors(
        query="clause", top_k=10)
    assert len(results_after_delete) == 0


@pytest.mark.skipif(not CHROMA_AVAILABLE, reason="ChromaDB not installed")
def test_chroma_vector_store(tmp_path):
    chroma_path = tmp_path / "chroma_db"
    store = ChromaDBVectorStore(persist_directory=str(chroma_path))
    store.initialize("test_collection")

    chunks = [
        Chunk(id="c1", file_id="f1", text="tax regulation 101",
              vector=[0.1, 0.2, 0.3], metadata={"year": 2023}),
        Chunk(id="c2", file_id="f1", text="contract clause 5",
              vector=[0.9, 0.8, 0.7], metadata={"type": "clause"}),
    ]

    store.insert_chunks(chunks)

    # Test search
    results = store.search_vectors(
        query="tax", top_k=1, query_vector=[0.1, 0.2, 0.3])
    assert len(results) == 1
    assert results[0].id == "c1"

    # Test delete
    store.delete_by_file_id("f1")
    results_after_delete = store.search_vectors(
        query="tax", top_k=10, query_vector=[0.1, 0.2, 0.3])
    assert len(results_after_delete) == 0


def test_health_check(sqlite_store):
    assert sqlite_store.health_check() is True
