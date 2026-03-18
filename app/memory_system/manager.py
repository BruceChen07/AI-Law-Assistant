from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List

import numpy as np
from pydantic import BaseModel, Field

from app.memory_system.indexer import MemoryIndexer
from app.memory_system.search import HybridSearcher
from app.memory_system.validator import validate_report_citations

logger = logging.getLogger("law_assistant")


class Clause(BaseModel):
    clause_id: str
    title: str
    text: str


class Risk(BaseModel):
    risk_id: str
    level: str
    issue: str
    suggestion: str
    law_title: str
    article_no: str
    evidence: str = ""
    clause_id: str = ""
    confidence: float = 0.0


class MemoryManagerConfig(BaseModel):
    short_memory_token_limit: int = Field(default=4000, ge=512, le=8000)
    flush_soft_threshold: int = Field(default=4000, ge=512, le=8000)
    llm_timeout_sec: float = Field(default=8.0, ge=1.0, le=30.0)
    markdown_max_bytes: int = Field(default=2 * 1024 * 1024, ge=1024)
    retrieval_top_k: int = Field(default=6, ge=1, le=30)
    risk_dedup_similarity_threshold: float = Field(default=0.86, ge=0.5, le=0.99)
    risk_dedup_enabled: bool = False


class ShortMemoryBuffer:
    def __init__(self, token_limit: int = 4000):
        self.token_limit = token_limit
        self.items: List[Dict[str, str]] = []

    def _tok(self, text: str) -> int:
        cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
        words = len(re.findall(r"[A-Za-z0-9_]+", text))
        return cjk + words

    def total_tokens(self) -> int:
        return sum(self._tok(x.get("content", "")) for x in self.items)

    def append(self, role: str, content: str) -> None:
        self.items.append({"role": role, "content": content})
        while self.total_tokens() > self.token_limit and len(self.items) > 1:
            self.items.pop(0)

    def export(self) -> List[Dict[str, str]]:
        return list(self.items)

    def clear(self) -> None:
        self.items = []


class MemoryLifecycleManager:
    def __init__(self, memory_root: Path, indexer: MemoryIndexer, searcher: HybridSearcher, cfg: MemoryManagerConfig | None = None):
        self.memory_root = memory_root
        self.indexer = indexer
        self.searcher = searcher
        self.cfg = cfg or MemoryManagerConfig()
        self.short = ShortMemoryBuffer(self.cfg.short_memory_token_limit)
        self.memory_root.mkdir(parents=True, exist_ok=True)

    def split_contract(self, text: str) -> List[Clause]:
        lines = [x.strip() for x in str(text or "").splitlines() if x.strip()]
        if not lines:
            return []
        out: List[Clause] = []
        current: List[str] = []
        title = ""
        idx = 1
        head = re.compile(
            r"^(第[一二三四五六七八九十百千0-9]+条|Article\s+\d+|Clause\s+\d+)", re.I)
        for line in lines:
            if head.search(line):
                if current:
                    body = "\n".join(current).strip()
                    out.append(
                        Clause(clause_id=f"c{idx}", title=title or f"Clause {idx}", text=body))
                    idx += 1
                    current = []
                title = line
                current.append(line)
            else:
                current.append(line)
        if current:
            body = "\n".join(current).strip()
            out.append(
                Clause(clause_id=f"c{idx}", title=title or f"Clause {idx}", text=body))
        return out

    def _daily_file(self) -> Path:
        day = datetime.utcnow().strftime("%Y-%m-%d")
        base = self.memory_root / f"{day}.md"
        if not base.exists():
            return base
        if base.stat().st_size < self.cfg.markdown_max_bytes:
            return base
        i = 1
        while True:
            candidate = self.memory_root / f"{day}-{i}.md"
            if (not candidate.exists()) or candidate.stat().st_size < self.cfg.markdown_max_bytes:
                return candidate
            i += 1

    async def append_long_memory(self, section_title: str, content: str) -> Path:
        target = self._daily_file()
        stamp = datetime.utcnow().isoformat()
        block = f"\n## {section_title} [{stamp}]\n\n{str(content or '').strip()}\n"
        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(self._append_text, target, block)
        self.indexer.index_file(target)
        logger.info("memory_append_done file=%s section=%s bytes=%s", str(
            target), section_title, len(block.encode("utf-8")))
        return target

    def _append_text(self, path: Path, block: str) -> None:
        with path.open("a", encoding="utf-8") as f:
            f.write(block)

    async def check_and_flush(self, llm_flush_callback: Callable[[str], Awaitable[str]]) -> None:
        if self.short.total_tokens() <= self.cfg.flush_soft_threshold:
            return
        tokens = self.short.total_tokens()
        logger.info("memory_flush_trigger tokens=%s threshold=%s",
                    tokens, self.cfg.flush_soft_threshold)
        context = "\n".join(x.get("content", "")
                            for x in self.short.export()[-10:])
        prompt = f"请将以下上下文压缩为长期记忆，输出Markdown要点，包含关键实体、风险结论、法条依据：\n\n{context}"
        summary = await asyncio.wait_for(llm_flush_callback(prompt), timeout=self.cfg.llm_timeout_sec)
        await self.append_long_memory("Silent Flush", summary)
        self.short.clear()
        logger.info("memory_flush_done summary_len=%s", len(summary))

    def _risk_rank(self, level: str) -> int:
        lv = str(level or "").lower()
        if lv == "high":
            return 0
        if lv == "medium":
            return 1
        if lv == "low":
            return 2
        return 3

    def _cluster_and_dedup(self, risks: List[Risk]) -> List[Risk]:
        if not risks:
            return []
        texts = [
            f"{r.issue}\n{r.suggestion}\n{r.law_title}{r.article_no}" for r in risks]
        vecs = np.asarray(self.indexer.embedder.encode(
            texts), dtype=np.float32)
        if vecs.size == 0:
            return risks
        norms = np.linalg.norm(vecs, axis=1) + 1e-12
        groups: List[List[int]] = []
        threshold = float(self.cfg.risk_dedup_similarity_threshold)
        for i in range(len(risks)):
            merged = False
            for g in groups:
                j = g[0]
                sim = float((vecs[i] @ vecs[j]) / (norms[i] * norms[j]))
                if sim >= threshold:
                    g.append(i)
                    merged = True
                    break
            if not merged:
                groups.append([i])
        dedup: List[Risk] = []
        for g in groups:
            options = [risks[i] for i in g]
            options.sort(key=lambda x: (
                self._risk_rank(x.level), -x.confidence))
            dedup.append(options[0])
        dedup.sort(key=lambda x: (self._risk_rank(
            x.level), -x.confidence, x.risk_id))
        return dedup

    async def audit_contract(
        self,
        contract_text: str,
        llm_clause_callback: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]],
        llm_flush_callback: Callable[[str], Awaitable[str]],
        legal_catalog: Dict[str, List[str]],
    ) -> Dict[str, Any]:
        clauses = self.split_contract(contract_text)
        logger.info("memory_audit_start clauses=%s short_limit=%s top_k=%s", len(
            clauses), self.cfg.short_memory_token_limit, self.cfg.retrieval_top_k)
        all_risks: List[Risk] = []
        for i, clause in enumerate(clauses, start=1):
            hits = self.searcher.search(
                clause.text[:800], top_k=self.cfg.retrieval_top_k)
            long_ctx = "\n\n".join(h.content[:500] for h in hits)
            short_ctx = "\n".join(x.get("content", "")
                                  for x in self.short.export()[-8:])
            payload = {
                "round": i,
                "clause": clause.model_dump(),
                "short_memory": short_ctx,
                "long_memory_hits": long_ctx,
                "instruction": "仅返回JSON，必须包含字段 risks(list) 与 summary。",
            }
            res = await asyncio.wait_for(llm_clause_callback(payload), timeout=self.cfg.llm_timeout_sec)
            risks = res.get("risks") if isinstance(
                res.get("risks"), list) else []
            summary = str(res.get("summary") or "").strip()
            record = {
                "clause": clause.model_dump(),
                "summary": summary,
                "risks": risks,
            }
            await self.append_long_memory(f"Clause Review {clause.clause_id}", "```json\n" + json.dumps(record, ensure_ascii=False, indent=2) + "\n```")
            self.short.append(
                "user", f"{clause.clause_id}: {clause.text[:700]}")
            self.short.append(
                "assistant", f"{summary}\n{json.dumps(risks, ensure_ascii=False)}")
            await self.check_and_flush(llm_flush_callback)
            logger.info(
                "memory_round_done round=%s clause_id=%s hits=%s short_tokens=%s risks=%s",
                i,
                clause.clause_id,
                len(hits),
                self.short.total_tokens(),
                len(risks),
            )
            for j, r in enumerate(risks, start=1):
                all_risks.append(
                    Risk(
                        risk_id=f"{clause.clause_id}-r{j}",
                        level=str(r.get("level", "medium")).lower(),
                        issue=str(r.get("issue", "")),
                        suggestion=str(r.get("suggestion", "")),
                        law_title=str(r.get("law_title", "")),
                        article_no=str(r.get("article_no", "")),
                        evidence=str(r.get("evidence", "")),
                        clause_id=clause.clause_id,
                        confidence=float(r.get("confidence", 0.0) or 0.0),
                    )
                )
        if self.cfg.risk_dedup_enabled:
            final_risks = self._cluster_and_dedup(all_risks)
        else:
            final_risks = sorted(all_risks, key=lambda x: (self._risk_rank(x.level), -x.confidence, x.risk_id))
        report = {
            "generated_at": datetime.utcnow().isoformat(),
            "risk_count": len(final_risks),
            "risks": [r.model_dump() for r in final_risks],
        }
        validation = validate_report_citations(report, legal_catalog)
        report["legal_validation"] = validation.model_dump()
        logger.info("memory_audit_done total_risks=%s validation_ok=%s", len(
            final_risks), bool(report["legal_validation"].get("ok")))
        return report
