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

from app.core.token_utils import estimate_messages_tokens
from app.core.llm_router import resolve_llm_route
from app.core.llm_trace import LlmTraceCollector, get_trace_collector
from app.core.secure_store import get_llm_api_key

logger = logging.getLogger("law_assistant")


class LLMService:
    # Cache for auto-detected model names, keyed by api_base
    _model_cache: Dict[str, str] = {}

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

    def _provider_uses_local_runtime(self, provider: str) -> bool:
        return self._clean_text(provider).lower() in {"ollama", "llama_cpp"}

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

    def _resolve_model_name(self, api_base: str, provider: str, configured_model: str) -> str:
        """Resolve model name. If configured as 'auto', detect from server.

        For local providers (llama_cpp, ollama), the model field in the
        request is ignored by the server – it uses whatever model is loaded.
        We query /v1/models to get the actual model name for display/trace.
        """
        if configured_model.lower() != "auto":
            return configured_model
        # Check cache first
        cached = LLMService._model_cache.get(api_base)
        if cached:
            return cached
        # Try to detect from server
        detected = self._detect_model_from_server(api_base, provider)
        if detected:
            LLMService._model_cache[api_base] = detected
            logger.info("model_auto_detected api_base=%s model=%s", api_base, detected)
            return detected
        # Detection failed – use "auto" as placeholder; will be
        # updated from response after first successful call
        logger.warning("model_auto_detect_failed api_base=%s using placeholder", api_base)
        return configured_model

    def _detect_model_from_server(self, api_base: str, provider: str) -> str:
        """Query the LLM server's /v1/models endpoint to get the actual model name."""
        try:
            base_url = self._build_base_url(api_base)
            # For ollama, use /api/tags; for openai_compatible/llama_cpp, use /v1/models
            if provider == "ollama":
                ollama_base = self._build_ollama_base_url(api_base)
                url = f"{ollama_base}/api/tags"
            else:
                url = f"{base_url}/models"
            self._enforce_network_policy(url)
            resp = httpx.get(url, timeout=5)
            resp.raise_for_status()
            data = resp.json()
            if provider == "ollama":
                models = data.get("models") or []
                if models:
                    name = str(models[0].get("name") or models[0].get("model") or "").strip()
                    if name:
                        return name
            else:
                models = data.get("data") or data.get("models") or []
                if models and isinstance(models, list):
                    name = str(models[0].get("id") or models[0].get("model") or "").strip()
                    if name:
                        return name
        except Exception as e:
            logger.debug("model_detect_error api_base=%s err=%s", api_base, str(e))
        return ""

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
        return estimate_messages_tokens(messages)

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
            enable_thinking = bool(cfg.get("enable_thinking"))
            extra_body["enable_thinking"] = enable_thinking
            if not enable_thinking:
                # Qwen3 / llama.cpp: 同时通过 chat_template_kwargs 抑制思考模式
                # 仅传 enable_thinking 顶层参数时，llama.cpp 可能忽略（非标准参数）
                extra_body["chat_template_kwargs"] = {"enable_thinking": False}
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
        trace_span=None,
        trace_collector: Optional[LlmTraceCollector] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        base_url = self._build_ollama_base_url(api_base)
        request_url = f"{base_url}/api/chat"
        request_headers = self._build_headers(None, extra_headers)
        request_body = self._build_ollama_chat_body(
            model, messages, temperature, max_tokens, cfg
        )

        def _try_ollama_request(_url: str, _body: Dict, _hdr: Dict, _to: int, _desc: str = "") -> Dict:
            nonlocal trace_span, trace_collector
            if trace_span and trace_collector:
                trace_collector.record_thinking(trace_span)
            return self._post_ollama_chat(_url, _body, _hdr, _to)

        try:
            raw = _try_ollama_request(
                request_url, request_body, request_headers, timeout, "primary")
        except httpx.HTTPStatusError as e:
            err_text = self._extract_http_error_text(e).lower()
            if e.response is not None and e.response.status_code == 404 and "model" in err_text and "not found" in err_text:
                if trace_span and trace_collector:
                    trace_collector.record_error(
                        trace_span, error_type="model_not_found",
                        error_message=f"ollama model not found: {model}")
                raise RuntimeError(f"ollama model not found: {model}") from e
            if trace_span and trace_collector:
                trace_collector.record_error(
                    trace_span, error_type="http_error",
                    error_message=str(e))
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
                raw = _try_ollama_request(
                    request_url, request_body, request_headers, timeout, "read_retry")
            except Exception as e2:
                if trace_span and trace_collector:
                    trace_collector.record_error(
                        trace_span, error_type="read_error_retry_failed",
                        error_message=str(e2))
                raise RuntimeError(
                    f"ollama request failed after read retry: {str(e2)}"
                ) from e2
        except httpx.TimeoutException:
            # ---- first retry: extended timeout + reduced max_tokens ----
            if trace_span and trace_collector:
                trace_collector.record_error(
                    trace_span, error_type="timeout_retry_1",
                    error_message=f"timeout after {timeout}s, retrying with extended timeout")
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
                raw = _try_ollama_request(
                    request_url, retry_body, request_headers, retry_timeout, "retry_1")
            except httpx.TimeoutException:
                # ---- last-chance retry: full timeout + aggressive reduction ----
                if trace_span and trace_collector:
                    trace_collector.record_error(
                        trace_span, error_type="timeout_retry_2",
                        error_message=f"timeout again after {retry_timeout}s, last-chance retry with reduced tokens")
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
                raw = _try_ollama_request(
                    request_url, last_body, request_headers, last_timeout, "retry_2")
            except Exception as e2:
                if trace_span and trace_collector:
                    trace_collector.record_error(
                        trace_span, error_type="timeout_retry_failed",
                        error_message=str(e2))
                raise RuntimeError(
                    f"ollama request failed after timeout retry: {str(e2)}"
                ) from e2
        message = raw.get("message") if isinstance(
            raw.get("message"), dict) else {}
        content = str(message.get("content") or "")
        # 捕获思考内容（部分模型如 deepseek-r1 会返回 reasoning_content）
        thinking_content = str(message.get(
            "reasoning_content") or message.get("thinking") or "")
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
        # ---- 记录 Trace 响应 ----
        if trace_span and trace_collector:
            if thinking_content:
                trace_collector.record_thinking(
                    trace_span, thinking_content=thinking_content)
            trace_collector.record_response(
                trace_span,
                content=content,
                usage=parsed["usage"],
                ollama_raw=raw,
            )
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
        reasoning_content = message.get("reasoning_content", "")
        if isinstance(reasoning_content, list):
            reasoning_content = json.dumps(
                reasoning_content, ensure_ascii=False)
        provider_name = self._clean_text(
            cfg.get("provider", "openai_compatible")).lower()
        used_reasoning_fallback = False
        thinking_leaked = False
        # 检查调用方是否显式禁用了思考模式
        thinking_disabled = "enable_thinking" in cfg and not bool(cfg.get("enable_thinking"))
        if provider_name == "llama_cpp" and not str(content or "").strip() and str(reasoning_content or "").strip():
            if thinking_disabled:
                # enable_thinking=False 但模型仍输出了 reasoning_content → 思考模式泄漏
                # 此时 reasoning_content 是思维链草稿，不是有效响应，不能作为 content
                thinking_leaked = True
                logger.warning(
                    "llama_cpp_thinking_leaked_rejected model=%s api_base=%s reasoning_chars=%s",
                    model,
                    api_base,
                    len(reasoning_content),
                )
            else:
                # Some llama.cpp + reasoning-capable templates return the full model output in
                # `reasoning_content` while leaving `content` empty. Fallback here so the
                # application does not treat a successful inference as a blank response.
                content = reasoning_content
                used_reasoning_fallback = True
                logger.warning(
                    "llama_cpp_reasoning_content_fallback model=%s api_base=%s",
                    model,
                    api_base,
                )
        # 检测思维链泄漏到 content 字段（即使不是通过 reasoning_content 回退）
        _THINKING_PREFIX_PATTERNS = (
            "here's a thinking", "here is a thinking", "let me think",
            "thinking process", "my thought process", "i'll think through",
            "step 1:", "**analysis**", "<think>", "let me analyze",
        )
        content_stripped = str(content or "").strip().lower()
        if content_stripped and any(content_stripped.startswith(p) for p in _THINKING_PREFIX_PATTERNS):
            thinking_leaked = True
            logger.warning(
                "llm_thinking_chain_in_content model=%s api_base=%s content_head=%s",
                model,
                api_base,
                content_stripped[:120],
            )
        parsed = dict(raw)
        if not isinstance(parsed.get("usage"), dict):
            parsed["usage"] = {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            }
        if choices:
            first_parsed_choice = parsed.setdefault("choices", [{}])[0]
            if not isinstance(first_parsed_choice, dict):
                first_parsed_choice = {}
                parsed["choices"][0] = first_parsed_choice
            first_parsed_message = first_parsed_choice.get("message")
            if not isinstance(first_parsed_message, dict):
                first_parsed_message = {}
                first_parsed_choice["message"] = first_parsed_message
            if reasoning_content:
                first_parsed_message["reasoning_content"] = str(
                    reasoning_content)
            first_parsed_message["content"] = str(content or "")
        if used_reasoning_fallback:
            parsed["_used_reasoning_content_fallback"] = True
        return str(content or ""), parsed

    def chat(self, messages: List[Dict[str, str]], overrides: Optional[Dict[str, Any]] = None) -> Tuple[str, Dict[str, Any]]:
        cfg, route_meta, trace_meta = self._resolve_chat_target(overrides)
        api_base_raw = cfg.get("api_base", "")
        api_base = self._build_base_url(str(api_base_raw or ""))
        provider = self._clean_text(
            cfg.get("provider", "openai_compatible")).lower()
        api_key = "" if self._provider_uses_local_runtime(
            provider) else self._resolve_api_key(cfg)
        model = self._clean_text(cfg.get("model", ""))
        temperature = float(cfg.get("temperature", 0.2))
        max_tokens = int(cfg.get("max_tokens", 2048))
        extra_headers = cfg.get("headers") if isinstance(
            cfg.get("headers"), dict) else None
        if not api_base:
            raise RuntimeError("llm_config api_base missing")
        if not model:
            raise RuntimeError("llm_config model missing")
        # Resolve "auto" model name from server
        model = self._resolve_model_name(api_base, provider, model)

        base_url = api_base
        self._enforce_network_policy(base_url)
        timeout = int(cfg.get("timeout", 60))
        input_tokens_est = self._estimate_input_tokens(messages)
        t0 = time.perf_counter()

        # ---- Trace: 创建 Span ----
        trace_collector = get_trace_collector(self.cfg)
        audit_id = str(trace_meta.get("audit_id") or "")
        task_profile = str(route_meta.get("task_profile", "default"))
        model_role = str(route_meta.get("selected_role", "main"))
        stage = str(trace_meta.get("stage", "llm_call"))
        trace_span = None
        if trace_collector.enabled:
            trace_span = trace_collector.new_span(
                trace_id=str(trace_meta.get("trace_id") or ""),
                audit_id=audit_id,
                stage=stage,
                task_profile=task_profile,
                model_role=model_role,
                span_metadata={
                    "lang": str(trace_meta.get("lang", "")),
                    "module": str(trace_meta.get("module", "llm")),
                },
            )
            trace_collector.record_request(
                trace_span,
                provider=provider,
                api_base=str(cfg.get("api_base", "")),
                model_name=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                input_tokens_est=input_tokens_est,
            )
            # 将 trace_id 回写 trace_meta，供调用方关联
            trace_meta["trace_id"] = trace_span.trace_id
            trace_meta["span_id"] = trace_span.span_id

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
                    trace_span=trace_span,
                    trace_collector=trace_collector,
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
            # ---- Trace: 记录失败 ----
            if trace_span and trace_collector.enabled:
                latency_ms = int((time.perf_counter() - t0) * 1000)
                trace_collector.finish_span(
                    trace_span, status="failed", duration_ms=latency_ms)
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

        # Update model name from response if available (more accurate than config)
        response_model = self._clean_text(parsed.get("model", ""))
        if response_model and response_model != model:
            LLMService._model_cache[api_base] = response_model
            model = response_model

        # ---- Trace: 记录成功 ----
        if trace_span and trace_collector.enabled:
            trace_collector.finish_span(
                trace_span, status="success", duration_ms=latency_ms)

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
