"""Schema migrations.

`init_db` used to inline ~800 lines of DDL. It is now one module per domain,
applied in a fixed order. Every statement is moved verbatim, so behaviour --
including the `duplicate column name: id` failure that some tests currently
hit -- is unchanged.
"""
from __future__ import annotations

from app.core.migrations import _01_core  # noqa: F401
from app.core.migrations import _02_prompts  # noqa: F401
from app.core.migrations import _03_contracts  # noqa: F401
from app.core.migrations import _04_rag  # noqa: F401
from app.core.migrations import _05_tax  # noqa: F401
from app.core.migrations import _06_audit_trail  # noqa: F401
from app.core.migrations import _07_export_archive  # noqa: F401
from app.core.migrations import _08_vector_upload  # noqa: F401
from app.core.migrations import _09_capabilities  # noqa: F401
from app.core.migrations import _10_agents  # noqa: F401
from app.core.migrations import _11_runtime  # noqa: F401


MIGRATIONS = (
    _01_core,
    _02_prompts,
    _03_contracts,
    _04_rag,
    _05_tax,
    _06_audit_trail,
    _07_export_archive,
    _08_vector_upload,
    _09_capabilities,
    _10_agents,
    _11_runtime,
)


def apply_migrations(cur) -> None:
    """Run every domain module in its original execution order."""
    _01_core.apply(cur)
    _02_prompts.apply(cur)
    _03_contracts.apply(cur)
    _04_rag.apply(cur)
    _05_tax.apply(cur)
    _06_audit_trail.apply(cur)
    _07_export_archive.apply(cur)
    _08_vector_upload.apply(cur)
    _09_capabilities.apply(cur)
    _10_agents.apply(cur)
    _11_runtime.apply(cur)
