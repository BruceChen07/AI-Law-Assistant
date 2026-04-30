"""
Contract Audit Facade.
职责: 作为合同审计模块的统一入口，封装 IO 操作并调用拆分后的子模块。
"""
import time
import uuid
import hashlib
import json
import re
import structlog
from typing import Dict, Any, Optional, Callable, List

from app.core.utils import extract_text_with_config
from app.services.audit_utils import _safe_int, _normalize_citation_item, _enrich_citations
from app.services.audit_retrieval import _normalize_retrieval_options, _retrieve_regulation_evidence
from app.services.contract_audit_modules.clause_builder import build_preview_clauses
from app.services.contract_audit_modules import memory_pipeline as memory_pipeline_module
from app.services.contract_audit_modules.memory_pipeline import execute_memory_audit
from app.services.contract_audit_modules.result_assembler import attach_risk_locations
from app.services.contract_audit_modules.trace_writer import write_audit_trace, trace_clip
from app.memory_system.search import HybridSearcher
from app.memory_system.experience_repo import save_audit_episode

logger = structlog.get_logger(__name__)


def _build_preview_clauses(text: str):
    return build_preview_clauses(text)


def _attach_risk_locations(audit, clauses):
    return attach_risk_locations(audit, clauses)


def _get_memory_embedder(lang: str = "zh", cfg: Optional[Dict[str, Any]] = None):
    getter = getattr(memory_pipeline_module, "get_memory_embedder", None)
    if not callable(getter):
        return None
    try:
        return getter(lang, cfg=cfg)
    except TypeError:
        try:
            return getter(lang)
        except TypeError:
            try:
                return getter()
            except Exception:
                return None
        except Exception:
            return None
    except Exception:
        return None


def _get_memory_runtime_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    raw = cfg.get("memory_runtime_config") if isinstance(
        cfg.get("memory_runtime_config"), dict) else {}
    mode = str(raw.get("memory_mode_when_disabled")
               or "classic").strip().lower()
    if mode not in {"classic"}:
        mode = "classic"
    return {
        "memory_module_enabled": bool(raw.get("memory_module_enabled", True)),
        "memory_mode_when_disabled": mode,
        "memory_disable_fallback_on_error": bool(raw.get("memory_disable_fallback_on_error", True)),
        "memory_max_prompt_chars_per_clause": max(300, int(raw.get("memory_max_prompt_chars_per_clause") or 2400)),
    }


def _load_llm_json_object(raw_text: str) -> Dict[str, Any]:
    s = str(raw_text or "").strip()
    if not s:
        return {}
    fenced = re.sub(r"^\s*```(?:json)?\s*", "", s,
                    count=1, flags=re.IGNORECASE)
    fenced = re.sub(r"\s*```\s*$", "", fenced, count=1,
                    flags=re.IGNORECASE).strip()
    candidates: List[str] = [x for x in [fenced, s] if x]
    if fenced:
        left = fenced.find("{")
        right = fenced.rfind("}")
        if left >= 0 and right > left:
            obj_text = fenced[left:right + 1].strip()
            if obj_text and obj_text not in candidates:
                candidates.append(obj_text)
    for item in candidates:
        try:
            parsed = json.loads(item)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue
    return {}


def _build_classic_audit(
    cfg: Dict[str, Any],
    llm,
    text: str,
    lang: str,
    preview_clauses: List[Dict[str, Any]],
    evidence_items: List[Dict[str, Any]],
    retrieval_opts: Dict[str, Any],
    audit_id: str,
) -> Dict[str, Any]:
    norm_lang = "en" if str(lang or "").lower() == "en" else "zh"
    allowed_citation_ids = {
        str(it.get("citation_id") or "").strip()
        for it in evidence_items
        if str(it.get("citation_id") or "").strip()
    }
    lines = []
    for idx, it in enumerate(list(evidence_items or [])[:24], start=1):
        cid = str(it.get("citation_id") or "").strip()
        law = str(it.get("law_title") or it.get("title") or "").strip()
        article = str(it.get("article_no") or "").strip()
        if not cid or not law:
            continue
        lines.append(f"- C{idx} [{cid}] {law} {article}".strip())
    whitelist_text = "\n".join(lines) if lines else "-"
    max_clause_chars = int(_get_memory_runtime_config(
        cfg).get("memory_max_prompt_chars_per_clause") or 2400)
    clause_lines = []
    for c in list(preview_clauses or [])[:10]:
        cid = str(c.get("clause_id") or "")
        title = str(c.get("title") or c.get("clause_path") or "")
        body = str(c.get("clause_text") or c.get("text") or "")
        clause_lines.append(f"[{cid}] {title}\n{body[:max_clause_chars]}")
    clause_text = "\n\n".join(clause_lines) if clause_lines else str(
        text or "")[:max_clause_chars]

    if norm_lang == "en":
        system = "You are a senior contract audit lawyer. Output ONLY JSON."
        user = (
            "Use only the contract text and whitelist evidence below.\n"
            "Do not output reasoning process.\n"
            f"Whitelist:\n{whitelist_text}\n\n"
            f"Contract Clauses:\n{clause_text}\n\n"
            "JSON: {\"summary\":\"\",\"risks\":[{\"level\":\"high|medium|low\",\"issue\":\"\",\"suggestion\":\"\",\"citation_id\":\"\",\"law_title\":\"\",\"article_no\":\"\",\"evidence\":\"\",\"confidence\":0.0,\"clause_id\":\"\"}]}"
        )
    else:
        system = "你是资深合同审计律师。只输出JSON。"
        user = (
            "仅根据合同文本与白名单证据输出结果；不要输出推理过程。\n"
            f"白名单:\n{whitelist_text}\n\n"
            f"合同条款:\n{clause_text}\n\n"
            "JSON: {\"summary\":\"\",\"risks\":[{\"level\":\"high|medium|low\",\"issue\":\"\",\"suggestion\":\"\",\"citation_id\":\"\",\"law_title\":\"\",\"article_no\":\"\",\"evidence\":\"\",\"confidence\":0.0,\"clause_id\":\"\"}]}"
        )

    trace_meta = {
        "module": "contract_audit",
        "stage": "contract_classic_audit",
        "audit_id": audit_id,
        "lang": norm_lang,
        "audit_mode": str(retrieval_opts.get("audit_mode") or ""),
    }
    result_text, _raw = llm.chat(
        [{"role": "system", "content": system},
            {"role": "user", "content": user}],
        overrides={"max_tokens": 900, "enable_thinking": False, "reasoning_effort": "low",
                   "thinking_budget_tokens": 0, "_trace_meta": trace_meta},
    )
    parsed = _load_llm_json_object(result_text)
    risks = parsed.get("risks") if isinstance(
        parsed.get("risks"), list) else []
    clause_map = {str(c.get("clause_id") or ""): c for c in preview_clauses}
    normalized_risks = []
    for idx, r in enumerate(risks, start=1):
        if not isinstance(r, dict):
            continue
        level = str(r.get("level") or "medium").strip().lower()
        if level not in {"high", "medium", "low"}:
            level = "medium"
        clause_id = str(r.get("clause_id") or "")
        c = clause_map.get(clause_id) or (
            preview_clauses[0] if preview_clauses else {})
        input_cid = str(r.get("citation_id") or "").strip()
        mapped_cid = input_cid if input_cid in allowed_citation_ids else ""
        law_title = str(r.get("law_title") or "")
        article_no = str(r.get("article_no") or "")
        basis = f"{law_title} {article_no}".strip()
        normalized_risks.append(
            {
                "level": level,
                "issue": str(r.get("issue") or ""),
                "suggestion": str(r.get("suggestion") or ""),
                "basis": basis,
                "law_reference": basis,
                "citation_id": mapped_cid,
                "citation_status": "mapped" if mapped_cid else "unmapped",
                "evidence": str(r.get("evidence") or ""),
                "law_title": law_title,
                "article_no": article_no,
                "location": {
                    "risk_id": f"classic-r{idx}",
                    "clause_id": str(c.get("clause_id") or ""),
                    "anchor_id": str(c.get("anchor_id") or ""),
                    "page_no": int(c.get("page_no") or 0),
                    "paragraph_no": str(c.get("paragraph_no") or ""),
                    "clause_path": str(c.get("clause_path") or ""),
                    "quote": str(r.get("evidence") or ""),
                    "score": round(float(r.get("confidence") or 0.0), 4),
                },
            }
        )
    risk_summary = {"high": 0, "medium": 0, "low": 0}
    for item in normalized_risks:
        risk_summary[str(item.get("level") or "low")] += 1
    summary = str(parsed.get("summary") or "").strip() or (
        f"经典审计完成，共发现 {len(normalized_risks)} 项风险" if norm_lang != "en" else
        f"Classic audit completed, found {len(normalized_risks)} risks"
    )
    audit = {
        "summary": summary,
        "executive_opinion": [],
        "risk_summary": risk_summary,
        "risks": normalized_risks,
        "citations": _enrich_citations(evidence_items, evidence_items),
        "legal_validation": {"ok": True, "issues": []},
    }
    return {
        "audit": audit,
        "meta": {
            "memory_mode": False,
            "memory_mode_enabled": False,
            "execution_path": "classic",
            "memory_llm_call_count": 1,
            "memory_report_risk_count": len(normalized_risks),
            "memory_validation_ok": True,
        },
        "raw": {"mode": "classic"},
    }


def _parse_citation_pack_ref(citation_id: str) -> Dict[str, str]:
    cid = str(citation_id or "").strip()
    if not cid:
        return {"regulation_id": "", "version_id": ""}
    parts = cid.split(":")
    if len(parts) >= 4:
        return {
            "regulation_id": str(parts[1] or "").strip(),
            "version_id": str(parts[2] or "").strip(),
        }
    return {"regulation_id": "", "version_id": ""}


def get_regulation_pack_fingerprint(
    evidence_items: List[Dict[str, Any]],
    retrieval_opts: Optional[Dict[str, Any]] = None,
    lang: str = "zh",
) -> Dict[str, Any]:
    """
    Build deterministic regulation pack identity for memory isolation.
    This function maps to capability: GetRegulationPackFingerprint.
    """
    members: List[str] = []
    citation_refs: List[str] = []
    for item in list(evidence_items or []):
        citation_id = str(item.get("citation_id") or "").strip()
        if citation_id:
            citation_refs.append(citation_id)
        reg_id = str(item.get("regulation_id") or "").strip()
        ver_id = str(item.get("version_id") or "").strip()
        if not reg_id or not ver_id:
            parsed = _parse_citation_pack_ref(citation_id)
            reg_id = reg_id or parsed.get("regulation_id", "")
            ver_id = ver_id or parsed.get("version_id", "")
        if reg_id and ver_id:
            members.append(f"{reg_id}:{ver_id}")
        elif citation_id:
            members.append(f"cid:{citation_id}")
        else:
            law = str(item.get("law_title") or item.get("title") or "").strip()
            article = str(item.get("article_no") or "").strip()
            if law or article:
                members.append(f"law:{law}#{article}")
    members = sorted(set(members))
    citation_refs = sorted(set(citation_refs))

    opts = dict(retrieval_opts or {})
    scope = {
        "lang": str(lang or "zh"),
        "region": str(opts.get("region") or ""),
        "industry": str(opts.get("industry") or ""),
        "date": str(opts.get("date") or ""),
    }
    pack_seed = "|".join(members) if members else f"empty_pack:{scope['lang']}"
    regulation_pack_id = "rp_" + \
        hashlib.sha1(pack_seed.encode("utf-8")).hexdigest()[:20]
    fingerprint_payload = {
        "pack_id": regulation_pack_id,
        "members": members,
        "citations": citation_refs,
        "scope": scope,
    }
    regulation_fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, ensure_ascii=False,
                   sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "regulation_pack_id": regulation_pack_id,
        "regulation_fingerprint": regulation_fingerprint,
        "regulation_pack_members": members,
    }


def audit_contract(
    cfg: Dict[str, Any],
    llm,
    file_path: str,
    lang: str = "zh",
    embedder=None,
    reranker=None,
    translator=None,
    retrieval_options: Optional[Dict[str, Any]] = None,
    progress_cb: Optional[Callable[[str, int, str], None]] = None
) -> Dict[str, Any]:
    """
    A unified facade function for contract auditing.
    It integrates text extraction, clause preview, evidence retrieval, and LLM clause-level auditing with memory.
    It remains completely transparent to upper-level calls.
    """
    def _report(stage: str, percent: int, message: str = "") -> None:
        if not callable(progress_cb):
            return
        try:
            progress_cb(stage, percent, message)
        except Exception:
            return

    audit_started_at = time.perf_counter()
    audit_id = f"audit_{uuid.uuid4().hex[:12]}"
    _report("extracting", 15, "extracting text")
    logger.info("audit_extract_start", file=file_path,
                lang=lang, audit_id=audit_id)
    text, meta = extract_text_with_config(cfg, file_path)
    preview_clauses = build_preview_clauses(text)
    logger.info(
        "audit_extract_done",
        file=file_path,
        text_length=len(text),
        ocr_used=meta.get("ocr_used"),
        ocr_engine=meta.get("ocr_engine"),
        page_count=meta.get("page_count")
    )
    _report("extract_done", 30, "extract complete")
    opts = _normalize_retrieval_options(retrieval_options)
    _report("retrieval", 40, "retrieving evidence")
    retrieval_embedder = embedder
    if retrieval_embedder is None:
        try:
            retrieval_embedder = _get_memory_embedder(lang, cfg)
        except TypeError:
            retrieval_embedder = _get_memory_embedder()
    if translator is None:
        retrieved = _retrieve_regulation_evidence(
            cfg, text, lang, opts, embedder=retrieval_embedder, reranker=reranker)
    else:
        retrieved = _retrieve_regulation_evidence(
            cfg, text, lang, opts, embedder=retrieval_embedder, reranker=reranker, translator=translator)
    logger.info(
        "audit_retrieval_done",
        file=file_path,
        mode=opts.get("audit_mode"),
        used=retrieved.get("used"),
        queries=retrieved.get("queries"),
        success=retrieved.get("query_success", 0),
        failed=retrieved.get("query_failed", 0),
        evidence_count=len(retrieved.get("items") or []),
        degraded=retrieved.get("retrieval_degraded", False),
        degraded_reasons=retrieved.get("retrieval_degraded_reasons", []),
    )
    _report("retrieval_done", 55, "evidence ready")
    if opts.get("require_full_coverage") and _safe_int(retrieved.get("query_failed", 0), 0) > 0:
        raise RuntimeError("retrieval coverage incomplete")
    evidence_items = [_normalize_citation_item(
        it) for it in (retrieved.get("items") or [])]
    regulation_identity = get_regulation_pack_fingerprint(
        evidence_items=evidence_items,
        retrieval_opts=opts,
        lang=lang,
    )
    write_audit_trace(
        cfg,
        "contract_split",
        {
            "audit_id": audit_id,
            "file_path": file_path,
            "lang": lang,
            "text_length": len(text),
            "clause_count": len(preview_clauses),
            "clauses": [
                {
                    "clause_id": str(c.get("clause_id") or ""),
                    "clause_path": str(c.get("clause_path") or ""),
                    "page_no": int(c.get("page_no") or 0),
                    "paragraph_no": str(c.get("paragraph_no") or ""),
                    "text_len": len(str(c.get("clause_text") or "")),
                    "text_preview": trace_clip(c.get("clause_text"), 220),
                }
                for c in preview_clauses[:120]
            ],
            "regulation_pack_id": regulation_identity.get("regulation_pack_id", ""),
            "regulation_fingerprint": regulation_identity.get("regulation_fingerprint", ""),
            "regulation_pack_members": regulation_identity.get("regulation_pack_members", []),
        },
    )
    memory_runtime_cfg = _get_memory_runtime_config(cfg)
    memory_enabled = bool(memory_runtime_cfg.get(
        "memory_module_enabled", True))
    fallback_on_error = bool(memory_runtime_cfg.get(
        "memory_disable_fallback_on_error", True))
    execution_path = "memory"
    memory_result: Dict[str, Any] = {}
    _report("auditing", 70, "auditing clauses")
    if memory_enabled:
        logger.info("audit_memory_enabled", file=file_path,
                    clauses=len(preview_clauses))
        custom_embedder = None
        if callable(_get_memory_embedder):
            try:
                custom_embedder = _get_memory_embedder(lang, cfg)
            except TypeError:
                custom_embedder = _get_memory_embedder()
        if custom_embedder is not None and hasattr(custom_embedder, "encode"):
            memory_pipeline_module.get_memory_embedder = lambda _lang="zh", cfg=None: custom_embedder
        if HybridSearcher is not None:
            memory_pipeline_module.HybridSearcher = HybridSearcher
        try:
            memory_result = execute_memory_audit(
                cfg=cfg,
                llm=llm,
                text=text,
                lang=lang,
                preview_clauses=preview_clauses,
                evidence_items=evidence_items,
                retrieval_opts=opts,
                trace_context={
                    "module": "contract_audit",
                    "file_path": file_path,
                    "audit_id": audit_id,
                    "regulation_pack_id": regulation_identity.get("regulation_pack_id", ""),
                    "regulation_fingerprint": regulation_identity.get("regulation_fingerprint", ""),
                },
            )
            execution_path = "memory"
        except Exception as e:
            if not fallback_on_error:
                raise
            logger.warning("memory_audit_failed_fallback_to_classic",
                           audit_id=audit_id, error=str(e))
            memory_result = _build_classic_audit(
                cfg=cfg,
                llm=llm,
                text=text,
                lang=lang,
                preview_clauses=preview_clauses,
                evidence_items=evidence_items,
                retrieval_opts=opts,
                audit_id=audit_id,
            )
            execution_path = "classic_fallback"
    else:
        logger.info("audit_memory_disabled_use_classic",
                    audit_id=audit_id, file=file_path)
        memory_result = _build_classic_audit(
            cfg=cfg,
            llm=llm,
            text=text,
            lang=lang,
            preview_clauses=preview_clauses,
            evidence_items=evidence_items,
            retrieval_opts=opts,
            audit_id=audit_id,
        )
        execution_path = "classic"
    _report("audit_done", 90, "audit complete")
    memory_meta = memory_result.get("meta") if isinstance(
        memory_result.get("meta"), dict) else {}
    citation_ids = [
        str(it.get("citation_id", "")).strip()
        for it in evidence_items
        if str(it.get("citation_id", "")).strip()
    ]
    audit_duration_ms = int((time.perf_counter() - audit_started_at) * 1000)
    output_meta = {
        "language": "en" if str(lang or "").lower() == "en" else "zh",
        "audit_id": audit_id,
        "text_length": len(text),
        "ocr_used": meta.get("ocr_used"),
        "ocr_engine": meta.get("ocr_engine"),
        "page_count": meta.get("page_count"),
        "llm_model": (cfg.get("llm_config") or {}).get("model", ""),
        "retrieval_mode": opts.get("audit_mode"),
        "risk_detection_mode": opts.get("risk_detection_mode"),
        "retrieval_used": retrieved.get("used"),
        "retrieval_queries": retrieved.get("queries"),
        "retrieval_chunk_total": retrieved.get("chunk_total", 0),
        "retrieval_query_success": retrieved.get("query_success", 0),
        "retrieval_query_failed": retrieved.get("query_failed", 0),
        "retrieval_degraded": bool(retrieved.get("retrieval_degraded", False)),
        "retrieval_degraded_reasons": list(retrieved.get("retrieval_degraded_reasons") or []),
        "retrieval_coverage": 0.0 if _safe_int(retrieved.get("chunk_total", 0), 0) == 0 else round(_safe_int(retrieved.get("query_success", 0), 0) / _safe_int(retrieved.get("chunk_total", 0), 0), 4),
        "retrieval_failed_chunks": retrieved.get("failed_chunks", []),
        "retrieved_chunks": len(evidence_items),
        "evidence_count": len(evidence_items),
        "citation_ids": citation_ids,
        "retrieval_filters": {
            "region": opts.get("region"),
            "date": opts.get("date"),
            "industry": opts.get("industry"),
            "tax_focus": opts.get("tax_focus")
        },
        "tax_focus": opts.get("tax_focus"),
        "require_full_coverage": opts.get("require_full_coverage"),
        "tax_evidence_count": len([
            it for it in evidence_items
            if _safe_int(it.get("tax_relevance", 0), 0) > 0
        ]),
        "preview_clause_total": len(preview_clauses),
        "audit_duration_ms": audit_duration_ms,
        "regulation_pack_id": regulation_identity.get("regulation_pack_id", ""),
        "regulation_fingerprint": regulation_identity.get("regulation_fingerprint", ""),
        "regulation_pack_members": regulation_identity.get("regulation_pack_members", []),
        "memory_module_enabled": memory_enabled,
        "execution_path": execution_path,
        **memory_meta,
    }
    write_audit_trace(
        cfg,
        "audit_done",
        {
            "audit_id": audit_id,
            "file_path": file_path,
            "duration_ms": audit_duration_ms,
            "preview_clause_total": len(preview_clauses),
            "memory_rounds": output_meta.get("memory_clause_rounds", 0),
            "memory_llm_call_count": output_meta.get("memory_llm_call_count", 0),
            "memory_llm_total_tokens": output_meta.get("memory_llm_total_tokens", 0),
            "risk_count": output_meta.get("memory_report_risk_count", 0),
            "suppressed_missing_risks": output_meta.get("suppressed_missing_risks", 0),
            "parse_failed_clauses": output_meta.get("parse_failed_clauses", 0),
        },
    )
    logger.info(
        "audit_metrics_done",
        file=file_path,
        duration_ms=audit_duration_ms,
        audit_id=audit_id,
        preview_clauses=len(preview_clauses),
        memory_rounds=output_meta.get("memory_clause_rounds", 0),
        llm_calls=output_meta.get("memory_llm_call_count", 0),
        llm_total_tokens=output_meta.get("memory_llm_total_tokens", 0),
        risks=output_meta.get("memory_report_risk_count", 0),
    )
    episode_meta: Dict[str, Any] = {}
    if execution_path == "memory":
        try:
            episode_meta = save_audit_episode(
                cfg=cfg,
                audit_id=audit_id,
                regulation_pack_id=regulation_identity.get(
                    "regulation_pack_id", ""),
                regulation_fingerprint=regulation_identity.get(
                    "regulation_fingerprint", ""),
                retrieval_opts=opts,
                preview_clauses=preview_clauses,
                audit=memory_result.get("audit") if isinstance(
                    memory_result.get("audit"), dict) else {},
                meta=output_meta,
            )
        except Exception as e:
            logger.warning("save_audit_episode_failed",
                           audit_id=audit_id, error=str(e))
            episode_meta = {"saved": False,
                            "reason": "exception", "error": str(e)}
    else:
        episode_meta = {
            "saved": False,
            "reason": "memory_module_disabled_or_fallback",
        }
    output_meta["episode_saved"] = bool(episode_meta.get("saved", False))
    output_meta["episode_id"] = str(episode_meta.get("episode_id") or "")
    output_meta["episode_status"] = str(episode_meta.get("status") or "")
    return {
        "audit": memory_result.get("audit"),
        "meta": output_meta,
        "raw": memory_result.get("raw") if isinstance(memory_result.get("raw"), dict) else {"mode": "memory"}
    }
