"""合同摘要（整份合同讲什么）生成。

输出契约与 feature/local-llm-llamacpp 保持一致，供前端「概览」直接渲染：

    {"title": "", "about": "", "core": "", "tax": "", "source_file": ""}

实现要点：
- 结果按 ``sha256(正文)+语言`` 缓存，重复打开不再调用模型；
- 正文过长时先分块抽取事实（map），再用事实生成摘要（reduce）；
- 模型返回非 JSON 或内容过短时，回退为正文截断，保证 UI 永远有内容可展示。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("law_assistant")

CONTRACT_SUMMARY_SOURCE_MAX_CHARS = 30000
CONTRACT_SUMMARY_MAX_TOKENS = 3000
CONTRACT_SUMMARY_RETRY_MAX_TOKENS = 4000
CONTRACT_SUMMARY_MAP_CHUNK_CHARS = 6000
CONTRACT_SUMMARY_MAP_MAX_CHUNKS = 24
CONTRACT_SUMMARY_MAP_MAX_TOKENS = 1200
CONTRACT_SUMMARY_MIN_SECTION_CHARS = 120
CONTRACT_SUMMARY_TARGET_MIN_CHARS = 600
CONTRACT_SUMMARY_TARGET_MAX_CHARS = 800
CONTRACT_SUMMARY_PROMPT_VERSION = "v1"
CONTRACT_SUMMARY_SINGLE_PASS_CHARS = 8000
CONTRACT_SUMMARY_CACHE_DIRNAME = "contract_summary"

_ZH_SYSTEM = "你是面向税务审核师的资深合同速读助手。只依据合同内容归纳，不得编造。"
_EN_SYSTEM = "You are a senior contract analyst writing a one-page executive summary for tax reviewers. Use only the contract content and never fabricate."

_ZH_TASK = """请仅依据下面给出的合同内容撰写一页式执行摘要，不得编造，不得引入合同之外的假设。

输出要求：只输出严格 JSON，且仅包含 title / about / core / tax 四个字段；不要输出 Markdown 代码块、注释或任何解释。

字段要求（合计约 {min_chars}-{max_chars} 字）：
- title：合同名称，不超过 40 字。
- about（合同在说什么，150-220 字）：先用一句话定位交易类型与双方角色，再按合同实际内容串联完整的合作机制链条（例如商品准入、质量、定价、返利与费用、对账结算、发票、退货、保证金、促销、违约与终止、知识产权、合规等）。
- core（核心安排，220-320 字）：用分号连接要点式长句，写清定价与含税口径、货款结算与可扣款项、开票与付款前置条件、暂停付款/抵销/退货等单方权利、保证金、违约与终止等关键商业安排。
- tax（税务审核提示，200-300 字）：先概括涉税条款的集中位置，再点出必须优先核对的税务口径（税率变化时的不含税价处理、不同纳税人及农产品业务的发票类型、税差补偿与计算公式、红冲与作废责任、企业所得税损失赔偿、跨法人扣款与开票主体一致性等），最后给出 2 到 4 条可直接执行的审核动作；不要展开法规条文分析。

写作规则：
1. 使用连贯书面语，不要使用列表符号、编号或换行。
2. 每段必须出现合同中的具体机制名词或数据，不要只写"双方约定了相关条款"。
3. 合同中未出现的机制不要臆测；确有必要说明缺失时写"未约定……"。
4. 只输出 JSON，字段名必须与要求完全一致。

合同上下文：
- 页数：{page_count}
- 条款数：{clause_count}
- 来源文件：{source_filename}
{facts_block}
合同正文：
{contract_text}"""

_EN_TASK = """Write a one-page executive summary using only the contract content below. Never fabricate and never add assumptions outside the contract.

Output: strict JSON only, with exactly the fields title / about / core / tax. Do not output source_file, Markdown fences, comments, or any explanation.

Field requirements (roughly {min_chars}-{max_chars} characters in total):
- title: contract name, at most 40 characters.
- about (150-220 chars): state the transaction type and the roles of both parties, then describe the cooperation chain actually present in the contract.
- core (220-320 chars): semicolon-separated key points covering pricing and tax treatment, settlement, invoicing preconditions, unilateral rights, deposit, breach and termination.
- tax (200-300 chars): summarize where the tax clauses concentrate, list the tax positions to verify first, and end with 2-4 review actions.

Rules:
1. Use flowing prose; no bullet symbols, numbering, or line breaks inside a field.
2. Every field must contain concrete mechanisms or figures from the contract.
3. Do not speculate about mechanisms absent from the contract; when absence matters, write "not agreed".
4. Output JSON only with exactly the required field names.

Contract context:
- Page count: {page_count}
- Clause count: {clause_count}
- Source file: {source_filename}
{facts_block}
Contract text:
{contract_text}"""

_ZH_MAP_TASK = """请仅依据给出的合同片段抽取结构化事实，不推测、不评价、不输出解释或修改建议。

输出严格 JSON，字段固定如下；没有内容的字段用空字符串或空数组，不要省略字段：
{{"parties": "", "product_or_service": "", "term": "", "pricing": "", "settlement": "", "invoicing": [], "tax": [], "rebates_and_fees": [], "returns_and_deposit": [], "promotion": [], "breach_and_termination": [], "ip_and_compliance": [], "other": []}}

要求：
1. 保留原文中的主体名称、比例、税率、金额、期限与触发条件。
2. 每条事实写成独立、可单独引用的短句；数组内不要重复。
3. 不要给出修改建议，不要输出 JSON 之外的文字。

合同片段 {index}/{total}：
{chunk}"""

_EN_MAP_TASK = """Extract structured facts from the contract chunk only. Do not speculate, evaluate, give explanations, or suggest amendments.

Return strict JSON with exactly these fields; use an empty string or empty array when a field has no content, and never omit a field:
{{"parties": "", "product_or_service": "", "term": "", "pricing": "", "settlement": "", "invoicing": [], "tax": [], "rebates_and_fees": [], "returns_and_deposit": [], "promotion": [], "breach_and_termination": [], "ip_and_compliance": [], "other": []}}

Rules:
1. Keep entity names, ratios, tax rates, amounts, deadlines and triggering conditions from the source.
2. Write each fact as a self-contained short sentence; do not duplicate items in an array.
3. Do not suggest amendments and do not output anything outside the JSON.

Contract chunk {index}/{total}:
{chunk}"""


def _norm_lang(lang: str) -> str:
    return "en" if str(lang or "").strip().lower().startswith("en") else "zh"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cache_root(cfg: Dict[str, Any]) -> str:
    data_dir = str(cfg.get("data_dir") or "data")
    return os.path.join(data_dir, "memory", CONTRACT_SUMMARY_CACHE_DIRNAME)


def _cache_path(cfg: Dict[str, Any], digest: str, lang: str) -> str:
    return os.path.join(_cache_root(cfg), f"{digest}_{lang}.json")


def _read_cache(path: str) -> Optional[Dict[str, Any]]:
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and data.get("title"):
            return data
    except (OSError, ValueError):
        logger.warning("contract_summary_cache_unreadable path=%s", path)
    return None


def _write_cache(path: str, payload: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError:
        logger.warning("contract_summary_cache_write_failed path=%s", path)


def _clip(text: str, limit: int) -> str:
    value = str(text or "").strip()
    return value if len(value) <= limit else value[:limit].rstrip() + "……"


def _parse_json_object(raw_text: str) -> Dict[str, Any]:
    text = str(raw_text or "").strip()
    if not text:
        return {}
    text = re.sub(r"^\s*```(?:json)?\s*", "", text, count=1, flags=re.IGNORECASE)
    text = re.sub(r"\s*```\s*$", "", text, count=1)
    for candidate in (text, text[text.find("{"): text.rfind("}") + 1]):
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _normalize_payload(parsed: Dict[str, Any], lang: str) -> Dict[str, str]:
    def pick(*keys: str) -> str:
        for key in keys:
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, list):
                joined = "；".join(
                    str(item).strip() for item in value if str(item).strip())
                if joined:
                    return joined
        return ""

    return {
        "title": pick("title", "name", "contract_name"),
        "about": pick("about", "overview", "summary", "what"),
        "core": pick("core", "key_points", "arrangement", "core_arrangement"),
        "tax": pick("tax", "tax_note", "tax_review"),
    }


def _chunk_text(text: str, size: int) -> List[str]:
    value = str(text or "").strip()
    if not value:
        return []
    size = max(2000, int(size or CONTRACT_SUMMARY_MAP_CHUNK_CHARS))
    chunks: List[str] = []
    start = 0
    while start < len(value) and len(chunks) < CONTRACT_SUMMARY_MAP_MAX_CHUNKS:
        end = min(len(value), start + size)
        if end < len(value):
            boundary = value.rfind("\n", start + size // 2, end)
            if boundary > start:
                end = boundary
        chunks.append(value[start:end].strip())
        start = end
    return [chunk for chunk in chunks if chunk]


def _chat(llm, messages: List[Dict[str, str]], max_tokens: int) -> Tuple[str, Dict[str, Any]]:
    overrides = {
        "max_tokens": max_tokens,
        "enable_thinking": False,
        "reasoning_effort": "low",
        "thinking_budget_tokens": 0,
        "_task_profile": "contract_summary",
    }
    try:
        return llm.chat(messages, overrides=overrides)
    except TypeError:
        return llm.chat(messages)


def _map_chunk_facts(cfg: Dict[str, Any], llm, chunk: str, index: int,
                     total: int, lang: str) -> Dict[str, Any]:
    template = _EN_MAP_TASK if lang == "en" else _ZH_MAP_TASK
    user = template.format(index=index, total=total, chunk=chunk)
    system = _EN_SYSTEM if lang == "en" else _ZH_SYSTEM
    try:
        raw_text, _raw = _chat(
            llm,
            [{"role": "system", "content": system},
             {"role": "user", "content": user}],
            CONTRACT_SUMMARY_MAP_MAX_TOKENS,
        )
    except Exception:
        logger.exception("contract_summary_map_failed index=%s", index)
        return {}
    return _parse_json_object(raw_text)


def _format_facts(facts: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for item in facts:
        if not isinstance(item, dict):
            continue
        parts: List[str] = []
        for key, value in item.items():
            if isinstance(value, list):
                text = "；".join(str(v).strip() for v in value if str(v).strip())
            else:
                text = str(value or "").strip()
            if text:
                parts.append(f"{key}: {text}")
        if parts:
            lines.append("；".join(parts))
    return "\n".join(lines)


def _empty_result(source_filename: str, enabled: bool, status: str,
                  error: str = "") -> Dict[str, Any]:
    return {
        "enabled": enabled,
        "title": "",
        "about": "",
        "core": "",
        "tax": "",
        "source_file": source_filename,
        "status": status,
        "source": "error" if status == "error" else "empty",
        "error": error,
        "cache_hit": False,
        "truncated": False,
        "source_chars": 0,
        "map_reduce": False,
        "map_chunks": 0,
        "prompt_version": CONTRACT_SUMMARY_PROMPT_VERSION,
        "generated_at": _now_iso(),
    }


def build_contract_summary(
    cfg: Dict[str, Any],
    llm,
    text: str,
    lang: str = "zh",
    *,
    page_count: int = 0,
    clause_count: int = 0,
    source_filename: str = "",
    document_id: str = "",
    source_sha256: str = "",
) -> Dict[str, Any]:
    """生成合同摘要；任何异常都返回可展示的降级结果，不向上抛。"""
    enabled = bool(cfg.get("contract_summary_enabled", True))
    norm_lang = _norm_lang(lang)
    source = str(text or "").strip()
    if not enabled:
        return _empty_result(source_filename, False, "disabled")
    if not source:
        return _empty_result(source_filename, True, "empty", "合同正文为空")

    digest = str(source_sha256 or "").strip() or hashlib.sha256(
        source.encode("utf-8")).hexdigest()
    cache_path = _cache_path(cfg, digest, norm_lang)
    cached = _read_cache(cache_path)
    if cached:
        result = dict(cached)
        result.update({"enabled": True, "cache_hit": True, "status": "ok"})
        return result

    source_text = _clip(source, CONTRACT_SUMMARY_SOURCE_MAX_CHARS)
    truncated = len(source_text) < len(source)
    chunks: List[str] = []
    facts_text = ""
    map_reduce = False
    if len(source_text) > CONTRACT_SUMMARY_SINGLE_PASS_CHARS:
        chunks = _chunk_text(source_text, CONTRACT_SUMMARY_MAP_CHUNK_CHARS)
        if len(chunks) > 1:
            map_reduce = True
            facts = [
                _map_chunk_facts(cfg, llm, chunk, idx, len(chunks), norm_lang)
                for idx, chunk in enumerate(chunks, start=1)
            ]
            facts_text = _format_facts(facts)

    template = _EN_TASK if norm_lang == "en" else _ZH_TASK
    prompt_text = source_text if not map_reduce else _clip(
        source_text, CONTRACT_SUMMARY_SINGLE_PASS_CHARS)
    facts_block = (
        f"已抽取的合同要点：\n{facts_text}\n" if facts_text else "")
    user = template.format(
        min_chars=CONTRACT_SUMMARY_TARGET_MIN_CHARS,
        max_chars=CONTRACT_SUMMARY_TARGET_MAX_CHARS,
        page_count=int(page_count or 0),
        clause_count=int(clause_count or 0),
        source_filename=source_filename or "-",
        facts_block=facts_block,
        contract_text=prompt_text,
    )
    system = _EN_SYSTEM if norm_lang == "en" else _ZH_SYSTEM

    payload = _empty_result(source_filename, True, "error")
    parsed: Dict[str, str] = {}
    error = ""
    for attempt, budget in enumerate((
            CONTRACT_SUMMARY_MAX_TOKENS, CONTRACT_SUMMARY_RETRY_MAX_TOKENS)):
        try:
            raw_text, _raw = _chat(
                llm,
                [{"role": "system", "content": system},
                 {"role": "user", "content": user}],
                budget,
            )
        except Exception as exc:
            error = str(exc)
            logger.exception("contract_summary_request_failed attempt=%s", attempt + 1)
            continue
        parsed = _normalize_payload(_parse_json_object(raw_text), norm_lang)
        if parsed.get("about") or parsed.get("core"):
            break

    total_chars = sum(len(value) for value in parsed.values())
    if total_chars >= CONTRACT_SUMMARY_MIN_SECTION_CHARS:
        payload.update(parsed)
        payload.update({
            "status": "ok",
            "source": "llm+map_reduce" if map_reduce else "llm",
            "error": "",
        })
    else:
        # 模型没给出可用结构：退化为正文首段，保证「概览」不为空。
        fallback = _clip(source_text, CONTRACT_SUMMARY_TARGET_MIN_CHARS)
        payload.update({
            "title": source_filename or "",
            "about": fallback,
            "core": "",
            "tax": "",
            "status": "fallback",
            "source": "source_text",
            "error": error or "summary_payload_too_short",
        })
    payload.update({
        "enabled": True,
        "cache_hit": False,
        "truncated": truncated,
        "source_chars": len(source),
        "map_reduce": map_reduce,
        "map_chunks": len(chunks),
        "prompt_version": CONTRACT_SUMMARY_PROMPT_VERSION,
        "generated_at": _now_iso(),
    })
    if payload.get("status") == "ok":
        _write_cache(cache_path, payload)
    logger.info(
        "contract_summary_done document_id=%s status=%s chars=%s map_reduce=%s cache=%s",
        document_id,
        payload.get("status"),
        sum(len(str(payload.get(k) or "")) for k in ("title", "about", "core", "tax")),
        map_reduce,
        payload.get("cache_hit"),
    )
    return payload
