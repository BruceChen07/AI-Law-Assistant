"""Audit template listing and detail."""
from __future__ import annotations

from app.core.database import get_conn
from typing import Any, Dict, List, Optional
from app.services.audit_capabilities._row_mappers import (
    _row_to_template,
)
from app.services.audit_capabilities.skills import (
    ensure_audit_capabilities_seeded,
)


def list_templates(cfg: Dict[str, Any], user_id: str = "", scene: str = "") -> List[Dict[str, Any]]:
    ensure_audit_capabilities_seeded(cfg)
    conn = get_conn(cfg)
    cur = conn.cursor()
    clauses = ["status='active'"]
    params: List[Any] = []
    if scene:
        clauses.append("scene=?")
        params.append(scene)
    clauses.append("(visibility='public' OR owner_id=?)")
    params.append(user_id)
    cur.execute(
        f"""
        SELECT *
        FROM audit_template
        WHERE {' AND '.join(clauses)}
        ORDER BY sort_order ASC, display_name ASC
        """,
        params,
    )
    rows = cur.fetchall()
    conn.close()
    return [_row_to_template(dict(row)) for row in rows]


def get_template_detail(cfg: Dict[str, Any], template_id: str, user_id: str = "") -> Optional[Dict[str, Any]]:
    ensure_audit_capabilities_seeded(cfg)
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM audit_template
        WHERE id=? AND status='active' AND (visibility='public' OR owner_id=?)
        """,
        (template_id, user_id),
    )
    row = cur.fetchone()
    conn.close()
    return _row_to_template(dict(row)) if row else None
