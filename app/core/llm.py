import json
import urllib.request
from typing import Dict, Any, List, Optional, Tuple


class LLMService:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg or {}

    def _get_llm_config(self) -> Dict[str, Any]:
        llm_cfg = self.cfg.get("llm_config") or {}
        if isinstance(llm_cfg, dict):
            return llm_cfg
        return {}

    def _build_url(self, base: str) -> str:
        if base.endswith("/chat/completions"):
            return base
        if base.endswith("/"):
            return f"{base}chat/completions"
        return f"{base}/chat/completions"

    def _build_headers(self, api_key: Optional[str], extra_headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if extra_headers:
            headers.update({k: str(v) for k, v in extra_headers.items()})
        return headers

    def chat(self, messages: List[Dict[str, str]], overrides: Optional[Dict[str, Any]] = None) -> Tuple[str, Dict[str, Any]]:
        cfg = dict(self._get_llm_config())
        if overrides:
            cfg.update(overrides)
        api_base = str(cfg.get("api_base", "")).strip()
        api_key = str(cfg.get("api_key", "")).strip()
        model = str(cfg.get("model", "")).strip()
        temperature = float(cfg.get("temperature", 0.2))
        max_tokens = int(cfg.get("max_tokens", 2048))
        extra_headers = cfg.get("headers") if isinstance(cfg.get("headers"), dict) else None
        if not api_base or not model:
            raise RuntimeError("llm_config api_base or model missing")

        url = self._build_url(api_base)
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=self._build_headers(api_key, extra_headers), method="POST")
        with urllib.request.urlopen(req, timeout=int(cfg.get("timeout", 60))) as resp:
            raw = resp.read().decode("utf-8")
        parsed = json.loads(raw)
        content = ""
        try:
            content = parsed["choices"][0]["message"]["content"]
        except Exception:
            content = raw
        return content, parsed
