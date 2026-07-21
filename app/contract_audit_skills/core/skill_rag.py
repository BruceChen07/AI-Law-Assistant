"""
分技能隔离RAG调度引擎 (Skill-isolated RAG Scheduler)
====================================================
功能：
1. 管理多个独立ChromaDB向量库，每个Skill一个专属库
2. 执行某Skill时仅加载对应专属向量库，不跨库检索
3. 检索结果注入当前Skill Prompt上下文
4. 每条风险结果溯源检索文档名称+段落
5. 支持RAG知识库导入脚本

RAG隔离策略：
- law_compliance/   → Skill 1: 法律合规审查专用
- business_clause/  → Skill 2: 商务权责审计专用
- tax_finance/      → Skill 3: 财税票据审计专用
- dispute_break/    → Skill 4: 违约&争议解决专用
- risk_history/     → Skill 5: 风险汇总分级专用
"""

from __future__ import annotations

import os
import json
import hashlib
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger("law_assistant.skill_rag")

# ============================================================================
# ChromaDB检测（依赖为可选，离线环境可能未安装但需优雅降级）
# ============================================================================
CHROMA_AVAILABLE = False
try:
    import chromadb
    from chromadb.config import Settings
    CHROMA_AVAILABLE = True
except ImportError:
    pass


# ============================================================================
# 数据模型
# ============================================================================

@dataclass
class RAGResult:
    """RAG单条检索结果"""
    document_name: str       # 文档名称
    paragraph: str           # 段落标识
    content: str             # 检索到的文本内容
    source: str = ""         # 来源（法规/判例/模板/政策等）
    doc_number: str = ""     # 文号（适用于法规/政策）
    doc_type: str = ""       # 文档类型
    relevance_score: float = 0.0  # 相关性评分
    chunk_id: str = ""       # 分块ID
    metadata: Dict[str, Any] = field(default_factory=dict)  # 原始元数据


@dataclass
class RAGQueryResult:
    """RAG查询结果"""
    skill_id: str
    skill_name: str
    query_text: str
    results: List[RAGResult] = field(default_factory=list)
    total_found: int = 0
    retrieval_time_ms: float = 0.0
    error: str = ""


# ============================================================================
# Skill-RAG存储映射
# ============================================================================

# Skill ID → RAG库路径 映射
SKILL_RAG_MAP: Dict[str, Dict[str, str]] = {
    "skill_law_compliance": {
        "rag_path": "law_compliance",
        "collection_name": "law_compliance",
        "display_name": "法律合规法规库",
        "doc_type": "法规/司法解释/判例",
    },
    "skill_business_clause": {
        "rag_path": "business_clause",
        "collection_name": "business_clause",
        "display_name": "商务合同模板库",
        "doc_type": "合同模板/纠纷台账",
    },
    "skill_tax_finance": {
        "rag_path": "tax_finance",
        "collection_name": "tax_finance",
        "display_name": "财税政策库",
        "doc_type": "财税政策/发票管理办法/处罚案例",
    },
    "skill_dispute_breach": {
        "rag_path": "dispute_break",
        "collection_name": "dispute_breach",
        "display_name": "争议判例库",
        "doc_type": "司法判例/管辖案例",
    },
    "skill_risk_summary": {
        "rag_path": "risk_history",
        "collection_name": "risk_history",
        "display_name": "历史风险库",
        "doc_type": "重大合同风险记录",
    },
}


class SkillRAGManager:
    """
    分技能隔离RAG管理器。

    核心职责：
    1. 为每个Skill维护独立的ChromDB向量库实例
    2. 检索时严格隔离，不跨库检索
    3. 支持知识导入（从JSON/文本批量导入法条、案例、模板）
    4. 支持embedding函数复用现有项目的embedding模型
    """

    def __init__(self, base_path: str, embedder=None, top_k: int = 8,
                 similarity_threshold: float = 0.55):
        """
        Args:
            base_path: RAG库根目录路径
            embedder: 可选的embedding函数（复用项目现有embedding模型）
            top_k: 每次检索返回的最大条数
            similarity_threshold: 相似度阈值
        """
        self.base_path = os.path.abspath(base_path)
        self.embedder = embedder
        self.top_k = top_k
        self.similarity_threshold = similarity_threshold
        # 缓存已初始化的ChromaDB客户端: skill_id -> ChromaDBVectorStore
        self._stores: Dict[str, Any] = {}
        logger.info("SkillRAGManager 初始化, base_path=%s, chroma_available=%s",
                    self.base_path, CHROMA_AVAILABLE)

    def _get_skill_rag_path(self, skill_id: str) -> Tuple[str, str]:
        """获取Skill对应的RAG库路径和collection名称"""
        skill_cfg = SKILL_RAG_MAP.get(skill_id)
        if not skill_cfg:
            raise ValueError(f"未找到Skill对应的RAG配置: {skill_id}")
        rag_dir = os.path.join(self.base_path, skill_cfg["rag_path"])
        return rag_dir, skill_cfg["collection_name"]

    def _get_store(self, skill_id: str):
        """获取或初始化指定Skill的ChromaDB存储实例"""
        if skill_id in self._stores:
            return self._stores[skill_id]

        rag_dir, collection_name = self._get_skill_rag_path(skill_id)
        os.makedirs(rag_dir, exist_ok=True)

        if not CHROMA_AVAILABLE:
            logger.warning("ChromaDB未安装，RAG不可用 skill=%s", skill_id)
            return None

        try:
            from app.vector_store.chroma_store import ChromaDBVectorStore
            store = ChromaDBVectorStore(
                persist_directory=rag_dir,
                embedder=self.embedder,
            )
            store.initialize(collection_name)
            self._stores[skill_id] = store
            logger.info("RAG store已初始化 skill=%s collection=%s path=%s",
                        skill_id, collection_name, rag_dir)
            return store
        except Exception as e:
            logger.error("RAG store初始化失败 skill=%s: %s", skill_id, str(e))
            return None

    def search(self, skill_id: str, query_text: str, top_k: Optional[int] = None) -> RAGQueryResult:
        """
        在指定Skill的专属RAG库中检索。

        Args:
            skill_id: Skill标识（如 skill_law_compliance）
            query_text: 查询文本
            top_k: 返回数量（覆盖默认值）

        Returns:
            RAGQueryResult: 检索结果
        """
        skill_cfg = SKILL_RAG_MAP.get(skill_id, {})
        result = RAGQueryResult(
            skill_id=skill_id,
            skill_name=skill_cfg.get("display_name", skill_id),
            query_text=query_text,
        )

        store = self._get_store(skill_id)
        if store is None:
            result.error = "ChromaDB不可用或初始化失败"
            return result

        k = top_k or self.top_k
        start_time = datetime.now()

        try:
            # 使用向量检索
            search_kwargs = {}
            if self.embedder and hasattr(self.embedder, 'compute_embedding'):
                try:
                    query_vector = self.embedder.compute_embedding(query_text, is_query=True)
                    if hasattr(query_vector, 'tolist'):
                        query_vector = query_vector.tolist()
                    search_kwargs['query_vector'] = query_vector
                except Exception as e:
                    logger.debug("embedding计算失败，回退到文本检索: %s", str(e))

            raw_results = store.search_vectors(query_text, k, **search_kwargs)

            # 转换为RAGResult
            for sr in raw_results:
                if sr.score < self.similarity_threshold:
                    continue
                result.results.append(RAGResult(
                    document_name=sr.metadata.get("document_name", sr.file_id),
                    paragraph=sr.metadata.get("paragraph", ""),
                    content=sr.text,
                    source=sr.metadata.get("source", skill_cfg.get("doc_type", "")),
                    doc_number=sr.metadata.get("doc_number", ""),
                    doc_type=sr.metadata.get("doc_type", skill_cfg.get("doc_type", "")),
                    relevance_score=round(sr.score, 4),
                    chunk_id=sr.id,
                    metadata=sr.metadata,
                ))

            result.total_found = len(raw_results)
            result.retrieval_time_ms = (datetime.now() - start_time).total_seconds() * 1000

            logger.info("RAG检索完成 skill=%s query_len=%d found=%d retained=%d time=%.0fms",
                        skill_id, len(query_text), len(raw_results),
                        len(result.results), result.retrieval_time_ms)

        except Exception as e:
            result.error = str(e)
            logger.error("RAG检索异常 skill=%s: %s", skill_id, str(e))

        return result

    def import_documents(self, skill_id: str, documents: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        向指定Skill的RAG库批量导入文档。

        Args:
            skill_id: Skill标识
            documents: 文档列表，每个文档包含:
                {
                    "text": "文本内容",
                    "metadata": {
                        "document_name": "文件名",
                        "source": "来源",
                        "paragraph": "段落",
                        "doc_number": "文号",
                        "doc_type": "类型",
                        ...
                    },
                    "chunk_size": 800,     # 可选，分块大小
                    "chunk_overlap": 150,  # 可选，分块重叠
                }

        Returns:
            {"imported_chunks": int, "total_docs": int, "errors": []}
        """
        store = self._get_store(skill_id)
        if store is None:
            return {"imported_chunks": 0, "total_docs": len(documents),
                    "errors": ["ChromaDB不可用"]}

        total_chunks = 0
        errors = []

        for idx, doc in enumerate(documents):
            try:
                text = doc.get("text", "")
                metadata = doc.get("metadata", {})
                chunk_size = doc.get("chunk_size", 800)
                chunk_overlap = doc.get("chunk_overlap", 150)

                # 文本分块
                chunks, chunk_metas = self._chunk_text(text, metadata, chunk_size, chunk_overlap)

                # 批量导入
                from app.vector_store.base import Chunk
                chunk_objects = []
                for i, (chunk_text, chunk_meta) in enumerate(zip(chunks, chunk_metas)):
                    chunk_id = hashlib.md5(f"{metadata.get('document_name', '')}_{i}".encode()).hexdigest()[:16]
                    chunk_objects.append(Chunk(
                        id=chunk_id,
                        file_id=metadata.get("document_name", f"doc_{idx}"),
                        text=chunk_text,
                        metadata=chunk_meta,
                    ))

                store.insert_chunks(chunk_objects)
                total_chunks += len(chunk_objects)

            except Exception as e:
                errors.append(f"文档 {idx} 导入失败: {str(e)}")
                logger.error("RAG导入失败 doc=%d skill=%s: %s", idx, skill_id, str(e))

        return {
            "imported_chunks": total_chunks,
            "total_docs": len(documents),
            "errors": errors,
        }

    def _chunk_text(self, text: str, base_metadata: Dict[str, Any],
                     chunk_size: int, chunk_overlap: int) -> Tuple[List[str], List[Dict[str, Any]]]:
        """
        将文本切分为重叠块。

        Args:
            text: 原始文本
            base_metadata: 基础元数据（写入每个块的metadata）
            chunk_size: 每块最大字符数
            chunk_overlap: 块间重叠字符数

        Returns:
            (chunks, metadatas)
        """
        if not text or len(text) <= chunk_size:
            return [text], [base_metadata]

        chunks = []
        metadatas = []
        start = 0

        while start < len(text):
            end = min(start + chunk_size, len(text))
            chunk = text[start:end]

            # 尝试在句号或换行处结束
            if end < len(text):
                last_period = max(chunk.rfind("。"), chunk.rfind("\n"), chunk.rfind("；"))
                if last_period > chunk_size // 2:
                    end = start + last_period + 1
                    chunk = text[start:end]

            chunks.append(chunk)

            # 为每个块创建元数据副本
            chunk_meta = dict(base_metadata)
            chunk_meta["chunk_index"] = len(chunks)
            chunk_meta["total_chunks"] = 0  # 稍后更新
            metadatas.append(chunk_meta)

            start = end - chunk_overlap

        # 更新total_chunks
        for meta in metadatas:
            meta["total_chunks"] = len(chunks)

        return chunks, metadatas

    def get_collection_stats(self, skill_id: str = "") -> Dict[str, Any]:
        """
        获取RAG库统计信息。

        Args:
            skill_id: 指定的Skill ID，为空则返回全部

        Returns:
            统计信息字典
        """
        skills_to_check = [skill_id] if skill_id else list(SKILL_RAG_MAP.keys())

        stats = {}
        for sid in skills_to_check:
            try:
                store = self._get_store(sid)
                if store and store.collection:
                    count = store.collection.count()
                    stats[sid] = {"document_count": count, "status": "active"}
                else:
                    stats[sid] = {"document_count": 0, "status": "unavailable"}
            except Exception as e:
                stats[sid] = {"document_count": 0, "status": f"error: {str(e)}"}

        return stats

    def clear_skill_rag(self, skill_id: str) -> Dict[str, Any]:
        """清空指定Skill的RAG库"""
        try:
            store = self._get_store(skill_id)
            if store and store.collection:
                # 获取所有文档ID并删除
                all_ids = store.collection.get()["ids"]
                if all_ids:
                    store.collection.delete(ids=all_ids)
                # 清除缓存
                self._stores.pop(skill_id, None)
                return {"success": True, "deleted_count": len(all_ids)}
            return {"success": True, "deleted_count": 0}
        except Exception as e:
            return {"success": False, "error": str(e)}


# ============================================================================
# 便捷函数：从知识文件批量导入RAG库
# ============================================================================

def import_from_jsonl(skill_id: str, jsonl_path: str, rag_manager: SkillRAGManager) -> Dict[str, Any]:
    """
    从JSON Lines文件批量导入知识到指定Skill RAG库。

    JSONL每行格式:
    {"text": "法规/判例/模板文本", "document_name": "...", "source": "...", "paragraph": "...", ...}

    Args:
        skill_id: Skill标识
        jsonl_path: JSONL文件路径
        rag_manager: RAG管理器实例

    Returns:
        导入统计
    """
    if not os.path.exists(jsonl_path):
        return {"imported_chunks": 0, "total_docs": 0, "errors": [f"文件不存在: {jsonl_path}"]}

    documents = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                text_content = record.pop("text", "")
                documents.append({
                    "text": text_content,
                    "metadata": record,
                })
            except json.JSONDecodeError as e:
                logger.warning("JSONL解析跳过: %s", str(e))

    return rag_manager.import_documents(skill_id, documents)


def import_from_text_files(skill_id: str, file_paths: List[str],
                           rag_manager: SkillRAGManager) -> Dict[str, Any]:
    """
    从纯文本文件批量导入知识到指定Skill RAG库。

    Args:
        skill_id: Skill标识
        file_paths: 文本文件路径列表
        rag_manager: RAG管理器实例

    Returns:
        导入统计
    """
    documents = []
    for file_path in file_paths:
        if not os.path.exists(file_path):
            continue
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                text = f.read()
            documents.append({
                "text": text,
                "metadata": {
                    "document_name": os.path.basename(file_path),
                    "source": os.path.basename(file_path),
                    "file_path": file_path,
                },
            })
        except Exception as e:
            logger.error("读取文件失败 %s: %s", file_path, str(e))

    return rag_manager.import_documents(skill_id, documents)


# ============================================================================
# 全局单例
# ============================================================================

_rag_manager_instance: Optional[SkillRAGManager] = None


def get_rag_manager(base_path: str = "", embedder=None, **kwargs) -> SkillRAGManager:
    """获取全局RAG管理器单例"""
    global _rag_manager_instance
    if _rag_manager_instance is None:
        if not base_path:
            base_path = os.path.join(os.path.dirname(__file__), "..", "rag_store")
        _rag_manager_instance = SkillRAGManager(
            base_path=base_path,
            embedder=embedder,
            **kwargs
        )
    return _rag_manager_instance
