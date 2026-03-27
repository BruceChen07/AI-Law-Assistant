import asyncio
from pathlib import Path
from typing import Any, Dict

import numpy as np

from app.memory_system.indexer import IndexerConfig, MemoryIndexer
from app.memory_system.manager import MemoryLifecycleManager
from app.memory_system.search import HybridSearcher


class FakeEmbedder:
    def encode(self, texts):
        out = []
        for text in texts:
            vec = np.zeros(16, dtype=np.float32)
            for i, ch in enumerate(str(text)[:128]):
                vec[i % 16] += (ord(ch) % 31) / 31.0
            norm = np.linalg.norm(vec) + 1e-12
            out.append(vec / norm)
        return np.vstack(out) if out else np.zeros((0, 16), dtype=np.float32)


async def clause_llm(payload: Dict[str, Any]) -> Dict[str, Any]:
    text = str(payload.get("clause", {}).get("text", ""))
    if "invoice" in text.lower() or "发票" in text:
        return {
            "summary": "检测到发票风险",
            "risks": [
                {
                    "level": "high",
                    "issue": "Invoice obligation unclear",
                    "suggestion": "Add issuer and deadline",
                    "law_title": "中华人民共和国增值税暂行条例",
                    "article_no": "第十九条",
                    "evidence": "invoice mentioned",
                    "confidence": 0.91,
                }
            ],
        }
    return {"summary": "无明显风险", "risks": []}


async def flush_llm(prompt: str) -> str:
    return "flush ok"


def test_contract_audit_zh_en_bilingual(tmp_path: Path):
    memory_root = tmp_path / "memory"
    memory_root.mkdir(parents=True, exist_ok=True)
    db_path = memory_root / "memory.db"

    indexer = MemoryIndexer(IndexerConfig(
        memory_root=memory_root, db_path=db_path), FakeEmbedder())
    searcher = HybridSearcher(indexer, db_path)
    manager = MemoryLifecycleManager(memory_root, indexer, searcher)

    contract = """第一条 甲方应在10日内开具增值税专用发票。
Article 2 Supplier shall issue invoice in 15 days.
第三条 双方按月结算。"""
    legal_catalog = {"中华人民共和国增值税暂行条例": ["第十九条"]}
    report = asyncio.run(manager.audit_contract(
        contract, clause_llm, flush_llm, legal_catalog))

    assert report["risk_count"] >= 1
    assert report["legal_validation"]["ok"] is True
