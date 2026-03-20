import json
import logging
import os
import re
import time
from datetime import datetime
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

    def _estimate_input_tokens(self, messages: List[Dict[str, Any]]) -> int:
        parts: List[str] = []
        for m in messages or []:
            if not isinstance(m, dict):
                parts.append(str(m or ""))
                continue
            content = m.get("content", "")
            if isinstance(content, str):
                parts.append(content)
                continue
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict):
                        text = str(item.get("text") or item.get("content") or "")
                        if text:
                            parts.append(text)
                    else:
                        parts.append(str(item or ""))
                continue
            parts.append(str(content or ""))
        joined = "\n".join([x for x in parts if x])
        cjk = len(re.findall(r"[\u4e00-\u9fff]", joined))
        non_cjk = max(0, len(joined) - cjk)
        return max(1, int(cjk * 1.1 + non_cjk / 3.8)) if joined else 0

    def _trace_options(self) -> Dict[str, Any]:
        enabled = bool(self.cfg.get("llm_trace_enabled", False))
        trace_dir = str(self.cfg.get("llm_trace_dir") or "").strip()
        if not trace_dir:
            base = str(self.cfg.get("data_dir") or "").strip()
            trace_dir = os.path.join(base, "llm_traces") if base else os.path.abspath("llm_traces")
        max_chars = int(self.cfg.get("llm_trace_max_chars", 12000) or 12000)
        return {"enabled": enabled, "dir": os.path.abspath(trace_dir), "max_chars": max(1000, max_chars)}

    def _mask_text(self, text: str) -> str:
        s = str(text or "")
        s = re.sub(r"Bearer\s+[A-Za-z0-9\-\._]+", "Bearer ***", s, flags=re.IGNORECASE)
        s = re.sub(r"sk-[A-Za-z0-9_\-]{16,}", "sk-***", s)
        s = re.sub(r"\b1[3-9]\d{9}\b", "1**********", s)
        return s

    def _clip(self, text: Any, max_chars: int) -> str:
        s = str(text or "")
        if len(s) <= max_chars:
            return s
        return s[:max_chars] + f"...<truncated:{len(s)-max_chars}>"

    def _sanitize_messages(self, messages: List[Dict[str, Any]], max_chars: int) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for m in messages or []:
            if not isinstance(m, dict):
                out.append({"role": "unknown", "content": self._clip(self._mask_text(m), max_chars)})
                continue
            role = str(m.get("role") or "")
            content = m.get("content", "")
            if isinstance(content, list):
                content = json.dumps(content, ensure_ascii=False)
            out.append({"role": role, "content": self._clip(self._mask_text(content), max_chars)})
        return out

    def _write_trace(self, payload: Dict[str, Any]) -> None:
        opts = self._trace_options()
        if not opts["enabled"]:
            return
        day = datetime.utcnow().strftime("%Y-%m-%d")
        trace_dir = os.path.join(opts["dir"], day)
        os.makedirs(trace_dir, exist_ok=True)
        file_path = os.path.join(trace_dir, "llm_trace.jsonl")
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def chat(self, messages: List[Dict[str, str]], overrides: Optional[Dict[str, Any]] = None) -> Tuple[str, Dict[str, Any]]:
        cfg = dict(self._get_llm_config())
        trace_meta = {}
        if overrides:
            trace_meta = overrides.get("_trace_meta") if isinstance(overrides.get("_trace_meta"), dict) else {}
            cfg.update({k: v for k, v in overrides.items() if k != "_trace_meta"})
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
        input_tokens_est = self._estimate_input_tokens(messages)
        t0 = time.perf_counter()
        logger.info(
            "llm_request_start api_base=%s base_url=%s model=%s temperature=%s max_tokens=%s timeout=%s retries=%s input_tokens_est=%s message_count=%s",
            api_base,
            base_url,
            model,
            temperature,
            max_tokens,
            timeout,
            retries,
            input_tokens_est,
            len(messages)
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
            retry_max_tokens = max_tokens
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
            self._write_trace({
                "ts": datetime.utcnow().isoformat(),
                "ok": False,
                "model": model,
                "meta": trace_meta,
                "messages": self._sanitize_messages(messages, self._trace_options()["max_chars"]),
                "error": self._clip(self._mask_text(str(e)), self._trace_options()["max_chars"]),
            })
            raise RuntimeError(f"llm request failed: {str(e)}") from e
        parsed = resp.model_dump()
        usage = parsed.get("usage") if isinstance(parsed.get("usage"), dict) else {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
        latency_ms = int((time.perf_counter() - t0) * 1000)
        logger.info(
            "llm_request_done model=%s input_tokens_est=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s latency_ms=%s",
            model,
            input_tokens_est,
            prompt_tokens,
            completion_tokens,
            total_tokens,
            latency_ms
        )
        content = ""
        try:
            content = resp.choices[0].message.content or ""
        except Exception:
            content = json.dumps(parsed, ensure_ascii=False)
        self._write_trace({
            "ts": datetime.utcnow().isoformat(),
            "ok": True,
            "model": model,
            "meta": trace_meta,
            "latency_ms": latency_ms,
            "input_tokens_est": input_tokens_est,
            "usage": usage,
            "messages": self._sanitize_messages(messages, self._trace_options()["max_chars"]),
            "response": self._clip(self._mask_text(content), self._trace_options()["max_chars"]),
        })
        return content, parsed
