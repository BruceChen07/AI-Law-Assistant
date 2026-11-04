import re
from typing import Any, Dict, List


def estimate_text_tokens(text: Any) -> int:
    value = str(text or "")
    if not value:
        return 0
    cjk = len(re.findall(r"[\u4e00-\u9fff]", value))
    non_cjk = max(0, len(value) - cjk)
    return max(1, int(cjk * 1.1 + non_cjk / 3.8))


def estimate_messages_tokens(messages: List[Dict[str, Any]]) -> int:
    parts: List[str] = []
    for message in messages or []:
        if not isinstance(message, dict):
            parts.append(str(message or ""))
            continue
        content = message.get("content", "")
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
    return estimate_text_tokens("\n".join([item for item in parts if item]))
