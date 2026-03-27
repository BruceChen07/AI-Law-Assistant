import os
import sys
from openai import OpenAI


def clean_text(v: str) -> str:
    s = str(v or "").strip()
    s = s.strip("`").strip('"').strip("'").strip()
    return s


def main() -> int:
    # 测试模式：如果为True，直接使用硬编码的正确值，绕过环境变量
    TEST_MODE = False  # 设置为True可以测试是否是环境变量问题

    if TEST_MODE:
        print("[debug] TEST_MODE enabled - using hardcoded values")
        api_key = "sk-458a55fe938a40e181f2b4578d98d609"
        base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        model = "qwen3.5-397b-a17b"
        image_url = "https://wallpapers.com/images/hd/excited-doggy-picture-5sm8o6npulz1guob.jpg"
    else:
        # 检测和清理环境变量污染
        for env_var in ["DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL", "DASHSCOPE_MODEL", "DASHSCOPE_TEST_IMAGE_URL"]:
            original_value = os.getenv(env_var)
            if original_value:
                cleaned_value = clean_text(original_value)
                if cleaned_value != original_value:
                    print(
                        f"[warning] Cleaned contaminated {env_var}: {repr(original_value)} -> {repr(cleaned_value)}")
                    os.environ[env_var] = cleaned_value

        api_key = clean_text(
            os.getenv("DASHSCOPE_API_KEY", "sk-458a55fe938a40e181f2b4578d98d609"))
        base_url = clean_text(os.getenv(
            "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"))
        model = clean_text(os.getenv("DASHSCOPE_MODEL", "qwen3.5-397b-a17b"))
        image_url = clean_text(
            os.getenv(
                "DASHSCOPE_TEST_IMAGE_URL",
                "https://wallpapers.com/images/hd/excited-doggy-picture-5sm8o6npulz1guob.jpg",
            )
        )

    print(f"[debug] base_url={base_url}")
    print(f"[debug] model={model}")
    print(f"[debug] api_key_len={len(api_key)}")

    client = OpenAI(api_key=api_key, base_url=base_url,
                    timeout=60, max_retries=1)

    completion = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You are a professional artist."},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_url}},
                    {"type": "text", "text": "Describe this image."},
                ],
            },
        ],
        stream=True,
    )

    for chunk in completion:
        delta = chunk.choices[0].delta if chunk.choices else None
        content = getattr(delta, "content", None) if delta else None
        if content:
            print(content, end="", flush=True)

    print()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"\n[error] {type(e).__name__}: {e}", file=sys.stderr)
        raise
