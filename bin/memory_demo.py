from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict

from app.memory_system.indexer import IndexerConfig, MemoryIndexer, SentenceTransformerEmbedder
from app.memory_system.manager import MemoryLifecycleManager, MemoryManagerConfig
from app.memory_system.search import HybridSearchConfig, HybridSearcher
from app.memory_system.watcher import FileWatcher


async def fake_clause_llm(payload: Dict[str, Any]) -> Dict[str, Any]:
    clause = str(payload.get("clause", {}).get("text", ""))
    risks = []
    if "发票" in clause or "invoice" in clause.lower():
        risks.append(
            {
                "level": "high",
                "issue": "开票义务和时限不明确",
                "suggestion": "补充开票主体、税率、时限与违约责任",
                "law_title": "中华人民共和国增值税暂行条例",
                "article_no": "第十九条",
                "evidence": "条款涉及发票但缺少明确约束",
                "confidence": 0.92,
            }
        )
    return {"summary": "条款分析完成", "risks": risks}


async def fake_flush_llm(prompt: str) -> str:
    return f"- Flush摘要\n- 输入长度: {len(prompt)}"


async def run_demo() -> None:
    memory_root = Path(r"d:\Workspace\AI-Law-Assistant\memory")
    db_path = memory_root / "memory.db"
    embedder = SentenceTransformerEmbedder("all-MiniLM-L6-v2")
    indexer = MemoryIndexer(IndexerConfig(memory_root=memory_root, db_path=db_path), embedder)
    searcher = HybridSearcher(indexer=indexer, db_path=db_path, cfg=HybridSearchConfig(vector_weight=0.7, keyword_weight=0.3))
    manager = MemoryLifecycleManager(memory_root, indexer, searcher, MemoryManagerConfig())
    watcher = FileWatcher(memory_root, indexer)

    watcher.start()
    try:
        indexer.reindex_all()
        contract_text = """第一条 甲方应在收款后10日内开具增值税专用发票。
第二条 双方按月结算，逾期付款按日万分之五承担违约金。
Article 3 The supplier shall issue compliant invoices within 15 days."""
        legal_catalog = {"中华人民共和国增值税暂行条例": ["第十九条", "第二十一条"]}
        report = await manager.audit_contract(contract_text, fake_clause_llm, fake_flush_llm, legal_catalog)
        print(report)
        hits = searcher.search("发票 时限", top_k=5)
        print([h.model_dump() for h in hits])
    finally:
        watcher.stop()


if __name__ == "__main__":
    asyncio.run(run_demo())
