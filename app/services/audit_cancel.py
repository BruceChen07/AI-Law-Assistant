"""Shared cancellation signal for contract audits.

Kept in its own module so the progress plumbing can re-raise it instead of
swallowing it like every other callback error.
"""
from __future__ import annotations


class AuditCancelledError(RuntimeError):
    """Raised when the owner asked to abandon a running audit."""
