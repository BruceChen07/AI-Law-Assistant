"""Rule-pack listing, drafts, review and published versions."""
from __future__ import annotations

import uuid

from app.core.database import get_conn
from typing import Any, Dict, List, Optional
from app.services.audit_capabilities._normalizers import (
    _json_text,
    _normalize_rule_selector,
    _safe_json_loads,
    _utc_now_iso,
)
from app.services.audit_capabilities._row_mappers import (
    _row_to_rule_pack,
    _row_to_rule_pack_draft,
    _row_to_rule_pack_version,
)
from app.services.audit_capabilities.skills import (
    ensure_audit_capabilities_seeded,
)


def list_rule_packs(cfg: Dict[str, Any], user_id: str = "", scene: str = "") -> List[Dict[str, Any]]:
    ensure_audit_capabilities_seeded(cfg)
    conn = get_conn(cfg)
    cur = conn.cursor()
    clauses = ["p.status='active'"]
    params: List[Any] = []
    if scene:
        clauses.append("p.scene=?")
        params.append(scene)
    clauses.append("(p.visibility='public' OR p.owner_id=?)")
    params.append(user_id)
    sql = f"""
        SELECT
            p.*,
            CASE
                WHEN p.id='tax_core_default' THEN (SELECT COUNT(1) FROM tax_rule)
                ELSE 0
            END AS rule_count
        FROM audit_rule_pack p
        WHERE {' AND '.join(clauses)}
        ORDER BY p.sort_order ASC, p.display_name ASC
    """
    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()
    return [_row_to_rule_pack(dict(row)) for row in rows]


def get_rule_pack_detail(cfg: Dict[str, Any], pack_id: str, user_id: str = "") -> Optional[Dict[str, Any]]:
    ensure_audit_capabilities_seeded(cfg)
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            p.*,
            CASE
                WHEN p.id='tax_core_default' THEN (SELECT COUNT(1) FROM tax_rule)
                ELSE 0
            END AS rule_count
        FROM audit_rule_pack p
        WHERE p.id=? AND p.status='active' AND (p.visibility='public' OR p.owner_id=?)
        """,
        (pack_id, user_id),
    )
    row = cur.fetchone()
    conn.close()
    return _row_to_rule_pack(dict(row)) if row else None


def list_rule_pack_drafts(
    cfg: Dict[str, Any],
    owner_id: str = "",
    status: str = "",
    base_pack_id: str = "",
    include_all: bool = False,
) -> List[Dict[str, Any]]:
    ensure_audit_capabilities_seeded(cfg)
    conn = get_conn(cfg)
    cur = conn.cursor()
    clauses = ["1=1"]
    params: List[Any] = []
    if not include_all:
        clauses.append("owner_id=?")
        params.append(owner_id)
    if status:
        clauses.append("status=?")
        params.append(status)
    if base_pack_id:
        clauses.append("base_pack_id=?")
        params.append(base_pack_id)
    cur.execute(
        f"""
        SELECT *
        FROM audit_rule_pack_draft
        WHERE {' AND '.join(clauses)}
        ORDER BY updated_at DESC, created_at DESC
        """,
        params,
    )
    rows = cur.fetchall()
    conn.close()
    return [_row_to_rule_pack_draft(dict(row)) for row in rows]


def get_rule_pack_draft(
    cfg: Dict[str, Any],
    draft_id: str,
    owner_id: str = "",
    include_all: bool = False,
) -> Optional[Dict[str, Any]]:
    ensure_audit_capabilities_seeded(cfg)
    conn = get_conn(cfg)
    cur = conn.cursor()
    clauses = ["id=?"]
    params: List[Any] = [draft_id]
    if not include_all:
        clauses.append("owner_id=?")
        params.append(owner_id)
    cur.execute(
        f"""
        SELECT *
        FROM audit_rule_pack_draft
        WHERE {' AND '.join(clauses)}
        """,
        params,
    )
    row = cur.fetchone()
    conn.close()
    return _row_to_rule_pack_draft(dict(row)) if row else None


def create_rule_pack_draft(
    cfg: Dict[str, Any],
    owner_id: str,
    base_pack_id: str,
    display_name: Optional[str] = None,
    description: Optional[str] = None,
    selector: Optional[Dict[str, Any]] = None,
    source_note: Optional[str] = None,
    change_summary: str = "",
) -> Dict[str, Any]:
    ensure_audit_capabilities_seeded(cfg)
    pack = get_rule_pack_detail(cfg, base_pack_id, user_id=owner_id)
    if not pack:
        raise ValueError("rule pack not found")

    now = _utc_now_iso()
    draft_id = str(uuid.uuid4())
    final_display_name = str(
        display_name or pack.get("display_name") or "").strip()
    final_description = str(
        pack.get("description") if description is None else description or ""
    ).strip()
    final_source_note = str(
        pack.get("source_note") if source_note is None else source_note or ""
    ).strip()
    final_selector = _normalize_rule_selector(
        pack.get("selector") if selector is None else selector
    )
    if not final_display_name:
        raise ValueError("display_name is required")

    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO audit_rule_pack_draft(
            id, base_pack_id, owner_id, display_name, scene, description,
            selector_json, source_note, change_summary, status,
            review_comment, submitted_at, reviewed_by, reviewed_at,
            published_version_no, created_at, updated_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            draft_id,
            base_pack_id,
            owner_id,
            final_display_name,
            str(pack.get("scene") or ""),
            final_description,
            _json_text(final_selector),
            final_source_note,
            str(change_summary or "").strip(),
            "draft",
            None,
            None,
            None,
            None,
            None,
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()
    return get_rule_pack_draft(cfg, draft_id, owner_id=owner_id) or {}


def update_rule_pack_draft(
    cfg: Dict[str, Any],
    draft_id: str,
    owner_id: str,
    display_name: Optional[str] = None,
    description: Optional[str] = None,
    selector: Optional[Dict[str, Any]] = None,
    source_note: Optional[str] = None,
    change_summary: Optional[str] = None,
) -> Dict[str, Any]:
    ensure_audit_capabilities_seeded(cfg)
    draft = get_rule_pack_draft(cfg, draft_id, owner_id=owner_id)
    if not draft:
        raise ValueError("rule pack draft not found")
    if str(draft.get("status") or "") != "draft":
        raise ValueError("only draft status can be updated")

    updates: Dict[str, Any] = {}
    if display_name is not None:
        normalized_display_name = str(display_name or "").strip()
        if not normalized_display_name:
            raise ValueError("display_name is required")
        updates["display_name"] = normalized_display_name
    if description is not None:
        updates["description"] = str(description or "").strip()
    if selector is not None:
        updates["selector_json"] = _json_text(
            _normalize_rule_selector(selector))
    if source_note is not None:
        updates["source_note"] = str(source_note or "").strip()
    if change_summary is not None:
        updates["change_summary"] = str(change_summary or "").strip()
    if not updates:
        raise ValueError("no fields to update")

    updates["updated_at"] = _utc_now_iso()
    set_clause = ", ".join(f"{column}=?" for column in updates.keys())
    params = list(updates.values()) + [draft_id, owner_id]
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        f"""
        UPDATE audit_rule_pack_draft
        SET {set_clause}
        WHERE id=? AND owner_id=? AND status='draft'
        """,
        params,
    )
    conn.commit()
    conn.close()
    return get_rule_pack_draft(cfg, draft_id, owner_id=owner_id) or {}


def submit_rule_pack_draft(cfg: Dict[str, Any], draft_id: str, owner_id: str) -> Dict[str, Any]:
    ensure_audit_capabilities_seeded(cfg)
    draft = get_rule_pack_draft(cfg, draft_id, owner_id=owner_id)
    if not draft:
        raise ValueError("rule pack draft not found")
    if str(draft.get("status") or "") != "draft":
        raise ValueError("draft is not in editable status")
    if not str(draft.get("change_summary") or "").strip():
        raise ValueError("change_summary is required before submission")

    now = _utc_now_iso()
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE audit_rule_pack_draft
        SET status=?, submitted_at=?, updated_at=?
        WHERE id=? AND owner_id=? AND status='draft'
        """,
        ("pending_review", now, now, draft_id, owner_id),
    )
    conn.commit()
    conn.close()
    return get_rule_pack_draft(cfg, draft_id, owner_id=owner_id) or {}


def review_rule_pack_draft(
    cfg: Dict[str, Any],
    draft_id: str,
    reviewer_id: str,
    action: str,
    review_comment: str = "",
) -> Dict[str, Any]:
    ensure_audit_capabilities_seeded(cfg)
    normalized_action = str(action or "").strip().lower()
    if normalized_action not in {"approve", "reject"}:
        raise ValueError("action must be approve or reject")

    draft = get_rule_pack_draft(cfg, draft_id, include_all=True)
    if not draft:
        raise ValueError("rule pack draft not found")
    if str(draft.get("status") or "") != "pending_review":
        raise ValueError("draft is not pending review")

    now = _utc_now_iso()
    conn = get_conn(cfg)
    cur = conn.cursor()
    if normalized_action == "reject":
        cur.execute(
            """
            UPDATE audit_rule_pack_draft
            SET status=?, review_comment=?, reviewed_by=?, reviewed_at=?, updated_at=?
            WHERE id=? AND status='pending_review'
            """,
            ("rejected", str(review_comment or "").strip(),
             reviewer_id, now, now, draft_id),
        )
        conn.commit()
        conn.close()
        return get_rule_pack_draft(cfg, draft_id, include_all=True) or {}

    cur.execute(
        "SELECT COALESCE(MAX(version_no), 0) AS max_version_no FROM audit_rule_pack_version WHERE pack_id=?",
        (draft["base_pack_id"],),
    )
    row = cur.fetchone()
    next_version_no = int(row["max_version_no"] or 0) + 1
    version_id = str(uuid.uuid4())
    cur.execute(
        """
        INSERT INTO audit_rule_pack_version(
            id, pack_id, draft_id, version_no, display_name, scene, description,
            selector_json, source_note, change_summary, published_by, published_at,
            created_at, updated_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            version_id,
            draft["base_pack_id"],
            draft_id,
            next_version_no,
            draft["display_name"],
            draft["scene"],
            draft.get("description", ""),
            _json_text(draft.get("selector") or {}),
            draft.get("source_note", ""),
            draft.get("change_summary", ""),
            reviewer_id,
            now,
            now,
            now,
        ),
    )
    cur.execute(
        """
        UPDATE audit_rule_pack
        SET display_name=?, scene=?, description=?, selector_json=?, source_note=?, updated_at=?
        WHERE id=?
        """,
        (
            draft["display_name"],
            draft["scene"],
            draft.get("description", ""),
            _json_text(draft.get("selector") or {}),
            draft.get("source_note", ""),
            now,
            draft["base_pack_id"],
        ),
    )
    cur.execute(
        """
        UPDATE audit_rule_pack_draft
        SET status=?, review_comment=?, reviewed_by=?, reviewed_at=?, published_version_no=?, updated_at=?
        WHERE id=? AND status='pending_review'
        """,
        (
            "approved",
            str(review_comment or "").strip(),
            reviewer_id,
            now,
            next_version_no,
            now,
            draft_id,
        ),
    )
    conn.commit()
    conn.close()
    return get_rule_pack_draft(cfg, draft_id, include_all=True) or {}


def list_rule_pack_versions(
    cfg: Dict[str, Any],
    pack_id: str,
    user_id: str = "",
) -> List[Dict[str, Any]]:
    ensure_audit_capabilities_seeded(cfg)
    pack = get_rule_pack_detail(cfg, pack_id, user_id=user_id)
    if not pack:
        return []
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM audit_rule_pack_version
        WHERE pack_id=?
        ORDER BY version_no DESC, published_at DESC
        """,
        (pack_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return [_row_to_rule_pack_version(dict(row)) for row in rows]


def list_rules(
    cfg: Dict[str, Any],
    pack_id: str = "",
    rule_type: str = "",
    limit: int = 200,
    offset: int = 0,
) -> Dict[str, Any]:
    ensure_audit_capabilities_seeded(cfg)
    selector: Dict[str, Any] = {}
    if pack_id:
        conn = get_conn(cfg)
        cur = conn.cursor()
        cur.execute(
            "SELECT selector_json FROM audit_rule_pack WHERE id=?", (pack_id,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return {"items": [], "total": 0, "pack_id": pack_id}
        selector = _safe_json_loads(row["selector_json"], {})

    clauses = ["1=1"]
    params: List[Any] = []
    if rule_type:
        clauses.append("rule_type=?")
        params.append(rule_type)
    selector_rule_types = selector.get(
        "rule_types") if isinstance(selector, dict) else None
    if isinstance(selector_rule_types, list) and selector_rule_types:
        placeholders = ",".join("?" for _ in selector_rule_types)
        clauses.append(f"rule_type IN ({placeholders})")
        params.extend(selector_rule_types)
    where_sql = " AND ".join(clauses)

    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        f"SELECT COUNT(1) AS total FROM tax_rule WHERE {where_sql}", params)
    total_row = cur.fetchone()
    total = int(total_row["total"]) if total_row else 0
    cur.execute(
        f"""
        SELECT id, law_title, article_no, rule_type, region, industry, effective_date, expiry_date, source_text
        FROM tax_rule
        WHERE {where_sql}
        ORDER BY effective_date DESC, article_no ASC, created_at DESC
        LIMIT ? OFFSET ?
        """,
        params + [max(1, min(limit, 500)), max(0, offset)],
    )
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return {"items": rows, "total": total, "pack_id": pack_id}
