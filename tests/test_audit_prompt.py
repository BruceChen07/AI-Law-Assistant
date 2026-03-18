import pytest
from app.services.audit_prompt import _estimate_prompt_tokens, _build_prompt


def test_estimate_prompt_tokens():
    tokens = _estimate_prompt_tokens("测试123")
    assert tokens > 0


def test_build_prompt():
    prompt = _build_prompt("合同内容", "zh", tax_focus=True)
    assert "涉税条款" in prompt["user"]
    assert "合同内容" in prompt["user"]
    assert "不要为了覆盖等级而强行输出high/medium/low各一条" in prompt["user"]
