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
from app.services.tax_contract_parser import split_contract_clauses

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
    risk_dedup_similarity_threshold: float = Field(
        default=0.86, ge=0.5, le=0.99)
    risk_dedup_enabled: bool = False
    max_rounds: int = Field(default=16, ge=1, le=60)
    clause_query_max_chars: int = Field(default=520, ge=100, le=2000)
    hit_item_max_chars: int = Field(default=380, ge=80, le=2000)
    short_ctx_turns: int = Field(default=6, ge=1, le=20)
    short_store_clause_chars: int = Field(default=420, ge=80, le=2000)
    short_store_risks_chars: int = Field(default=900, ge=120, le=4000)
    long_hit_keep: int = Field(default=3, ge=1, le=10)


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
        raw = split_contract_clauses(str(text or ""))
        english_mode = self._is_english_text(text)

        def _is_major_heading(path: str, line: str) -> bool:
            p = str(path or "").strip()
            s = str(line or "").strip()
            if not p or p.startswith("段"):
                if re.match(r"^(?:article|section|chapter|part)\s+[0-9ivxlcdm]+(?:\.[0-9]+)*[a-z]?$", s, re.I):
                    return True
                return False
            if re.match(r"^[一二三四五六七八九十百千]+、$", p):
                return True
            if re.match(r"^第[一二三四五六七八九十百千0-9]+[章节编部分篇]$", p):
                return True
            if re.match(r"^(?:article|section|chapter|part)\s+[0-9ivxlcdm]+(?:\.[0-9]+)*[a-z]?$", p, re.I):
                return True
            if re.match(r"^[ivxlcdm]+[.)]$", p, re.I):
                return True
            return False

        def _is_minor_break(line: str, path: str) -> bool:
            s = str(line or "").strip()
            p = str(path or "").strip()
            if not s:
                return False
            if re.match(r"^[（(]?[0-9]{1,3}[)）.、]\s*", s):
                return True
            if re.match(r"^[一二三四五六七八九十百千]+[、.．]\s*", s):
                return True
            if s.startswith("、"):
                return True
            if re.match(r"^[\(\[]?[a-z][\)\].、]\s*", s, re.I):
                return True
            if re.match(r"^\(?[ivxlcdm]{1,6}[\)\].、]\s*", s, re.I):
                return True
            if re.match(r"^(?:article|section|clause)\s+[0-9ivxlcdm]+(?:\.[0-9]+)*[a-z]?\b", s, re.I):
                return True
            if re.match(r"^[0-9]{1,3}(?:\.[0-9]{1,3}){1,3}$", p):
                return True
            if re.match(r"^[0-9]{1,3}$", p):
                return True
            if re.match(r"^[ivxlcdm]+$", p, re.I):
                return True
            return False

        out: List[Clause] = []
        preamble_lines: List[str] = []
        preamble_start = 1

        section_title = ""
        section_start = 0
        section_intro: List[str] = []
        sub_start = 0
        sub_lines: List[str] = []

        def _flush_sub(include_intro: bool = False) -> None:
            nonlocal sub_start, sub_lines, section_intro
            if not sub_lines:
                return
            cid = f"c{max(1, int(sub_start or section_start or 1))}"
            title = section_title or f"Clause {cid[1:]}"
            body_lines = [title]
            if include_intro and section_intro:
                body_lines.extend(section_intro)
            body_lines.extend(sub_lines)
            out.append(Clause(clause_id=cid, title=title, text="\n".join(
                [x for x in body_lines if x]).strip()))
            sub_start = 0
            sub_lines = []

        def _flush_section() -> None:
            nonlocal section_title, section_start, section_intro, sub_start, sub_lines
            if not section_title and not section_intro and not sub_lines:
                return
            if sub_lines:
                _flush_sub(include_intro=bool(section_intro))
            elif section_title:
                cid = f"c{max(1, int(section_start or 1))}"
                body = "\n".join(
                    [x for x in [section_title, *section_intro] if x]).strip()
                if body:
                    out.append(
                        Clause(clause_id=cid, title=section_title, text=body))
            section_title = ""
            section_start = 0
            section_intro = []
            sub_start = 0
            sub_lines = []

        for raw_idx, item in enumerate(raw, start=1):
            clause_text = str(item.get("clause_text") or "").strip()
            if not clause_text:
                continue
            clause_path = str(item.get("clause_path") or "").strip()
            if _is_major_heading(clause_path, clause_text):
                if preamble_lines:
                    out.append(Clause(clause_id=f"c{preamble_start}", title=("Preamble" if english_mode else "导言"), text="\n".join(
                        preamble_lines).strip()))
                    preamble_lines = []
                _flush_section()
                section_start = raw_idx
                section_title = f"{clause_path} {clause_text}".strip()
                section_intro = []
                continue
            if section_start == 0:
                preamble_lines.append(clause_text)
                continue
            if _is_minor_break(clause_text, clause_path):
                if sub_lines:
                    _flush_sub(include_intro=bool(section_intro))
                    section_intro = []
                sub_start = raw_idx
                sub_lines = [clause_text]
            elif sub_lines:
                sub_lines.append(clause_text)
            else:
                section_intro.append(clause_text)

        if preamble_lines:
            out.append(Clause(clause_id=f"c{preamble_start}", title=("Preamble" if english_mode else "导言"), text="\n".join(
                preamble_lines).strip()))
        _flush_section()

        if out:
            out.sort(key=lambda x: int(re.sub(r"\D+", "", x.clause_id) or "1"))
            return out
        lines = [x.strip() for x in str(text or "").splitlines() if x.strip()]
        if not lines:
            return []
        return [Clause(clause_id="c1", title="Clause 1", text="\n".join(lines))]

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

    def _dump_clause_split(self, clauses: List[Clause]) -> Path:
        debug_dir = self.memory_root / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S-%f")
        target = debug_dir / f"contract_clause_split_{stamp}.json"
        payload = {
            "generated_at": datetime.utcnow().isoformat(),
            "count": len(clauses),
            "clauses": [c.model_dump() for c in clauses],
        }
        with target.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return target

    async def check_and_flush(self, llm_flush_callback: Callable[[str], Awaitable[str]]) -> None:
        if self.short.total_tokens() <= self.cfg.flush_soft_threshold:
            return
        tokens = self.short.total_tokens()
        logger.info("memory_flush_trigger tokens=%s threshold=%s",
                    tokens, self.cfg.flush_soft_threshold)
        context = "\n".join(x.get("content", "")
                            for x in self.short.export()[-10:])
        if self._is_english_text(context):
            prompt = (
                "Compress the following context into long-term memory. "
                "Output concise Markdown bullet points, keep only objective facts, clause locations, and key tax elements, "
                "and do not output risk conclusions:\n\n"
                f"{context}"
            )
        else:
            prompt = f"请将以下上下文压缩为长期记忆，输出Markdown要点，仅保留客观事实、条款定位和关键税务要素，不输出风险结论：\n\n{context}"
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

    def _clip(self, text: str, max_chars: int) -> str:
        s = str(text or "")
        limit = max(0, int(max_chars or 0))
        if limit <= 0 or len(s) <= limit:
            return s
        return s[:limit]

    def _is_english_text(self, text: str) -> bool:
        s = str(text or "")
        latin = len(re.findall(r"[A-Za-z]", s))
        cjk = len(re.findall(r"[\u4e00-\u9fff]", s))
        if latin <= 0:
            return False
        return latin >= max(30, int(cjk * 1.2))

    def _fact_slot_summary(self, clause_text: str) -> Dict[str, Any]:
        text = str(clause_text or "")
        lowered = text.lower()
        slots = {
            "invoice": any(k in lowered for k in ["发票", "invoice", "增值税普通发票", "专用发票", "电子发票"]),
            "tax_rate": any(k in lowered for k in ["税率", "tax rate", "税点", "免税"]),
            "invoice_timing": any(k in lowered for k in ["开票时间", "开票时点", "发送发票", "每次付款后", "issue invoice"]),
            "tax_obligation": any(k in lowered for k in ["纳税义务", "纳税时间", "征管", "tax obligation", "tax liability"]),
            "withholding": any(k in lowered for k in ["代扣", "代缴", "代扣代缴", "withholding"]),
        }
        return {
            "slots": slots,
            "excerpt": self._clip(text.replace("\n", " ").strip(), 220),
        }

    def _short_fact_line(self, clause: Clause, facts: Dict[str, Any]) -> str:
        slots = facts.get("slots") if isinstance(
            facts.get("slots"), dict) else {}
        enabled = [k for k, v in slots.items() if bool(v)]
        slot_text = ",".join(enabled) if enabled else "none"
        excerpt = str(facts.get("excerpt") or "")
        return f"{clause.clause_id} facts[{slot_text}] {excerpt}".strip()

    def _ranked_long_hits(self, clause_text: str, hits: List[Any]) -> List[Any]:
        terms = [x for x in re.findall(
            r"[\u4e00-\u9fffA-Za-z0-9_]{2,}", str(clause_text or "").lower()) if len(x) >= 2]
        terms = terms[:10]
        if not terms:
            return list(hits)
        scored = []
        for h in hits:
            content = str(getattr(h, "content", "") or "").lower()
            score = sum(1 for t in terms if t in content)
            scored.append((score, h))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [h for _, h in scored]

    def _compact_clauses_for_budget(self, clauses: List[Clause], budget: int) -> List[Clause]:
        items = list(clauses or [])
        limit = max(1, int(budget or 1))
        if len(items) <= limit:
            return items
        while len(items) > limit and len(items) >= 2:
            best_idx = 0
            best_score = None
            for i in range(len(items) - 1):
                left, right = items[i], items[i + 1]
                same_title = 0 if str(left.title or "") == str(
                    right.title or "") else 1
                pair_len = len(str(left.text or "")) + \
                    len(str(right.text or ""))
                score = (same_title, pair_len)
                if best_score is None or score < best_score:
                    best_score = score
                    best_idx = i
            l = items[best_idx]
            r = items[best_idx + 1]
            merged_title = l.title if str(l.title or "") == str(
                r.title or "") else f"{l.title} / {r.title}"
            merged = Clause(
                clause_id=str(l.clause_id or ""),
                title=str(merged_title or ""),
                text=f"{str(l.text or '').strip()}\n{str(r.text or '').strip()}".strip(),
            )
            items = items[:best_idx] + [merged] + items[best_idx + 2:]
        return items

    async def audit_contract(
        self,
        contract_text: str,
        llm_clause_callback: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]],
        llm_flush_callback: Callable[[str], Awaitable[str]],
        legal_catalog: Dict[str, List[str]],
    ) -> Dict[str, Any]:
        raw_clauses = self.split_contract(contract_text)
        clauses = self._compact_clauses_for_budget(
            raw_clauses, self.cfg.max_rounds)
        logger.info("memory_audit_start clauses=%s compacted=%s short_limit=%s top_k=%s", len(
            raw_clauses), max(0, len(raw_clauses) - len(clauses)), self.cfg.short_memory_token_limit, self.cfg.retrieval_top_k)
        try:
            dump_path = await asyncio.to_thread(self._dump_clause_split, clauses)
            logger.info("memory_clause_split_dumped file=%s clauses=%s", str(
                dump_path), len(clauses))
        except Exception as e:
            logger.error("memory_clause_split_dump_failed error=%s", str(e))
        all_risks: List[Risk] = []
        clause_summaries: List[str] = []
        for i, clause in enumerate(clauses, start=1):
            if i > self.cfg.max_rounds:
                logger.error(
                    "memory_audit_round_exceeded max_rounds=%s actual_round=%s", self.cfg.max_rounds, i)
                break
            hits = self.searcher.search(
                clause.text[:self.cfg.clause_query_max_chars], top_k=self.cfg.retrieval_top_k)
            ranked_hits = self._ranked_long_hits(clause.text, hits)
            kept_hits = ranked_hits[:self.cfg.long_hit_keep]
            long_ctx = "\n\n".join(self._clip(str(getattr(
                h, "content", "") or ""), self.cfg.hit_item_max_chars) for h in kept_hits)
            short_ctx_items = self.short.export(
            )[-max(2, self.cfg.short_ctx_turns * 2):]
            short_ctx = "\n".join(x.get("content", "")
                                  for x in short_ctx_items)
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
            if summary:
                clause_summaries.append(summary)
            facts = self._fact_slot_summary(clause.text)
            long_record = {
                "clause": {
                    "clause_id": clause.clause_id,
                    "title": clause.title,
                },
                "facts": facts,
            }
            await self.append_long_memory(
                f"Clause Facts {clause.clause_id}",
                "```json\n" +
                json.dumps(long_record, ensure_ascii=False,
                           indent=2) + "\n```",
            )
            self.short.append(
                "user", f"{clause.clause_id}: {self._clip(clause.text, self.cfg.short_store_clause_chars)}")
            short_fact_line = self._clip(self._short_fact_line(
                clause, facts), self.cfg.short_store_risks_chars)
            self.short.append("assistant", short_fact_line)
            await self.check_and_flush(llm_flush_callback)
            logger.info(
                "memory_round_done round=%s clause_id=%s hits=%s short_tokens=%s risks=%s",
                i,
                clause.clause_id,
                len(kept_hits),
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
            final_risks = sorted(all_risks, key=lambda x: (
                self._risk_rank(x.level), -x.confidence, x.risk_id))
        final_summary = ""
        if clause_summaries:
            final_summary = sorted(clause_summaries, key=len, reverse=True)[0]
        report = {
            "generated_at": datetime.utcnow().isoformat(),
            "summary": final_summary,
            "clause_summaries": clause_summaries[-8:],
            "risk_count": len(final_risks),
            "risks": [r.model_dump() for r in final_risks],
        }
        validation = validate_report_citations(report, legal_catalog)
        report["legal_validation"] = validation.model_dump()
        logger.info("memory_audit_done total_risks=%s validation_ok=%s", len(
            final_risks), bool(report["legal_validation"].get("ok")))
        return report
