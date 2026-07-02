import ipaddress
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
from urllib.parse import urlsplit

import httpx

from app.core.llm_router import resolve_llm_route
from app.core.secure_store import get_llm_api_key

logger = logging.getLogger("law_assistant")


class LLMService:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg or {}

    def _clean_text(self, v: Any) -> str:
        s = str(v or "").strip()
        s = s.replace("`", "").replace("“", "").replace(
            "”", "").replace("‘", "").replace("’", "")
        s = s.strip('"').strip("'").strip()
        return s

    def _get_llm_config(self) -> Dict[str, Any]:
        llm_cfg = self.cfg.get("llm_config") or {}
        if isinstance(llm_cfg, dict):
            return llm_cfg
        return {}

    def _resolve_chat_target(
        self, overrides: Optional[Dict[str, Any]] = None
    ) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        merged_overrides = dict(overrides or {})
        trace_meta = merged_overrides.get("_trace_meta") if isinstance(
            merged_overrides.get("_trace_meta"), dict) else {}
        task_profile = self._clean_text(
            merged_overrides.pop("_task_profile", "")
            or merged_overrides.pop("task_profile", "")
        ) or "default"
        model_role = self._clean_text(
            merged_overrides.pop("_model_role", "")
            or merged_overrides.pop("model_role", "")
        )
        cfg, route_meta = resolve_llm_route(
            self.cfg,
            task_profile=task_profile,
            model_role=model_role,
        )
        visible_overrides = {
            k: v for k, v in merged_overrides.items()
            if not str(k).startswith("_")
        }
        cfg.update(visible_overrides)
        route_trace = dict(route_meta)
        route_trace["task_profile"] = task_profile
        merged_trace_meta = dict(trace_meta)
        merged_trace_meta["llm_route"] = route_trace
        return cfg, route_meta, merged_trace_meta

    def _resolve_api_key(self, cfg: Dict[str, Any]) -> str:
        key = self._clean_text(cfg.get("api_key", ""))
        if key:
            return key
        secure_key = self._clean_text(get_llm_api_key(self.cfg))
        if secure_key:
            return secure_key
        return self._clean_text(os.environ.get("LLM_API_KEY", ""))

    def _build_base_url(self, base: str) -> str:
        s = re.sub(r"\s+", "", self._clean_text(base))
        if not s:
            return ""
        if not re.match(r"^https?://", s, flags=re.IGNORECASE):
            s = f"https://{s}"
        if s.endswith("/chat/completions"):
            s = s[: -len("/chat/completions")]
        if s.endswith("/"):
            s = s[:-1]
        p = urlsplit(s)
        if not p.scheme or not p.netloc:
            raise RuntimeError("llm_config api_base invalid")
        return f"{p.scheme}://{p.netloc}{p.path}".rstrip("/")

    def _build_ollama_base_url(self, base: str) -> str:
        s = self._build_base_url(base)
        for suffix in ("/chat/completions", "/api/chat", "/api/generate", "/v1"):
            if s.endswith(suffix):
                s = s[: -len(suffix)]
        return s.rstrip("/")

    def _network_policy(self) -> Dict[str, Any]:
        raw = self.cfg.get("network_policy")
        return dict(raw) if isinstance(raw, dict) else {}

    def _offline_mode_enabled(self) -> bool:
        policy = self._network_policy()
        return bool(policy.get("enabled", True)) and str(
            policy.get("mode", "offline_strict")
        ).strip().lower() == "offline_strict"

    def _is_allowed_host(self, host: str) -> bool:
        normalized = self._clean_text(host).lower()
        if not normalized:
            return False
        policy = self._network_policy()
        allowed_hosts = {
            str(x).strip().lower()
            for x in (policy.get("allowed_hosts") or [])
            if str(x).strip()
        }
        allowed_suffixes = [
            str(x).strip().lower()
            for x in (policy.get("allowed_domain_suffixes") or [])
            if str(x).strip()
        ]
        if normalized in allowed_hosts or normalized in {"127.0.0.1", "localhost", "::1"}:
            return True
        if any(normalized.endswith(suffix) for suffix in allowed_suffixes):
            return True
        try:
            ip = ipaddress.ip_address(normalized)
            if ip.is_loopback:
                return True
            return bool(policy.get("allow_private_ip_ranges", True)) and (
                ip.is_private or ip.is_link_local
            )
        except ValueError:
            # Hostnames without public suffixes are commonly used in intranet DNS.
            if "." not in normalized:
                return True
        return False

    def _enforce_network_policy(self, base_url: str) -> None:
        if not self._offline_mode_enabled():
            return
        host = urlsplit(base_url).hostname or ""
        if not self._is_allowed_host(host):
            raise RuntimeError(
                f"network policy blocked non-intranet LLM endpoint: {host or '<empty>'}"
            )

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
                        text = str(item.get("text")
                                   or item.get("content") or "")
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
            trace_dir = os.path.join(
                base, "llm_traces") if base else os.path.abspath("llm_traces")
        max_chars = int(self.cfg.get("llm_trace_max_chars", 12000) or 12000)
        return {"enabled": enabled, "dir": os.path.abspath(trace_dir), "max_chars": max(1000, max_chars)}

    def _mask_text(self, text: str) -> str:
        s = str(text or "")
        s = re.sub(r"Bearer\s+[A-Za-z0-9\-\._]+",
                   "Bearer ***", s, flags=re.IGNORECASE)
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
                out.append({"role": "unknown", "content": self._clip(
                    self._mask_text(m), max_chars)})
                continue
            role = str(m.get("role") or "")
            content = m.get("content", "")
            if isinstance(content, list):
                content = json.dumps(content, ensure_ascii=False)
            out.append({"role": role, "content": self._clip(
                self._mask_text(content), max_chars)})
        return out

    def _write_trace(self, payload: Dict[str, Any]) -> None:
        opts = self._trace_options()
        if not opts["enabled"]:
            return
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        trace_dir = os.path.join(opts["dir"], day)
        os.makedirs(trace_dir, exist_ok=True)
        file_path = os.path.join(trace_dir, "llm_trace.jsonl")
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _build_chat_kwargs(self, model: str, messages: List[Dict[str, str]], temperature: float, max_tokens: int, cfg: Dict[str, Any]) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        extra_body: Dict[str, Any] = {}
        if "enable_thinking" in cfg:
            extra_body["enable_thinking"] = bool(cfg.get("enable_thinking"))
        thinking_budget = cfg.get("thinking_budget_tokens")
        if thinking_budget is not None:
            try:
                extra_body["thinking_budget_tokens"] = max(
                    0, int(thinking_budget))
            except (TypeError, ValueError) as e:
                logger.warning(
                    "llm_invalid_thinking_budget_tokens value=%s err=%s",
                    str(thinking_budget),
                    str(e),
                )
        reasoning_effort = str(
            cfg.get("reasoning_effort") or "").strip().lower()
        if reasoning_effort in {"low", "medium", "high"}:
            kwargs["reasoning_effort"] = reasoning_effort
        if extra_body:
            kwargs["extra_body"] = extra_body
        return kwargs

    def _build_openai_compatible_body(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
        cfg: Dict[str, Any],
    ) -> Dict[str, Any]:
        body = self._build_chat_kwargs(
            model, messages, temperature, max_tokens, cfg)
        extra_body = body.pop("extra_body", None)
        if isinstance(extra_body, dict):
            body.update(extra_body)
        return body

    def _post_json(
        self,
        url: str,
        body: Dict[str, Any],
        headers: Dict[str, str],
        timeout: int,
    ) -> Dict[str, Any]:
        response = httpx.post(url, json=body, headers=headers, timeout=timeout)
        response.raise_for_status()
        return response.json()

    def _build_ollama_chat_body(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
        cfg: Dict[str, Any],
    ) -> Dict[str, Any]:
        options: Dict[str, Any] = {
            "temperature": temperature,
            "num_predict": max_tokens,
        }
        if cfg.get("num_ctx") not in {None, ""}:
            try:
                options["num_ctx"] = int(cfg.get("num_ctx"))
            except (TypeError, ValueError):
                logger.warning("ollama_invalid_num_ctx value=%s",
                               cfg.get("num_ctx"))
        if cfg.get("top_p") not in {None, ""}:
            try:
                options["top_p"] = float(cfg.get("top_p"))
            except (TypeError, ValueError):
                logger.warning("ollama_invalid_top_p value=%s",
                               cfg.get("top_p"))
        body: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": options,
        }
        body["think"] = bool(cfg.get("enable_thinking", False))
        keep_alive = cfg.get("keep_alive")
        if keep_alive not in {None, ""}:
            body["keep_alive"] = keep_alive
        return body

    def _post_ollama_chat(
        self,
        url: str,
        body: Dict[str, Any],
        headers: Dict[str, str],
        timeout: int,
    ) -> Dict[str, Any]:
        return self._post_json(url, body, headers, timeout)

    def _extract_http_error_text(self, err: httpx.HTTPStatusError) -> str:
        response = getattr(err, "response", None)
        if response is None:
            return ""
        try:
            payload = response.json()
            if isinstance(payload, dict):
                return str(payload.get("error") or payload.get("message") or "")
            return str(payload or "")
        except Exception:
            return str(getattr(response, "text", "") or "")

    def _chat_via_ollama(
        self,
        api_base: str,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
        timeout: int,
        cfg: Dict[str, Any],
        extra_headers: Optional[Dict[str, str]],
    ) -> Tuple[str, Dict[str, Any]]:
        base_url = self._build_ollama_base_url(api_base)
        request_url = f"{base_url}/api/chat"
        request_headers = self._build_headers(None, extra_headers)
        request_body = self._build_ollama_chat_body(
            model, messages, temperature, max_tokens, cfg
        )
        try:
            raw = self._post_ollama_chat(
                request_url, request_body, request_headers, timeout
            )
        except httpx.HTTPStatusError as e:
            err_text = self._extract_http_error_text(e).lower()
            if e.response is not None and e.response.status_code == 404 and "model" in err_text and "not found" in err_text:
                raise RuntimeError(f"ollama model not found: {model}") from e
            raise
        except httpx.ReadError as e:
            logger.warning(
                "ollama_request_read_retry url=%s model=%s timeout=%s err=%s",
                request_url,
                model,
                timeout,
                str(e),
            )
            time.sleep(0.2)
            try:
                raw = self._post_ollama_chat(
                    request_url, request_body, request_headers, timeout
                )
            except Exception as e2:
                raise RuntimeError(
                    f"ollama request failed after read retry: {str(e2)}"
                ) from e2
        except httpx.TimeoutException:
            # ---- first retry: extended timeout + reduced max_tokens ----
            retry_timeout = max(timeout, int(cfg.get("timeout_retry", 900)))
            retry_cfg = dict(cfg)
            retry_max_tokens = max(220, min(max_tokens, int(max_tokens * 0.6)))
            retry_body = self._build_ollama_chat_body(
                model, messages, temperature, retry_max_tokens, retry_cfg
            )
            logger.warning(
                "ollama_request_timeout_retry url=%s model=%s timeout=%s->%s max_tokens=%s->%s",
                request_url,
                model,
                timeout,
                retry_timeout,
                max_tokens,
                retry_max_tokens,
            )
            try:
                raw = self._post_ollama_chat(
                    request_url, retry_body, request_headers, retry_timeout
                )
            except httpx.TimeoutException:
                # ---- last-chance retry: full timeout + aggressive reduction ----
                last_timeout = retry_timeout
                last_max_tokens = max(64, int(max_tokens * 0.3))
                last_body = self._build_ollama_chat_body(
                    model, messages, temperature, last_max_tokens, dict(cfg)
                )
                logger.warning(
                    "ollama_request_timeout_last_chance url=%s model=%s timeout=%s max_tokens=%s->%s",
                    request_url,
                    model,
                    last_timeout,
                    max_tokens,
                    last_max_tokens,
                )
                raw = self._post_ollama_chat(
                    request_url, last_body, request_headers, last_timeout
                )
            except Exception as e2:
                raise RuntimeError(
                    f"ollama request failed after timeout retry: {str(e2)}"
                ) from e2
        message = raw.get("message") if isinstance(
            raw.get("message"), dict) else {}
        content = str(message.get("content") or "")
        prompt_tokens = int(raw.get("prompt_eval_count") or 0)
        completion_tokens = int(raw.get("eval_count") or 0)
        parsed = {
            "model": raw.get("model") or model,
            "done": bool(raw.get("done", True)),
            "done_reason": raw.get("done_reason") or "",
            "message": {
                "role": message.get("role") or "assistant",
                "content": content,
            },
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
            "ollama_raw": raw,
            "choices": [
                {
                    "message": {
                        "role": message.get("role") or "assistant",
                        "content": content,
                    }
                }
            ],
        }
        return content, parsed

    def _chat_via_openai_compatible(
        self,
        api_base: str,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
        timeout: int,
        cfg: Dict[str, Any],
        api_key: str,
        extra_headers: Optional[Dict[str, str]],
    ) -> Tuple[str, Dict[str, Any]]:
        base_url = self._build_base_url(api_base)
        self._enforce_network_policy(base_url)
        request_url = f"{base_url}/chat/completions"
        request_headers = self._build_headers(api_key, extra_headers)
        request_body = self._build_openai_compatible_body(
            model, messages, temperature, max_tokens, cfg
        )
        try:
            raw = self._post_json(request_url, request_body,
                                  request_headers, timeout)
        except httpx.TimeoutException as e:
            retry_timeout = max(timeout, int(cfg.get("timeout_retry", 240)))
            retry_max_tokens = max(220, min(max_tokens, int(max_tokens * 0.6)))
            retry_body = self._build_openai_compatible_body(
                model, messages, temperature, retry_max_tokens, cfg
            )
            logger.warning(
                "openai_compatible_timeout_retry url=%s model=%s timeout=%s->%s max_tokens=%s->%s",
                request_url,
                model,
                timeout,
                retry_timeout,
                max_tokens,
                retry_max_tokens,
            )
            try:
                raw = self._post_json(
                    request_url, retry_body, request_headers, retry_timeout
                )
            except Exception as e2:
                raise RuntimeError(
                    f"openai-compatible request failed after timeout retry: {str(e2)}"
                ) from e2
        choices = raw.get("choices") if isinstance(
            raw.get("choices"), list) else []
        first_choice = choices[0] if choices else {}
        message = first_choice.get("message") if isinstance(
            first_choice.get("message"), dict
        ) else {}
        content = message.get("content", "")
        if isinstance(content, list):
            content = json.dumps(content, ensure_ascii=False)
        parsed = dict(raw)
        if not isinstance(parsed.get("usage"), dict):
            parsed["usage"] = {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            }
        return str(content or ""), parsed

    def chat(self, messages: List[Dict[str, str]], overrides: Optional[Dict[str, Any]] = None) -> Tuple[str, Dict[str, Any]]:
        cfg, route_meta, trace_meta = self._resolve_chat_target(overrides)
        api_base_raw = cfg.get("api_base", "")
        api_base = self._build_base_url(str(api_base_raw or ""))
        provider = self._clean_text(
            cfg.get("provider", "openai_compatible")).lower()
        api_key = "" if provider == "ollama" else self._resolve_api_key(cfg)
        model = self._clean_text(cfg.get("model", ""))
        temperature = float(cfg.get("temperature", 0.2))
        max_tokens = int(cfg.get("max_tokens", 2048))
        extra_headers = cfg.get("headers") if isinstance(
            cfg.get("headers"), dict) else None
        if not api_base or not model:
            raise RuntimeError("llm_config api_base or model missing")

        base_url = api_base
        self._enforce_network_policy(base_url)
        timeout = int(cfg.get("timeout", 60))
        input_tokens_est = self._estimate_input_tokens(messages)
        t0 = time.perf_counter()
        logger.info(
            "llm_request_start api_base=%s base_url=%s model=%s selected_role=%s task_profile=%s temperature=%s max_tokens=%s timeout=%s input_tokens_est=%s message_count=%s",
            api_base,
            base_url,
            model,
            route_meta.get("selected_role", ""),
            route_meta.get("task_profile", "default"),
            temperature,
            max_tokens,
            timeout,
            input_tokens_est,
            len(messages)
        )
        logger.debug(
            "llm_request_debug provider=%s api_key=%s raw_api_base=%r headers=%s message_count=%s",
            provider,
            self._mask_secret(api_key),
            cfg.get("api_base", ""),
            list((extra_headers or {}).keys()),
            len(messages)
        )
        try:
            if provider == "ollama":
                content, parsed = self._chat_via_ollama(
                    api_base,
                    model,
                    messages,
                    temperature,
                    max_tokens,
                    timeout,
                    cfg,
                    extra_headers,
                )
            else:
                content, parsed = self._chat_via_openai_compatible(
                    api_base,
                    model,
                    messages,
                    temperature,
                    max_tokens,
                    timeout,
                    cfg,
                    api_key,
                    extra_headers,
                )
        except Exception as e:
            logger.exception(
                "llm_request_failed provider=%s api_base=%s base_url=%s model=%s timeout=%s raw_api_base=%r api_key=%s headers=%s",
                provider,
                api_base,
                base_url,
                model,
                timeout,
                cfg.get("api_base", ""),
                self._mask_secret(api_key),
                list((extra_headers or {}).keys())
            )
            dns_hint = ""
            low_err = str(e).lower()
            if "getaddrinfo failed" in low_err or "name or service not known" in low_err:
                host = urlsplit(base_url).netloc
                dns_hint = f" (dns resolve failed for host: {host})"
            self._write_trace({
                "ts": datetime.now(timezone.utc).isoformat(),
                "ok": False,
                "model": model,
                "meta": trace_meta,
                "route": route_meta,
                "messages": self._sanitize_messages(messages, self._trace_options()["max_chars"]),
                "error": self._clip(self._mask_text(str(e)), self._trace_options()["max_chars"]),
            })
            raise RuntimeError(
                f"llm request failed: {str(e)}{dns_hint}") from e
        usage = parsed.get("usage") if isinstance(
            parsed.get("usage"), dict) else {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or (
            prompt_tokens + completion_tokens))
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
        self._write_trace({
            "ts": datetime.now(timezone.utc).isoformat(),
            "ok": True,
            "model": model,
            "meta": trace_meta,
            "route": route_meta,
            "latency_ms": latency_ms,
            "input_tokens_est": input_tokens_est,
            "usage": usage,
            "messages": self._sanitize_messages(messages, self._trace_options()["max_chars"]),
            "response": self._clip(self._mask_text(content), self._trace_options()["max_chars"]),
        })
        parsed["_route"] = route_meta
        return content, parsed

    def chat_with_profile(
        self,
        messages: List[Dict[str, str]],
        task_profile: str,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        next_overrides = dict(overrides or {})
        next_overrides["_task_profile"] = task_profile
        return self.chat(messages, overrides=next_overrides)
