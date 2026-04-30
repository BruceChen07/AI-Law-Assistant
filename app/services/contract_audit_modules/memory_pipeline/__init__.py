"""Compatibility facade for memory pipeline.

This package keeps the old import path stable while the implementation
is moved into:
`app.services.contract_audit_modules.memory_pipeline.audit_loop`.
"""

from typing import Any, Callable, Optional

from app.services.contract_audit_modules.memory_pipeline import audit_loop as _audit_loop
from app.services.contract_audit_modules.memory_pipeline.cleanup import (
    cleanup_debug_tree as cleanup_debug_tree,
)
from app.services.contract_audit_modules.memory_pipeline.evidence_builder import (
    prepare_evidence_context as prepare_evidence_context,
    _resolve_risk_citation_id as _resolve_risk_citation_id,
)
from app.services.contract_audit_modules.memory_pipeline.risk_reconciliation import (
    process_report_risks as process_report_risks,
)

__all__ = [
    "execute_memory_audit",
    "get_memory_embedder",
    "set_runtime_overrides",
    "_load_llm_json_object",
    "_resolve_risk_citation_id",
    "prepare_evidence_context",
    "process_report_risks",
    "cleanup_debug_tree",
]


def execute_memory_audit(*args: Any, **kwargs: Any):
    """Backward-compatible entrypoint for memory audit execution."""
    return _audit_loop.execute_memory_audit(*args, **kwargs)


def get_memory_embedder(*args: Any, **kwargs: Any):
    """Backward-compatible embedder resolver export."""
    return _audit_loop.get_memory_embedder(*args, **kwargs)


def _load_llm_json_object(*args: Any, **kwargs: Any):
    """Backward-compatible JSON parser export used by tests/tools."""
    return _audit_loop._load_llm_json_object(*args, **kwargs)


def set_runtime_overrides(
    get_memory_embedder: Optional[Callable[..., Any]] = None,
    hybrid_searcher: Optional[Any] = None,
) -> None:
    """Apply runtime overrides to the real implementation module."""
    if callable(get_memory_embedder):
        _audit_loop.get_memory_embedder = get_memory_embedder
    if hybrid_searcher is not None:
        _audit_loop.HybridSearcher = hybrid_searcher


def __getattr__(name: str) -> Any:
    return getattr(_audit_loop, name)


def __dir__() -> list[str]:
    return sorted(set(globals().keys()) | set(dir(_audit_loop)))
