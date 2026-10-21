"""Audit skill CRUD plus the lazy seeding hook."""
from __future__ import annotations

from app.core.database import get_conn
from typing import Any, Dict, List, Optional
from app.services.audit_capabilities._normalizers import (
    _SKILL_ID_PATTERN,
    _SKILL_STATUS_VALUES,
    _SKILL_VISIBILITY_VALUES,
    _json_text,
    _normalize_changelog_items,
    _normalize_json_schema,
    _normalize_optional_int,
    _normalize_string_list,
    _slugify_skill_id,
    _utc_now_iso,
)
from app.services.audit_capabilities._row_mappers import (
    _row_to_skill,
    _row_to_skill_file,
    _row_to_skill_version,
)
from app.services.audit_capabilities._seed_data import (
    BUILTIN_RULE_PACK_SEEDS,
    BUILTIN_SKILL_SEEDS,
    BUILTIN_TEMPLATE_SEEDS,
    _build_default_skill_detail,
    _build_default_skill_md,
    _get_skill_seed_detail,
)


def _save_skill_detail(
    cur,
    skill_id: str,
    detail: Dict[str, Any],
    now: str,
) -> None:
    cur.execute(
        """
        UPDATE audit_skill
        SET publisher_name = ?, publisher_handle = ?, install_command = ?, skill_md_text = ?,
            skill_card_json = ?, current_version = ?, license_name = ?, security_audit_status = ?,
            downloads_30d = ?, downloads_all_time = ?, last_published_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            detail.get("publisher_name", ""),
            detail.get("publisher_handle", ""),
            detail.get("install_command", ""),
            detail.get("skill_md_text", ""),
            _json_text(detail.get("skill_card", {})),
            detail.get("current_version", ""),
            detail.get("license_name", ""),
            detail.get("security_audit_status", ""),
            int(detail.get("downloads_30d", 0) or 0),
            int(detail.get("downloads_all_time", 0) or 0),
            detail.get("last_published_at", ""),
            now,
            skill_id,
        ),
    )
    cur.execute("DELETE FROM audit_skill_file WHERE skill_id = ?", (skill_id,))
    for index, file_item in enumerate(detail.get("files", []), start=1):
        file_path = str(file_item.get("path") or "").strip()
        if not file_path:
            continue
        cur.execute(
            """
            INSERT INTO audit_skill_file(
                id, skill_id, path, entry_type, size_bytes, branch_name,
                content_text, sort_order, created_at, updated_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                f"{skill_id}::file::{index}",
                skill_id,
                file_path,
                str(file_item.get("entry_type") or "file"),
                int(file_item.get("size_bytes", 0) or 0),
                str(file_item.get("branch_name") or ""),
                str(file_item.get("content_text") or ""),
                int(file_item.get("sort_order", index) or index),
                now,
                now,
            ),
        )
    cur.execute(
        "DELETE FROM audit_skill_version WHERE skill_id = ?", (skill_id,))
    for index, version_item in enumerate(detail.get("versions", []), start=1):
        version_tag = str(version_item.get("version_tag") or "").strip()
        if not version_tag:
            continue
        cur.execute(
            """
            INSERT INTO audit_skill_version(
                id, skill_id, version_tag, release_label, published_at, is_latest,
                download_url, changelog_text, changelog_json, sort_order, created_at, updated_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                f"{skill_id}::version::{index}",
                skill_id,
                version_tag,
                str(version_item.get("release_label") or ""),
                str(version_item.get("published_at") or ""),
                1 if version_item.get("is_latest") else 0,
                str(version_item.get("download_url") or ""),
                str(version_item.get("changelog_text") or ""),
                _json_text(version_item.get("changelog_json", [])),
                int(version_item.get("sort_order", index) or index),
                now,
                now,
            ),
        )


def ensure_audit_capabilities_seeded(cfg: Dict[str, Any]) -> None:
    now = _utc_now_iso()
    conn = get_conn(cfg)
    cur = conn.cursor()
    try:
        for item in BUILTIN_SKILL_SEEDS:
            cur.execute("SELECT * FROM audit_skill WHERE id = ?",
                        (item["id"],))
            existing_row = cur.fetchone()
            should_insert = existing_row is None
            if should_insert:
                cur.execute(
                    """
                    INSERT INTO audit_skill(
                        id, display_name, category, scene, description, owner_type, owner_id,
                        visibility, status, source_url, reference_summary,
                        input_schema_json, output_schema_json, config_schema_json, tags_json,
                        sort_order, created_at, updated_at
                    )
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        item["id"],
                        item["display_name"],
                        item["category"],
                        item["scene"],
                        item.get("description", ""),
                        "system",
                        None,
                        "public",
                        "active",
                        item.get("source_url", ""),
                        item.get("reference_summary", ""),
                        item.get("input_schema_json", ""),
                        item.get("output_schema_json", ""),
                        item.get("config_schema_json", ""),
                        item.get("tags_json", "[]"),
                        int(item.get("sort_order", 100)),
                        now,
                        now,
                    ),
                )
                cur.execute(
                    "SELECT * FROM audit_skill WHERE id = ?", (item["id"],))
                existing_row = cur.fetchone()
            existing_item = _row_to_skill(dict(existing_row))
            detail = _get_skill_seed_detail(item)
            cur.execute(
                "SELECT COUNT(1) FROM audit_skill_file WHERE skill_id = ?", (
                    item["id"],)
            )
            file_count = int((cur.fetchone() or [0])[0] or 0)
            cur.execute(
                "SELECT COUNT(1) FROM audit_skill_version WHERE skill_id = ?", (
                    item["id"],)
            )
            version_count = int((cur.fetchone() or [0])[0] or 0)
            needs_detail_seed = should_insert or (
                not str(existing_item.get("publisher_name") or "").strip()
                and not str(existing_item.get("skill_md_text") or "").strip()
                and file_count == 0
                and version_count == 0
            )
            if needs_detail_seed:
                _save_skill_detail(cur, item["id"], detail, now)

        for item in BUILTIN_RULE_PACK_SEEDS:
            cur.execute(
                """
                INSERT OR IGNORE INTO audit_rule_pack(
                    id, display_name, scene, description, owner_type, owner_id,
                    visibility, status, selector_json, source_note, sort_order,
                    created_at, updated_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    item["id"],
                    item["display_name"],
                    item["scene"],
                    item.get("description", ""),
                    "system",
                    None,
                    "public",
                    "active",
                    item.get("selector_json", "{}"),
                    item.get("source_note", ""),
                    int(item.get("sort_order", 100)),
                    now,
                    now,
                ),
            )

        for item in BUILTIN_TEMPLATE_SEEDS:
            cur.execute(
                """
                INSERT OR IGNORE INTO audit_template(
                    id, display_name, scene, description, owner_type, owner_id,
                    visibility, status, skill_ids_json, rule_pack_ids_json,
                    max_llm_steps, max_skills_per_run, sort_order, created_at, updated_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    item["id"],
                    item["display_name"],
                    item["scene"],
                    item.get("description", ""),
                    "system",
                    None,
                    "public",
                    "active",
                    item.get("skill_ids_json", "[]"),
                    item.get("rule_pack_ids_json", "[]"),
                    int(item.get("max_llm_steps", 2)),
                    int(item.get("max_skills_per_run", 6)),
                    int(item.get("sort_order", 100)),
                    now,
                    now,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def list_visible_skills(
    cfg: Dict[str, Any],
    user_id: str = "",
    scene: str = "",
    category: str = "",
) -> List[Dict[str, Any]]:
    ensure_audit_capabilities_seeded(cfg)
    conn = get_conn(cfg)
    cur = conn.cursor()
    clauses = ["status='active'"]
    params: List[Any] = []
    if scene:
        clauses.append("scene=?")
        params.append(scene)
    if category:
        clauses.append("category=?")
        params.append(category)
    clauses.append("(visibility='public' OR owner_id=?)")
    params.append(user_id)
    sql = f"""
        SELECT *
        FROM audit_skill
        WHERE {' AND '.join(clauses)}
        ORDER BY sort_order ASC, display_name ASC
    """
    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()
    return [_row_to_skill(dict(row)) for row in rows]


def _attach_skill_detail_meta(cur, item: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(item)
    skill_id = str(item.get("id") or "")
    cur.execute(
        """
        SELECT *
        FROM audit_skill_file
        WHERE skill_id = ?
        ORDER BY sort_order ASC, path ASC
        """,
        (skill_id,),
    )
    out["files"] = [_row_to_skill_file(dict(row)) for row in cur.fetchall()]
    cur.execute(
        """
        SELECT *
        FROM audit_skill_version
        WHERE skill_id = ?
        ORDER BY sort_order ASC, published_at DESC, version_tag DESC
        """,
        (skill_id,),
    )
    out["versions"] = [_row_to_skill_version(
        dict(row)) for row in cur.fetchall()]
    return out


def get_skill_detail(cfg: Dict[str, Any], skill_id: str, user_id: str = "") -> Optional[Dict[str, Any]]:
    ensure_audit_capabilities_seeded(cfg)
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM audit_skill
        WHERE id=? AND status='active' AND (visibility='public' OR owner_id=?)
        """,
        (skill_id, user_id),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return None
    item = _attach_skill_detail_meta(cur, _row_to_skill(dict(row)))
    conn.close()
    return item


def _normalize_skill_id(raw_id: str, display_name: str) -> str:
    candidate = str(raw_id or "").strip().lower()
    if not candidate:
        candidate = _slugify_skill_id(display_name)
    if not candidate:
        raise ValueError("skill id is required")
    if not _SKILL_ID_PATTERN.match(candidate):
        raise ValueError(
            "skill id must be 3-64 chars and contain only lowercase letters, numbers, underscores, or hyphens"
        )
    return candidate


def _normalize_skill_files(
    value: Any,
    default: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    raw_items = default if value is None else value
    if raw_items in (None, ""):
        raw_items = []
    if not isinstance(raw_items, list):
        raise ValueError("files must be a list")
    items: List[Dict[str, Any]] = []
    seen_paths = set()
    for index, raw_item in enumerate(raw_items, start=1):
        if not isinstance(raw_item, dict):
            raise ValueError("files contains invalid item")
        path = str(raw_item.get("path") or "").strip()
        if not path:
            raise ValueError("files.path is required")
        if path in seen_paths:
            raise ValueError(f"duplicate file path: {path}")
        seen_paths.add(path)
        entry_type = str(raw_item.get("entry_type") or "file").strip().lower()
        if entry_type not in {"file", "dir"}:
            raise ValueError("files.entry_type must be file or dir")
        content_text = "" if entry_type == "dir" else str(
            raw_item.get("content_text") or "")
        size_value = raw_item.get("size_bytes")
        size_bytes = len(content_text.encode("utf-8")) if size_value in (None, "") else _normalize_optional_int(
            size_value, "files.size_bytes", 0
        )
        items.append(
            {
                "path": path,
                "entry_type": entry_type,
                "content_text": content_text,
                "size_bytes": max(size_bytes, 0),
                "branch_name": str(raw_item.get("branch_name") or ""),
                "sort_order": _normalize_optional_int(
                    raw_item.get("sort_order"), "files.sort_order", index
                ),
            }
        )
    return items


def _normalize_skill_versions(
    value: Any,
    default: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    raw_items = default if value is None else value
    if raw_items in (None, ""):
        raw_items = []
    if not isinstance(raw_items, list):
        raise ValueError("versions must be a list")
    items: List[Dict[str, Any]] = []
    seen_tags = set()
    latest_found = False
    for index, raw_item in enumerate(raw_items, start=1):
        if not isinstance(raw_item, dict):
            raise ValueError("versions contains invalid item")
        version_tag = str(raw_item.get("version_tag") or "").strip()
        if not version_tag:
            raise ValueError("versions.version_tag is required")
        if version_tag in seen_tags:
            raise ValueError(f"duplicate version tag: {version_tag}")
        seen_tags.add(version_tag)
        changelog_items = _normalize_changelog_items(raw_item)
        is_latest = bool(raw_item.get("is_latest"))
        latest_found = latest_found or is_latest
        items.append(
            {
                "version_tag": version_tag,
                "release_label": str(raw_item.get("release_label") or "").strip(),
                "published_at": str(raw_item.get("published_at") or "").strip(),
                "is_latest": is_latest,
                "download_url": str(raw_item.get("download_url") or "").strip(),
                "changelog_items": changelog_items,
                "changelog_text": "\n".join(f"- {item}" for item in changelog_items),
                "changelog_json": changelog_items,
                "sort_order": _normalize_optional_int(
                    raw_item.get("sort_order"), "versions.sort_order", index
                ),
            }
        )
    if items and not latest_found:
        items[0]["is_latest"] = True
        if not items[0]["release_label"]:
            items[0]["release_label"] = "Latest"
    return items


def _upsert_detail_file(
    files: List[Dict[str, Any]],
    *,
    path: str,
    content_text: str,
    sort_order: int,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    matched = False
    encoded_size = len(content_text.encode("utf-8"))
    for item in files:
        if str(item.get("path") or "") == path:
            next_item = dict(item)
            next_item["entry_type"] = "file"
            next_item["content_text"] = content_text
            next_item["size_bytes"] = encoded_size
            next_item["sort_order"] = int(item.get("sort_order") or sort_order)
            out.append(next_item)
            matched = True
        else:
            out.append(dict(item))
    if not matched:
        out.append(
            {
                "path": path,
                "entry_type": "file",
                "content_text": content_text,
                "size_bytes": encoded_size,
                "branch_name": "main",
                "sort_order": sort_order,
            }
        )
    return sorted(
        out,
        key=lambda item: (
            int(item.get("sort_order") or 0),
            str(item.get("path") or ""),
        ),
    )


def _normalize_skill_detail_payload(
    payload: Dict[str, Any],
    normalized_skill: Dict[str, Any],
    existing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    current = existing or {}
    skill_card = _normalize_json_schema(
        payload.get("skill_card", current.get("skill_card", {})),
        "skill_card",
    )
    detail = {
        "publisher_name": str(
            payload.get("publisher_name", current.get(
                "publisher_name", "")) or ""
        ).strip(),
        "publisher_handle": str(
            payload.get("publisher_handle", current.get(
                "publisher_handle", "")) or ""
        ).strip(),
        "install_command": str(
            payload.get("install_command", current.get(
                "install_command", "")) or ""
        ).strip(),
        "skill_md_text": str(
            payload.get("skill_md_text", current.get(
                "skill_md_text", "")) or ""
        ),
        "skill_card": skill_card,
        "current_version": str(
            payload.get("current_version", current.get(
                "current_version", "")) or ""
        ).strip(),
        "license_name": str(
            payload.get("license_name", current.get("license_name", "")) or ""
        ).strip(),
        "security_audit_status": str(
            payload.get(
                "security_audit_status",
                current.get("security_audit_status", ""),
            ) or ""
        ).strip(),
        "downloads_30d": _normalize_optional_int(
            payload.get("downloads_30d", current.get("downloads_30d", 0)),
            "downloads_30d",
            0,
        ),
        "downloads_all_time": _normalize_optional_int(
            payload.get("downloads_all_time", current.get(
                "downloads_all_time", 0)),
            "downloads_all_time",
            0,
        ),
        "last_published_at": str(
            payload.get("last_published_at", current.get(
                "last_published_at", "")) or ""
        ).strip(),
    }
    detail["files"] = _normalize_skill_files(
        payload.get("files"),
        default=current.get("files", []),
    )
    detail["versions"] = _normalize_skill_versions(
        payload.get("versions"),
        default=current.get("versions", []),
    )
    if not detail["current_version"] and detail["versions"]:
        latest = next(
            (item for item in detail["versions"] if item.get("is_latest")),
            detail["versions"][0],
        )
        detail["current_version"] = str(latest.get("version_tag") or "")
    if not detail["publisher_name"]:
        detail["publisher_name"] = "AI-Law-Assistant"
    if not detail["publisher_handle"]:
        detail["publisher_handle"] = "@system"
    if not detail["license_name"]:
        detail["license_name"] = "Internal Reference"
    if not detail["skill_md_text"]:
        detail["skill_md_text"] = _build_default_skill_md(
            {
                "id": normalized_skill["id"],
                "display_name": normalized_skill["display_name"],
                "category": normalized_skill["category"],
                "scene": normalized_skill["scene"],
                "description": normalized_skill["description"],
                "source_url": normalized_skill["source_url"],
                "reference_summary": normalized_skill["reference_summary"],
                "tags_json": _json_text(normalized_skill["tags"]),
            }
        )
    detail["files"] = _upsert_detail_file(
        detail["files"],
        path="SKILL.md",
        content_text=detail["skill_md_text"],
        sort_order=10,
    )
    detail["files"] = _upsert_detail_file(
        detail["files"],
        path="skill-card.md",
        content_text=_json_text(detail["skill_card"]),
        sort_order=20,
    )
    return detail


def _normalize_skill_payload(
    payload: Dict[str, Any],
    existing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    current = existing or {}
    display_name = str(
        payload.get("display_name", current.get("display_name", "")) or ""
    ).strip()
    category = str(payload.get(
        "category", current.get("category", "")) or "").strip()
    scene = str(payload.get("scene", current.get("scene", "")) or "").strip()
    description = str(
        payload.get("description", current.get("description", "")) or ""
    ).strip()
    source_url = str(payload.get(
        "source_url", current.get("source_url", "")) or "").strip()
    reference_summary = str(
        payload.get("reference_summary", current.get(
            "reference_summary", "")) or ""
    ).strip()
    visibility = str(
        payload.get("visibility", current.get(
            "visibility", "public")) or "public"
    ).strip().lower()
    status = str(payload.get("status", current.get(
        "status", "active")) or "active").strip().lower()
    sort_order_raw = payload.get("sort_order", current.get("sort_order", 100))
    tags = _normalize_string_list(
        payload.get("tags", current.get("tags", [])),
        "tags",
    ) or []
    input_schema = _normalize_json_schema(
        payload.get("input_schema", current.get("input_schema", {})),
        "input_schema",
    )
    output_schema = _normalize_json_schema(
        payload.get("output_schema", current.get("output_schema", {})),
        "output_schema",
    )
    config_schema = _normalize_json_schema(
        payload.get("config_schema", current.get("config_schema", {})),
        "config_schema",
    )

    if not display_name:
        raise ValueError("display_name is required")
    if len(display_name) > 120:
        raise ValueError("display_name must be <= 120 chars")
    if not category:
        raise ValueError("category is required")
    if len(category) > 60:
        raise ValueError("category must be <= 60 chars")
    if not scene:
        raise ValueError("scene is required")
    if len(scene) > 80:
        raise ValueError("scene must be <= 80 chars")
    if visibility not in _SKILL_VISIBILITY_VALUES:
        raise ValueError("visibility must be public or private")
    if status not in _SKILL_STATUS_VALUES:
        raise ValueError("status must be active, disabled, or deleted")

    try:
        sort_order = int(sort_order_raw)
    except Exception as exc:
        raise ValueError("sort_order must be an integer") from exc
    if sort_order < 0 or sort_order > 100000:
        raise ValueError("sort_order must be between 0 and 100000")

    return {
        "id": str(current.get("id") or payload.get("id") or "").strip(),
        "display_name": display_name,
        "category": category,
        "scene": scene,
        "description": description,
        "source_url": source_url,
        "reference_summary": reference_summary,
        "visibility": visibility,
        "status": status,
        "sort_order": sort_order,
        "tags": tags,
        "input_schema": input_schema,
        "output_schema": output_schema,
        "config_schema": config_schema,
    }


def _get_skill_reference_counts(cur, skill_id: str) -> Dict[str, int]:
    token = f'"{skill_id}"'
    cur.execute(
        """
        SELECT COUNT(1)
        FROM audit_template
        WHERE status <> 'deleted' AND skill_ids_json LIKE ?
        """,
        (f"%{token}%",),
    )
    template_ref_count = int((cur.fetchone() or [0])[0] or 0)
    cur.execute(
        """
        SELECT COUNT(1)
        FROM agent_profile
        WHERE status <> 'deleted' AND enabled_skill_ids_json LIKE ?
        """,
        (f"%{token}%",),
    )
    agent_ref_count = int((cur.fetchone() or [0])[0] or 0)
    return {
        "template_ref_count": template_ref_count,
        "agent_ref_count": agent_ref_count,
        "in_use": template_ref_count > 0 or agent_ref_count > 0,
    }


def _attach_skill_reference_meta(cur, item: Dict[str, Any]) -> Dict[str, Any]:
    refs = _get_skill_reference_counts(cur, str(item.get("id") or ""))
    out = dict(item)
    out.update(refs)
    return out


def list_admin_skills(
    cfg: Dict[str, Any],
    page: int = 1,
    page_size: int = 10,
    search: str = "",
    category: str = "",
    scene: str = "",
    status: str = "",
) -> Dict[str, Any]:
    ensure_audit_capabilities_seeded(cfg)
    conn = get_conn(cfg)
    cur = conn.cursor()
    clauses = ["1=1"]
    params: List[Any] = []
    search_text = str(search or "").strip()
    if search_text:
        clauses.append(
            "(id LIKE ? OR display_name LIKE ? OR description LIKE ? OR reference_summary LIKE ?)"
        )
        token = f"%{search_text}%"
        params.extend([token, token, token, token])
    if category:
        clauses.append("category = ?")
        params.append(str(category).strip())
    if scene:
        clauses.append("scene = ?")
        params.append(str(scene).strip())
    if status:
        clauses.append("status = ?")
        params.append(str(status).strip().lower())

    where_sql = " AND ".join(clauses)
    cur.execute(f"SELECT COUNT(1) FROM audit_skill WHERE {where_sql}", params)
    total = int((cur.fetchone() or [0])[0] or 0)
    offset = max(page - 1, 0) * page_size
    cur.execute(
        f"""
        SELECT *
        FROM audit_skill
        WHERE {where_sql}
        ORDER BY sort_order ASC, updated_at DESC, created_at DESC, display_name ASC
        LIMIT ? OFFSET ?
        """,
        params + [page_size, offset],
    )
    rows = cur.fetchall()
    items = [
        _attach_skill_reference_meta(cur, _row_to_skill(dict(row)))
        for row in rows
    ]
    conn.close()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def get_admin_skill_detail(cfg: Dict[str, Any], skill_id: str) -> Optional[Dict[str, Any]]:
    ensure_audit_capabilities_seeded(cfg)
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("SELECT * FROM audit_skill WHERE id = ?", (skill_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return None
    item = _attach_skill_reference_meta(cur, _row_to_skill(dict(row)))
    item = _attach_skill_detail_meta(cur, item)
    conn.close()
    return item


def create_skill(
    cfg: Dict[str, Any],
    owner_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    ensure_audit_capabilities_seeded(cfg)
    now = _utc_now_iso()
    normalized = _normalize_skill_payload(payload)
    skill_id = _normalize_skill_id(
        payload.get("id"), normalized["display_name"])
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM audit_skill WHERE id = ?", (skill_id,))
    if cur.fetchone():
        conn.close()
        raise ValueError("skill id already exists")
    cur.execute(
        """
        INSERT INTO audit_skill(
            id, display_name, category, scene, description, owner_type, owner_id,
            visibility, status, source_url, reference_summary,
            input_schema_json, output_schema_json, config_schema_json, tags_json,
            sort_order, created_at, updated_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            skill_id,
            normalized["display_name"],
            normalized["category"],
            normalized["scene"],
            normalized["description"],
            "admin",
            owner_id,
            normalized["visibility"],
            normalized["status"],
            normalized["source_url"],
            normalized["reference_summary"],
            _json_text(normalized["input_schema"]),
            _json_text(normalized["output_schema"]),
            _json_text(normalized["config_schema"]),
            _json_text(normalized["tags"]),
            normalized["sort_order"],
            now,
            now,
        ),
    )
    detail_seed_item = {
        "id": skill_id,
        "display_name": normalized["display_name"],
        "category": normalized["category"],
        "scene": normalized["scene"],
        "description": normalized["description"],
        "source_url": normalized["source_url"],
        "reference_summary": normalized["reference_summary"],
        "tags_json": _json_text(normalized["tags"]),
    }
    detail_payload = _normalize_skill_detail_payload(
        payload,
        normalized,
        existing=_build_default_skill_detail(detail_seed_item),
    )
    _save_skill_detail(
        cur,
        skill_id,
        detail_payload,
        now,
    )
    conn.commit()
    cur.execute("SELECT * FROM audit_skill WHERE id = ?", (skill_id,))
    row = cur.fetchone()
    item = _attach_skill_reference_meta(cur, _row_to_skill(dict(row)))
    item = _attach_skill_detail_meta(cur, item)
    conn.close()
    return item


def update_skill(
    cfg: Dict[str, Any],
    skill_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    ensure_audit_capabilities_seeded(cfg)
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("SELECT * FROM audit_skill WHERE id = ?", (skill_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise ValueError("skill not found")
    existing = _row_to_skill(dict(row))
    normalized = _normalize_skill_payload(payload, existing=existing)
    refs = _get_skill_reference_counts(cur, skill_id)
    if normalized["status"] == "disabled" and refs["in_use"]:
        conn.close()
        raise ValueError(
            "cannot disable a skill that is referenced by templates or agents")
    now = _utc_now_iso()
    cur.execute(
        """
        UPDATE audit_skill
        SET display_name = ?, category = ?, scene = ?, description = ?, visibility = ?,
            status = ?, source_url = ?, reference_summary = ?, input_schema_json = ?,
            output_schema_json = ?, config_schema_json = ?, tags_json = ?, sort_order = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            normalized["display_name"],
            normalized["category"],
            normalized["scene"],
            normalized["description"],
            normalized["visibility"],
            normalized["status"],
            normalized["source_url"],
            normalized["reference_summary"],
            _json_text(normalized["input_schema"]),
            _json_text(normalized["output_schema"]),
            _json_text(normalized["config_schema"]),
            _json_text(normalized["tags"]),
            normalized["sort_order"],
            now,
            skill_id,
        ),
    )
    cur.execute("SELECT * FROM audit_skill WHERE id = ?", (skill_id,))
    saved_row = _row_to_skill(dict(cur.fetchone()))
    existing_detail = _attach_skill_detail_meta(cur, dict(saved_row))
    _save_skill_detail(
        cur,
        skill_id,
        _normalize_skill_detail_payload(
            payload, normalized, existing=existing_detail),
        now,
    )
    conn.commit()
    cur.execute("SELECT * FROM audit_skill WHERE id = ?", (skill_id,))
    saved = _attach_skill_reference_meta(
        cur, _row_to_skill(dict(cur.fetchone())))
    saved = _attach_skill_detail_meta(cur, saved)
    conn.close()
    return saved


def set_skill_status(
    cfg: Dict[str, Any],
    skill_id: str,
    status: str,
) -> Dict[str, Any]:
    status_value = str(status or "").strip().lower()
    if status_value not in {"active", "disabled"}:
        raise ValueError("status must be active or disabled")
    return update_skill(cfg, skill_id, {"status": status_value})


def delete_skill(cfg: Dict[str, Any], skill_id: str) -> Dict[str, Any]:
    ensure_audit_capabilities_seeded(cfg)
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("SELECT * FROM audit_skill WHERE id = ?", (skill_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise ValueError("skill not found")
    refs = _get_skill_reference_counts(cur, skill_id)
    if refs["in_use"]:
        conn.close()
        raise ValueError(
            "cannot delete a skill that is referenced by templates or agents")
    now = _utc_now_iso()
    cur.execute(
        "UPDATE audit_skill SET status = 'deleted', updated_at = ? WHERE id = ?",
        (now, skill_id),
    )
    conn.commit()
    cur.execute("SELECT * FROM audit_skill WHERE id = ?", (skill_id,))
    item = _attach_skill_reference_meta(
        cur, _row_to_skill(dict(cur.fetchone())))
    conn.close()
    return item
