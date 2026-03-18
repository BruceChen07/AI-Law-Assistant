from pathlib import Path

import numpy as np

from app.memory_system.indexer import Chunk, IndexerConfig, MemoryIndexer
from app.memory_system.search import HybridSearcher


class FakeEmbedder:
    def encode(self, texts):
        out = []
        for text in texts:
            vec = np.zeros(8, dtype=np.float32)
            for i, ch in enumerate(str(text)[:64]):
                vec[i % 8] += (ord(ch) % 17) / 17.0
            norm = np.linalg.norm(vec) + 1e-12
            out.append(vec / norm)
        return np.vstack(out) if out else np.zeros((0, 8), dtype=np.float32)


def test_index_and_search(tmp_path: Path):
    memory_root = tmp_path / "memory"
    memory_root.mkdir(parents=True, exist_ok=True)
    db_path = memory_root / "memory.db"
    md = memory_root / "2026-03-18.md"
    md.write_text("第一条 发票开具时限。\n\nArticle 2 payment terms.", encoding="utf-8")

    indexer = MemoryIndexer(IndexerConfig(memory_root=memory_root, db_path=db_path), FakeEmbedder())
    count = indexer.index_file(md)
    assert count >= 1

    searcher = HybridSearcher(indexer, db_path)
    hits = searcher.search("invoice 发票", top_k=3)
    assert len(hits) >= 1

    count2 = indexer.index_file(md)
    assert count2 >= 1


def test_index_file_dedup_duplicate_ranges(tmp_path: Path):
    memory_root = tmp_path / "memory"
    memory_root.mkdir(parents=True, exist_ok=True)
    db_path = memory_root / "memory.db"
    md = memory_root / "2026-03-18.md"
    md.write_text("A\n\nB", encoding="utf-8")

    indexer = MemoryIndexer(IndexerConfig(memory_root=memory_root, db_path=db_path), FakeEmbedder())
    duplicate = Chunk(content="dup", start_line=1, end_line=1, content_hash="h1")
    indexer.chunker.split = lambda _text: [duplicate, duplicate]

    count = indexer.index_file(md)
    assert count == 1
