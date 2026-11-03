"""User administration."""
from __future__ import annotations

from app.api.dependencies import require_admin
from app.core.auth import get_all_users, log_audit, update_user_role
from app.core.config import get_config
from app.core.database import get_conn
from fastapi import APIRouter, Depends, HTTPException, Request
from app.api.routers.admin.schemas import (
    DeleteResponse,
)

router = APIRouter()


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


@router.delete("/users/{user_id}", response_model=DeleteResponse)
def delete_user(
    user_id: str,
    request: Request,
    current_user: dict = Depends(require_admin)
):
    if str(current_user.get("id")) == str(user_id):
        raise HTTPException(
            status_code=400, detail="Cannot delete current user")

    conn = get_conn(get_config())
    cur = conn.cursor()

    cur.execute("SELECT id, username, role FROM users WHERE id = ?", (user_id,))
    target = cur.fetchone()
    if not target:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")

    if str(target["role"]) == "admin":
        cur.execute(
            "SELECT COUNT(*) as cnt FROM users WHERE role = 'admin' AND is_active = 1 AND id <> ?",
            (user_id,),
        )
        other_admin_count = int(cur.fetchone()["cnt"])
        if other_admin_count <= 0:
            conn.close()
            raise HTTPException(
                status_code=400, detail="Cannot delete the last active admin")

    cur.execute(
        "SELECT COUNT(*) as cnt FROM documents WHERE user_id = ? AND status = 'active'",
        (user_id,),
    )
    active_doc_count = int(cur.fetchone()["cnt"])
    if active_doc_count > 0:
        conn.close()
        raise HTTPException(
            status_code=400, detail="Please delete this user's active documents first")

    cur.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    cur.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()

    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    log_audit(
        current_user["id"], "delete", "user", user_id,
        ip_address, user_agent, f"Deleted user: {target['username']}"
    )

    return DeleteResponse(message="User deleted successfully", deleted_id=user_id)
