import os
import uuid
from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends, Query, Request
from pydantic import BaseModel
from app.core.auth import get_all_users, update_user_role, log_audit
from app.api.dependencies import require_admin
from app.core.database import get_conn
from app.core.config import get_config, update_config_patch

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ============ Document Models ============
class DocumentResponse(BaseModel):
    id: str
    filename: str
    original_filename: str
    file_size: int
    mime_type: Optional[str]
    user_id: str
    username: Optional[str]
    title: Optional[str]
    category: Optional[str]
    status: str
    created_at: str


class DocumentListResponse(BaseModel):
    items: List[DocumentResponse]
    total: int
    page: int
    page_size: int


class DeleteResponse(BaseModel):
    message: str
    deleted_id: str


# ============ Stats Models ============
class StatsResponse(BaseModel):
    total_documents: int
    total_users: int
    total_size: int
    documents_by_category: dict
    documents_by_user: dict


class LLMConfigResponse(BaseModel):
    provider: str
    api_base: str
    model: str
    temperature: float
    max_tokens: int
    timeout: int
    headers: dict
    has_api_key: bool


class LLMConfigUpdate(BaseModel):
    provider: str
    api_base: str
    api_key: str
    model: str
    temperature: float
    max_tokens: int
    timeout: int
    headers: dict


def _validate_llm_config(payload: dict):
    api_base = str(payload.get("api_base", "")).strip()
    model = str(payload.get("model", "")).strip()
    if not api_base.startswith("http"):
        raise HTTPException(status_code=400, detail="api_base must be a valid http(s) url")
    if not model:
        raise HTTPException(status_code=400, detail="model is required")
    temperature = float(payload.get("temperature", 0.2))
    if temperature < 0 or temperature > 2:
        raise HTTPException(status_code=400, detail="temperature out of range")
    max_tokens = int(payload.get("max_tokens", 2048))
    if max_tokens <= 0:
        raise HTTPException(status_code=400, detail="max_tokens must be positive")
    timeout = int(payload.get("timeout", 60))
    if timeout <= 0:
        raise HTTPException(status_code=400, detail="timeout must be positive")


# ============ Document CRUD ============
@router.get("/documents", response_model=DocumentListResponse)
def list_documents(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    search: Optional[str] = None,
    category: Optional[str] = None,
    user_id: Optional[str] = None,
    current_user: dict = Depends(require_admin)
):
    conn = get_conn(get_config())
    cur = conn.cursor()

    where_clauses = ["d.status = 'active'"]
    params = []

    if search:
        where_clauses.append("(d.original_filename LIKE ? OR d.title LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])

    if category:
        where_clauses.append("d.category = ?")
        params.append(category)

    if user_id:
        where_clauses.append("d.user_id = ?")
        params.append(user_id)

    where_sql = " AND ".join(where_clauses)

    cur.execute(f"""
        SELECT COUNT(*) as total FROM documents d WHERE {where_sql}
    """, params)
    total = cur.fetchone()[0]

    offset = (page - 1) * page_size
    cur.execute(f"""
        SELECT d.*, u.username 
        FROM documents d 
        LEFT JOIN users u ON d.user_id = u.id
        WHERE {where_sql}
        ORDER BY d.created_at DESC
        LIMIT ? OFFSET ?
    """, params + [page_size, offset])

    rows = cur.fetchall()
    conn.close()

    items = []
    for row in rows:
        items.append(DocumentResponse(
            id=row["id"],
            filename=row["filename"],
            original_filename=row["original_filename"],
            file_size=row["file_size"],
            mime_type=row["mime_type"],
            user_id=row["user_id"],
            username=row["username"],
            title=row["title"],
            category=row["category"],
            status=row["status"],
            created_at=row["created_at"]
        ))

    return DocumentListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size
    )


@router.delete("/documents/{doc_id}", response_model=DeleteResponse)
def delete_document(
    doc_id: str,
    request: Request,
    current_user: dict = Depends(require_admin)
):
    conn = get_conn(get_config())
    cur = conn.cursor()

    cur.execute("SELECT * FROM documents WHERE id = ?", (doc_id,))
    doc = cur.fetchone()

    if not doc:
        conn.close()
        raise HTTPException(status_code=404, detail="Document not found")

    cur.execute("UPDATE documents SET status = 'deleted', deleted_at = ? WHERE id = ?",
                (datetime.utcnow().isoformat(), doc_id))
    conn.commit()
    conn.close()

    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    log_audit(
        current_user["id"], "delete", "document", doc_id,
        ip_address, user_agent, f"Deleted document: {doc['original_filename']}"
    )

    return DeleteResponse(message="Document deleted successfully", deleted_id=doc_id)


# ============ User Management ============
@router.get("/users")
def list_users(current_user: dict = Depends(require_admin)):
    users = get_all_users()
    return users


@router.put("/users/{user_id}/role")
def update_role(
    user_id: str,
    role: str,
    request: Request,
    current_user: dict = Depends(require_admin)
):
    if role not in ["user", "admin"]:
        raise HTTPException(status_code=400, detail="Invalid role")

    success = update_user_role(user_id, role)
    if not success:
        raise HTTPException(status_code=404, detail="User not found")

    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    log_audit(
        current_user["id"], "update_role", "user", user_id,
        ip_address, user_agent, f"Changed role to {role}"
    )

    return {"message": f"Role updated to {role}"}


# ============ Statistics ============
@router.get("/stats", response_model=StatsResponse)
def get_stats(current_user: dict = Depends(require_admin)):
    conn = get_conn(get_config())
    cur = conn.cursor()

    cur.execute(
        "SELECT COUNT(*) as cnt, COALESCE(SUM(file_size), 0) as total_size FROM documents WHERE status = 'active'")
    doc_stats = cur.fetchone()

    cur.execute("SELECT COUNT(*) as cnt FROM users")
    user_count = cur.fetchone()[0]

    cur.execute("""
        SELECT COALESCE(category, 'uncategorized') as cat, COUNT(*) as cnt 
        FROM documents WHERE status = 'active' GROUP BY category
    """)
    by_category = {row["cat"]: row["cnt"] for row in cur.fetchall()}

    cur.execute("""
        SELECT u.username, COUNT(d.id) as cnt 
        FROM users u LEFT JOIN documents d ON u.id = d.user_id AND d.status = 'active'
        GROUP BY u.id
    """)
    by_user = {row["username"]: row["cnt"] for row in cur.fetchall()}

    conn.close()

    return StatsResponse(
        total_documents=doc_stats["cnt"],
        total_users=user_count,
        total_size=doc_stats["total_size"],
        documents_by_category=by_category,
        documents_by_user=by_user
    )


@router.get("/llm-config", response_model=LLMConfigResponse)
def get_llm_config(current_user: dict = Depends(require_admin)):
    cfg = get_config()
    llm_cfg = cfg.get("llm_config") or {}
    return LLMConfigResponse(
        provider=str(llm_cfg.get("provider", "")),
        api_base=str(llm_cfg.get("api_base", "")),
        model=str(llm_cfg.get("model", "")),
        temperature=float(llm_cfg.get("temperature", 0.2)),
        max_tokens=int(llm_cfg.get("max_tokens", 2048)),
        timeout=int(llm_cfg.get("timeout", 60)),
        headers=llm_cfg.get("headers") if isinstance(llm_cfg.get("headers"), dict) else {},
        has_api_key=bool(llm_cfg.get("api_key"))
    )


@router.put("/llm-config", response_model=LLMConfigResponse)
def update_llm_config(payload: LLMConfigUpdate, current_user: dict = Depends(require_admin)):
    data = payload.dict()
    _validate_llm_config(data)
    current = get_config().get("llm_config") or {}
    if not data.get("api_key"):
        data["api_key"] = current.get("api_key", "")
    cfg = update_config_patch({"llm_config": data})
    llm_cfg = cfg.get("llm_config") or {}
    return LLMConfigResponse(
        provider=str(llm_cfg.get("provider", "")),
        api_base=str(llm_cfg.get("api_base", "")),
        model=str(llm_cfg.get("model", "")),
        temperature=float(llm_cfg.get("temperature", 0.2)),
        max_tokens=int(llm_cfg.get("max_tokens", 2048)),
        timeout=int(llm_cfg.get("timeout", 60)),
        headers=llm_cfg.get("headers") if isinstance(llm_cfg.get("headers"), dict) else {},
        has_api_key=bool(llm_cfg.get("api_key"))
    )
