import json
import os
import sys

import httpx


def clean_text(v: str) -> str:
    s = str(v or "").strip()
    s = s.strip("`").strip('"').strip("'").strip()
    return s


def main() -> int:
    api_key = clean_text(os.getenv("LLM_API_KEY", ""))
    base_url = clean_text(os.getenv("LLM_BASE_URL", "http://127.0.0.1:18081/v1"))
    model = clean_text(os.getenv("LLM_MODEL", "qwen3-14b-instruct-awq"))

    print(f"[debug] base_url={base_url}")
    print(f"[debug] model={model}")
    print(f"[debug] api_key_len={len(api_key)}")

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    response = httpx.post(
        f"{base_url.rstrip('/')}/chat/completions",
        headers=headers,
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": "You are a concise legal assistant."},
                {"role": "user", "content": "Reply with a one-field JSON object: {\"ok\": true}"},
            ],
            "temperature": 0.0,
            "max_tokens": 64,
        },
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"\n[error] {type(e).__name__}: {e}", file=sys.stderr)
        raise
