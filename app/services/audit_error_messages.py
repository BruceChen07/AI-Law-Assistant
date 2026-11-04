"""Audit error classification and user-facing messages.

Shared by the HTTP layer (submit endpoints) and the background task runner so a
failed audit reports the same message whether it fails synchronously or later.
"""
from __future__ import annotations


def _is_zh(language: str) -> bool:
    return str(language or "zh").lower().startswith("zh")


def build_audit_error_detail(err: Exception, language: str) -> str:
    msg = str(err or "")
    low = msg.lower()
    is_zh = _is_zh(language)
    if "invalid_api_key" in low or "incorrect api key" in low or "authentication" in low or "401" in low:
        if is_zh:
            return "LLM 鉴权失败：API Key 无效或已过期。请前往【Admin -> 模型配置】更新 API Base、Model、API Key 后重试。"
        return "LLM authentication failed: API key is invalid or expired. Go to Admin -> Model Config and update API Base, Model, and API key, then retry."
    if "timeout" in low or "timed out" in low:
        if is_zh:
            return "LLM 请求超时。请在【Admin -> 模型配置】适当增大 timeout，或更换更稳定的模型后重试。"
        return "LLM request timed out. Increase timeout in Admin -> Model Config or switch to a more stable model, then retry."
    if "keyring" in low:
        if is_zh:
            return "本地安全密钥存储不可用。请安装 keyring 依赖或改用环境变量配置 LLM_API_KEY 后重试。"
        return "Local secure key storage is unavailable. Install keyring or use LLM_API_KEY environment variable, then retry."
    if "coverage_incomplete" in low or "max_rounds" in low or "coverage" in low:
        if is_zh:
            return "合同条款规模超出当前审计计划容量，审计在调用模型前已中止（未产生残缺报告）。请联系管理员调整审计分组/容量配置（audit_token_policy / audit_optimization）后重试。"
        return "The contract exceeds the current audit planning capacity; the audit stopped before calling the model (no partial report was produced). Ask an administrator to raise the audit capacity settings (audit_token_policy / audit_optimization) and retry."
    if "invalid_model_json" in low or "model_response_truncated" in low:
        if is_zh:
            return "模型返回的分批审计结果无效或被截断，该批次缺少可用结论。已保存的批次结果会保留，可在修正配置或模型后重试以复用已完成批次。"
        return "The model returned an invalid or truncated per-chunk result. Finished chunks are preserved and will be reused when you retry."
    if "ocr_parse_failed" in low or "ocr_failed" in low or "torchvision" in low:
        if is_zh:
            return "OCR 解析失败，未能正确抽取 PDF 文本，审计流程已中止。请检查 OCR 依赖（如 torchvision）和 MinerU 配置后重试。"
        return "OCR parsing failed and PDF text was not extracted correctly. Audit flow has been stopped. Check OCR dependencies (e.g., torchvision) and MinerU configuration, then retry."
    if is_zh:
        return "合同审计失败。请检查【Admin -> 模型配置】中的 LLM 参数后重试。"
    return "Contract audit failed. Check LLM settings in Admin -> Model Config and retry."


def audit_error_http_status(err: Exception) -> int:
    low = str(err or "").lower()
    if "coverage_incomplete" in low or "max_rounds" in low:
        return 422
    if "invalid_model_json" in low or "model_response_truncated" in low:
        return 502
    return 500
