import json
import logging
from typing import Dict, Any, List, Optional, Tuple
from openai import OpenAI, APITimeoutError

logger = logging.getLogger("law_assistant")


class LLMService:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg or {}

    def _clean_text(self, v: Any) -> str:
        s = str(v or "").strip()
        s = s.replace("`", "").replace("“", "").replace("”", "").replace("‘", "").replace("’", "")
        s = s.strip('"').strip("'").strip()
        return s

    def _get_llm_config(self) -> Dict[str, Any]:
        llm_cfg = self.cfg.get("llm_config") or {}
        if isinstance(llm_cfg, dict):
            return llm_cfg
        return {}

    def _build_base_url(self, base: str) -> str:
        if base.endswith("/chat/completions"):
            return base[: -len("/chat/completions")]
        if base.endswith("/"):
            return base[:-1]
        return base

    def _build_headers(self, api_key: Optional[str], extra_headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if extra_headers:
            headers.update({k: str(v) for k, v in extra_headers.items()})
        return headers

    def _mask_secret(self, v: str) -> str:
        s = str(v or "")
        if not s:
            return ""
        if len(s) <= 8:
            return "*" * len(s)
        return f"{s[:4]}***{s[-4:]}(len={len(s)})"

    def chat(self, messages: List[Dict[str, str]], overrides: Optional[Dict[str, Any]] = None) -> Tuple[str, Dict[str, Any]]:
        cfg = dict(self._get_llm_config())
        if overrides:
            cfg.update(overrides)
        api_base = self._clean_text(cfg.get("api_base", ""))
        api_key = self._clean_text(cfg.get("api_key", ""))
        model = self._clean_text(cfg.get("model", ""))
        temperature = float(cfg.get("temperature", 0.2))
        max_tokens = int(cfg.get("max_tokens", 2048))
        extra_headers = cfg.get("headers") if isinstance(cfg.get("headers"), dict) else None
        if not api_base or not model:
            raise RuntimeError("llm_config api_base or model missing")

        base_url = self._build_base_url(api_base)
        timeout = int(cfg.get("timeout", 60))
        retries = max(1, int(cfg.get("retries", 2)))
        logger.info(
            "llm_request_start api_base=%s base_url=%s model=%s temperature=%s max_tokens=%s timeout=%s retries=%s",
            api_base,
            base_url,
            model,
            temperature,
            max_tokens,
            timeout,
            retries
        )
        logger.debug(
            "llm_request_debug api_key=%s raw_api_base=%r headers=%s message_count=%s",
            self._mask_secret(api_key),
            cfg.get("api_base", ""),
            list((extra_headers or {}).keys()),
            len(messages)
        )
        client = OpenAI(
            api_key=api_key or None,
            base_url=base_url,
            timeout=timeout,
            max_retries=max(0, retries - 1),
            default_headers=extra_headers or None
        )
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens
            )
        except APITimeoutError as e:
            fallback_model = self._clean_text(cfg.get("fallback_model", ""))
            retry_timeout = max(timeout, int(cfg.get("timeout_retry", 240)))
            retry_max_tokens = max(256, min(max_tokens, int(cfg.get("max_tokens_retry", 1024))))
            retry_model = fallback_model or model
            logger.warning(
                "llm_request_timeout_retry base_url=%s model=%s retry_model=%s timeout=%s->%s max_tokens=%s->%s",
                base_url,
                model,
                retry_model,
                timeout,
                retry_timeout,
                max_tokens,
                retry_max_tokens,
            )
            retry_client = OpenAI(
                api_key=api_key or None,
                base_url=base_url,
                timeout=retry_timeout,
                max_retries=0,
                default_headers=extra_headers or None,
            )
            try:
                resp = retry_client.chat.completions.create(
                    model=retry_model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=retry_max_tokens,
                )
            except Exception as e2:
                logger.exception(
                    "llm_request_failed_after_retry api_base=%s base_url=%s model=%s retry_model=%s timeout=%s retry_timeout=%s raw_api_base=%r api_key=%s headers=%s",
                    api_base,
                    base_url,
                    model,
                    retry_model,
                    timeout,
                    retry_timeout,
                    cfg.get("api_base", ""),
                    self._mask_secret(api_key),
                    list((extra_headers or {}).keys()),
                )
                raise RuntimeError(f"llm request failed after timeout retry: {str(e2)}") from e2
        except Exception as e:
            logger.exception(
                "llm_request_failed api_base=%s base_url=%s model=%s timeout=%s raw_api_base=%r api_key=%s headers=%s",
                api_base,
                base_url,
                model,
                timeout,
                cfg.get("api_base", ""),
                self._mask_secret(api_key),
                list((extra_headers or {}).keys())
            )
            raise RuntimeError(f"llm request failed: {str(e)}") from e
        parsed = resp.model_dump()
        content = ""
        try:
            content = resp.choices[0].message.content or ""
        except Exception:
            content = json.dumps(parsed, ensure_ascii=False)
        return content, parsed
