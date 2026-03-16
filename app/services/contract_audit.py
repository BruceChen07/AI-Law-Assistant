import json
import logging
from typing import Dict, Any, Optional
from app.core.utils import extract_text_with_config

logger = logging.getLogger("law_assistant")


def _build_prompt(text: str, lang: str) -> Dict[str, str]:
    if lang.lower() == "en":
        system = "You are a contract audit assistant. Output strict JSON only."
        user = (
            "Analyze the contract and identify tax/compliance and legal risks. "
            "Return JSON with keys: summary, risks. "
            "risks is an array of objects with keys: level, type, issue, evidence, suggestion, law_reference. "
            "If none, return risks as empty array. "
            "Contract:\n" + text
        )
    else:
        system = "你是合同审计助手，仅输出严格JSON。"
        user = (
            "对合同进行财税与法律风险审计，输出JSON，包含字段：summary, risks。"
            "risks为数组，元素字段：level, type, issue, evidence, suggestion, law_reference。"
            "如无风险，risks为空数组。"
            "合同内容：\n" + text
        )
    return {
        "system": system,
        "user": user
    }


def audit_contract(cfg: Dict[str, Any], llm, file_path: str, lang: str = "zh") -> Dict[str, Any]:
    text, meta = extract_text_with_config(cfg, file_path)
    prompt = _build_prompt(text, lang)
    messages = [
        {"role": "system", "content": prompt["system"]},
        {"role": "user", "content": prompt["user"]}
    ]
    result_text, raw = llm.chat(messages)
    parsed = None
    try:
        parsed = json.loads(result_text)
    except Exception:
        parsed = {
            "summary": "",
            "risks": [],
            "raw": result_text
        }
    return {
        "audit": parsed,
        "meta": {
            "text_length": len(text),
            "ocr_used": meta.get("ocr_used"),
            "ocr_engine": meta.get("ocr_engine"),
            "page_count": meta.get("page_count"),
            "llm_model": (cfg.get("llm_config") or {}).get("model", "")
        },
        "raw": raw
    }
