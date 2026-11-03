"""Local-only LLM runtime boundary."""

from app.local_llm.policy import (
    assert_local_runtime_enabled,
    ensure_llamacpp_target,
    local_llm_enabled,
)

__all__ = [
    "assert_local_runtime_enabled",
    "ensure_llamacpp_target",
    "local_llm_enabled",
]
