"""
Contract Audit Facade.
职责: 作为合同审计模块的统一入口，封装 IO 操作并调用拆分后的子模块。
"""
import time
import uuid
import hashlib
import json
import re
import socket
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
from app.core.llm_trace import new_trace_id

logger = structlog.get_logger(__name__)


def _build_preview_clauses(text: str):
    return build_preview_clauses(text)


def _attach_risk_locations(audit, clauses):
    return attach_risk_locations(audit, clauses)


def _chat_with_task_profile(llm, messages, task_profile: str, overrides=None):
    if hasattr(llm, "chat_with_profile"):
        return llm.chat_with_profile(messages, task_profile, overrides=overrides)
    next_overrides = dict(overrides or {})
    next_overrides["_task_profile"] = task_profile
    return llm.chat(messages, overrides=next_overrides)


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


def _get_memory_temporary_disable_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    raw = cfg.get("memory_temporary_disable") if isinstance(
        cfg.get("memory_temporary_disable"), dict) else {}
    fallback_mode = str(raw.get("fallback_mode") or "classic").strip().lower()
    if fallback_mode not in {"classic"}:
        fallback_mode = "classic"
    return {
        "enabled": bool(raw.get("enabled", False)),
        "fallback_mode": fallback_mode,
        "reason": str(raw.get("reason") or "edge_llm_context_limit").strip() or "edge_llm_context_limit",
        "trigger_source": str(raw.get("trigger_source") or "config.memory_temporary_disable").strip() or "config.memory_temporary_disable",
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
    trace_id: str = "",
) -> Dict[str, Any]:
    norm_lang = "en" if str(lang or "").lower() == "en" else "zh"
    allowed_citation_ids = {
        str(it.get("citation_id") or "").strip()
        for it in evidence_items
        if str(it.get("citation_id") or "").strip()
    }
    # Build whitelist (citation IDs only) for structured matching
    lines = []
    for idx, it in enumerate(list(evidence_items or [])[:24], start=1):
        cid = str(it.get("citation_id") or "").strip()
        law = str(it.get("law_title") or it.get("title") or "").strip()
        article = str(it.get("article_no") or "").strip()
        if not cid or not law:
            continue
        lines.append(f"- C{idx} [{cid}] {law} {article}".strip())
    whitelist_text = "\n".join(lines) if lines else "-"

    # Build reference evidence text from ALL evidence items (text content, not just IDs)
    # This ensures LLM has regulatory context even when structured fields are missing.
    reference_lines = []
    for idx, it in enumerate(list(evidence_items or [])[:24], start=1):
        content = (
            str(it.get("content") or it.get("source_text") or it.get("excerpt") or "")
        ).strip()
        law = str(it.get("law_title") or it.get("title") or "").strip()
        article = str(it.get("article_no") or "").strip()
        header = f"[E{idx}] {law} {article}".strip()
        if content:
            reference_lines.append(f"{header}\n{content[:1200]}")
        elif law:
            reference_lines.append(header)
    reference_evidence_text = "\n\n".join(
        reference_lines) if reference_lines else ""
    if reference_evidence_text:
        reference_evidence_text = (
            "参考法规证据（共{}条）:\n".format(len(reference_lines))
            + reference_evidence_text
        )

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

    # Full contract text for cross-clause joint analysis (Classic mode)
    full_ctx_budget = max(1500, int(cfg.get("memory_full_context_budget_chars") or 6000))
    full_text_context = str(text or "")[:full_ctx_budget]

    if norm_lang == "en":
        system = "You are a senior tax contract audit lawyer. Output ONLY JSON."
        user = "Use only the contract text and reference evidence below.\n"
        user += "Do not output reasoning process.\n\n"
        user += "TAX RISK ONLY: Only output tax-related risks (tax rate, invoicing, tax obligations, withholding tax, tax compliance). "
        user += "Do NOT output contract clause descriptions, breach of contract, or payment obligation issues that are not tax risks.\n"
        user += "CITATION REQUIRED: Every risk MUST include a citation_id from the whitelist and the corresponding law_title. "
        user += "If you cannot match a risk to a regulation in the whitelist, do NOT output that risk.\n"
        user += "RISK DESCRIPTION: The 'issue' field must describe the specific tax compliance risk and why it violates or conflicts with the cited regulation. "
        user += "Do NOT simply copy contract clause text as the issue.\n"
        user += "CROSS-CLAUSE RULE: Before flagging 'missing/unspecified' risks, verify against the full contract text. "
        user += "If the element is covered elsewhere in the contract, do NOT flag it.\n"
        if reference_evidence_text:
            user += f"{reference_evidence_text}\n\n"
        user += f"Whitelist (citation IDs):\n{whitelist_text}\n\n"
        user += f"Full Contract Text (for cross-clause verification):\n{full_text_context}\n\n"
        user += f"Contract Clauses (structured):\n{clause_text}\n\n"
        user += "JSON: {\"summary\":\"\",\"risks\":[{\"level\":\"high|medium|low\",\"issue\":\"\",\"suggestion\":\"\",\"citation_id\":\"\",\"law_title\":\"\",\"article_no\":\"\",\"evidence\":\"\",\"confidence\":0.0,\"clause_id\":\"\"}]}"
    else:
        system = "你是资深财税合同审计律师。只输出JSON。"
        user = "仅根据合同文本与参考法规证据输出结果；不要输出推理过程。\n\n"
        user += "【仅限涉税风险】只输出涉税风险（税率、开票、纳税义务、代扣代缴、税务合规等）。"
        user += "不要输出合同条款描述、违约责任、支付义务等非涉税内容。\n"
        user += "【强制引用】每个风险项必须包含 citation_id（从白名单中选择）和对应的 law_title。"
        user += "无法从白名单中匹配到法规条款的风险，不得输出。\n"
        user += "【风险描述】issue 字段必须描述具体的税务合规风险及其违反或冲突的法规依据，不得仅复制合同条款原文。\n"
        user += "【跨条款联合校验】标记'缺失/未约定'风险前，必须核查合同全文。若该要素已在其他条款中约定，不得标记为风险。\n"
        if reference_evidence_text:
            user += f"{reference_evidence_text}\n\n"
        user += f"白名单（引用ID）:\n{whitelist_text}\n\n"
        user += f"合同全文(用于跨条款联合校验):\n{full_text_context}\n\n"
        user += f"合同条款(结构化拆分):\n{clause_text}\n\n"
        user += "JSON: {\"summary\":\"\",\"risks\":[{\"level\":\"high|medium|low\",\"issue\":\"\",\"suggestion\":\"\",\"citation_id\":\"\",\"law_title\":\"\",\"article_no\":\"\",\"evidence\":\"\",\"confidence\":0.0,\"clause_id\":\"\"}]}"

    trace_meta = {
        "module": "contract_audit",
        "stage": "contract_classic_audit",
        "audit_id": audit_id,
        "trace_id": trace_id,
        "lang": norm_lang,
        "audit_mode": str(retrieval_opts.get("audit_mode") or ""),
    }
    result_text, _raw = _chat_with_task_profile(
        llm,
        [{"role": "system", "content": system},
         {"role": "user", "content": user}],
        "contract_audit_main",
        overrides={"max_tokens": 2048, "enable_thinking": False, "reasoning_effort": "low",
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
        issue_text = str(r.get("issue") or "").strip()
        evidence_text = str(r.get("evidence") or "").strip()
        # Skip risks with completely empty content (LLM returned a skeleton without substance)
        if len(issue_text) < 6 and len(evidence_text) < 6:
            continue
        # Skip risks without any legal reference (no citation_id AND no law_title)
        # These are likely the model echoing contract text rather than doing tax risk analysis
        if not mapped_cid and not law_title.strip():
            continue
        normalized_risks.append(
            {
                "level": level,
                "issue": issue_text,
                "suggestion": str(r.get("suggestion") or ""),
                "basis": basis,
                "law_reference": basis,
                "citation_id": mapped_cid,
                "citation_status": "mapped" if mapped_cid else "unmapped",
                "evidence": evidence_text,
                "law_title": law_title,
                "article_no": article_no,
                "location": {
                    "risk_id": f"classic-r{idx}",
                    "clause_id": str(c.get("clause_id") or ""),
                    "anchor_id": str(c.get("anchor_id") or ""),
                    "page_no": int(c.get("page_no") or 0),
                    "paragraph_no": str(c.get("paragraph_no") or ""),
                    "clause_path": str(c.get("clause_path") or ""),
                    "quote": evidence_text,
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
    trace_id = new_trace_id()
    _report("extracting", 15, "extracting text")
    logger.info("audit_extract_start", file=file_path,
                lang=lang, audit_id=audit_id, trace_id=trace_id)
    text, meta = extract_text_with_config(cfg, file_path)
    preview_clauses = build_preview_clauses(text)

    # Auto-generate internal markdown file for full-text LLM analysis.
    # This file is stored in a hidden internal directory (.audit_internal/)
    # and is NOT exposed to end users. It serves as intermediate material
    # for the LLM's cross-clause joint analysis and subsequent data processing.
    _internal_md_path = ""
    try:
        from app.services.markdown_export import contract_text_to_markdown
        internal_dir = os.path.join(
            cfg.get("data_dir", "data"), ".audit_internal")
        os.makedirs(internal_dir, exist_ok=True)
        source_name = os.path.basename(file_path)
        md_filename = f"{audit_id}_{os.path.splitext(source_name)[0]}.md"
        _internal_md_path = os.path.join(internal_dir, md_filename)
        md_content = contract_text_to_markdown(
            text=text,
            source_filename=source_name,
            meta=meta,
            include_metadata_header=True,
        )
        with open(_internal_md_path, "w", encoding="utf-8") as _mf:
            _mf.write(md_content)
        logger.info("audit_internal_md_generated",
                    path=_internal_md_path, length=len(md_content))
    except Exception as _md_err:
        logger.warning("audit_internal_md_failed", error=str(_md_err))

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
    memory_temporary_disable_cfg = _get_memory_temporary_disable_config(cfg)
    runtime_memory_enabled = bool(memory_runtime_cfg.get(
        "memory_module_enabled", True))
    memory_temporarily_disabled = bool(
        memory_temporary_disable_cfg.get("enabled", False))
    effective_disable_mode = str(
        memory_temporary_disable_cfg.get("fallback_mode")
        or memory_runtime_cfg.get("memory_mode_when_disabled")
        or "classic"
    ).strip().lower()
    memory_enabled = runtime_memory_enabled and not memory_temporarily_disabled
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
        if (custom_embedder is not None and hasattr(custom_embedder, "encode")) or HybridSearcher is not None:
            runtime_getter = (lambda _lang="zh", cfg=None: custom_embedder) if (
                custom_embedder is not None and hasattr(
                    custom_embedder, "encode")
            ) else None
            setter = getattr(memory_pipeline_module,
                             "set_runtime_overrides", None)
            if callable(setter):
                setter(
                    get_memory_embedder=runtime_getter,
                    hybrid_searcher=HybridSearcher if HybridSearcher is not None else None,
                )
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
                    "trace_id": trace_id,
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
                trace_id=trace_id,
            )
            execution_path = "classic_fallback"
    else:
        if memory_temporarily_disabled:
            logger.warning(
                "memory_temporarily_disabled audit_id=%s file=%s service_node=%s trigger_source=%s disable_reason=%s fallback_mode=%s runtime_memory_enabled=%s clauses=%s",
                audit_id,
                file_path,
                socket.gethostname(),
                str(memory_temporary_disable_cfg.get(
                    "trigger_source") or "config.memory_temporary_disable"),
                str(memory_temporary_disable_cfg.get(
                    "reason") or "edge_llm_context_limit"),
                effective_disable_mode,
                runtime_memory_enabled,
                len(preview_clauses),
            )
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
            trace_id=trace_id,
        )
        execution_path = "classic" if effective_disable_mode == "classic" else effective_disable_mode
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
        "memory_runtime_module_enabled": runtime_memory_enabled,
        "memory_temporarily_disabled": memory_temporarily_disabled,
        "memory_temporary_disable_reason": memory_temporary_disable_cfg.get("reason"),
        "memory_temporary_disable_trigger_source": memory_temporary_disable_cfg.get("trigger_source"),
        "memory_temporary_disable_fallback_mode": memory_temporary_disable_cfg.get("fallback_mode"),
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
            "memory_temporarily_disabled": memory_temporarily_disabled,
            "memory_temporary_disable_reason": output_meta.get("memory_temporary_disable_reason", ""),
            "memory_temporary_disable_trigger_source": output_meta.get("memory_temporary_disable_trigger_source", ""),
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
