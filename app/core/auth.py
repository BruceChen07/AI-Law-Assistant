import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
import jwt
from passlib.context import CryptContext
from app.core.database import get_conn

SECRET_KEY = os.environ.get(
    "JWT_SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    # Ensure tokens minted within the same second are still unique.
    to_encode.update({"exp": expire, "iat": now, "jti": str(uuid.uuid4())})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def decode_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError as e:
        print(f"Token expired: {e}")
        return None
    except jwt.InvalidTokenError as e:
        print(f"Invalid token: {e}")
        return None


def get_current_user(token: str) -> Optional[dict]:
    payload = decode_token(token)
    if not payload:
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    user = get_user_by_id(user_id)
    return user


def require_admin(token: str) -> dict:
    user = get_current_user(token)
    if not user:
        raise ValueError("Invalid authentication")
    if user.get("role") != "admin":
        raise ValueError("Admin privileges required")
    return user


def create_user(username: str, email: str, password: str, role: str = "user") -> str:
    from app.core.config import get_config

    user_id = str(uuid.uuid4())
    password_hash = hash_password(password)
    now = datetime.now(timezone.utc).isoformat()

    cfg = get_config()
    conn = get_conn(cfg)
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO users (id, username, email, password_hash, role, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, username, email, password_hash, role, now))
        conn.commit()
    except Exception as e:
        conn.close()
        raise e
    conn.close()
    return user_id


def get_user_by_username(username: str) -> Optional[dict]:
    from app.core.config import get_config

    cfg = get_config()
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username = ?", (username,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_id(user_id: str) -> Optional[dict]:
    from app.core.config import get_config

    cfg = get_config()
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, username, email, role, created_at, is_active FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def authenticate_user(username: str, password: str) -> Optional[dict]:
    user = get_user_by_username(username)
    if not user:
        return None
    if not verify_password(password, user["password_hash"]):
        return None
    return user


def create_session(user_id: str, token: str, ip_address: str = None, user_agent: str = None):
    from app.core.config import get_config

    session_id = str(uuid.uuid4())
    expires_at = (datetime.now(timezone.utc) +
                  timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)).isoformat()
    now = datetime.now(timezone.utc).isoformat()

    cfg = get_config()
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO sessions (id, user_id, token, ip_address, user_agent, expires_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (session_id, user_id, token, ip_address, user_agent, expires_at, now))
    conn.commit()
    conn.close()
    return session_id


def delete_session(token: str):
    from app.core.config import get_config

    cfg = get_config()
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.commit()
    conn.close()


def get_all_users() -> list:
    from app.core.config import get_config

    cfg = get_config()
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, username, email, role, created_at, is_active FROM users ORDER BY created_at DESC")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_user_role(user_id: str, role: str) -> bool:
    from app.core.config import get_config

    cfg = get_config()
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("UPDATE users SET role = ?, updated_at = ? WHERE id = ?",
                (role, datetime.now(timezone.utc).isoformat(), user_id))
    conn.commit()
    affected = cur.rowcount
    conn.close()
    return affected > 0


def update_user_password(user_id: str, password: str) -> bool:
    from app.core.config import get_config

    cfg = get_config()
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
        (hash_password(password), datetime.now(timezone.utc).isoformat(), user_id),
    )
    conn.commit()
    affected = cur.rowcount
    conn.close()
    return affected > 0


def log_audit(user_id: str, action: str, resource_type: str = None, resource_id: str = None,
              ip_address: str = None, user_agent: str = None, details: str = None):
    from app.core.config import get_config

    log_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    cfg = get_config()
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO audit_logs (id, user_id, action, resource_type, resource_id, ip_address, user_agent, details, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (log_id, user_id, action, resource_type, resource_id, ip_address, user_agent, details, now))
    conn.commit()
    conn.close()


def count_admin_users() -> int:
    """Count users with admin role."""
    from app.core.config import get_config

    cfg = get_config()
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1")
    row = cur.fetchone()
    conn.close()
    return row[0] if row else 0


def ensure_default_admin(cfg: dict = None, logger=None) -> dict:
    """
    Ensure at least one admin user exists in the database.
    Creates a default admin account if no admin users are found.
    Returns dict with info about what was done.
    """
    if cfg is None:
        from app.core.config import get_config
        cfg = get_config()

    default_username = "admin"
    default_password = "adminMars654321"
    default_email = "admin@local.internal"
    placeholder_emails = {default_email, "admin@example.com"}

    admin_count = count_admin_users()

    try:
        existing_default = get_user_by_username(default_username)
        if (
            admin_count > 0
            and existing_default
            and existing_default.get("role") == "admin"
            and existing_default.get("is_active", 1)
            and str(existing_default.get("email") or "").strip().lower() in placeholder_emails
            and not verify_password(default_password, existing_default["password_hash"])
        ):
            update_user_password(existing_default["id"], default_password)
            msg = (
                f"Synchronized default admin password for '{default_username}' "
                f"to the current bootstrap default"
            )
            if logger:
                logger.warning("bootstrap_admin %s", msg)
            return {"action": "sync_password", "username": default_username, "message": msg}

        if admin_count > 0:
            return {"action": "skip", "reason": f"{admin_count} admin user(s) already exist"}

        # No admin exists - try to upgrade known users first, then create default
        # Priority 1: upgrade existing user "bruce" to admin
        bruce = get_user_by_username("bruce")
        if bruce:
            update_user_role(bruce["id"], "admin")
            msg = f"Upgraded existing user 'bruce' to admin role"
            if logger:
                logger.warning("bootstrap_admin %s", msg)
            return {"action": "upgrade", "username": "bruce", "message": msg}

        # Priority 2: if user "admin" exists but not admin role, upgrade it
        if existing_default:
            update_user_role(existing_default["id"], "admin")
            if not verify_password(default_password, existing_default["password_hash"]):
                update_user_password(existing_default["id"], default_password)
            msg = f"Upgraded existing user '{default_username}' to admin role"
            if logger:
                logger.warning("bootstrap_admin %s", msg)
            return {"action": "upgrade", "username": default_username, "message": msg}

        # Priority 3: create brand new default admin account
        create_user(default_username, default_email,
                    default_password, role="admin")
        msg = (f"Created default admin account: username='{default_username}' "
               f"password='{default_password}' - CHANGE THIS PASSWORD IMMEDIATELY")
        if logger:
            logger.warning("bootstrap_admin %s", msg)
        return {"action": "create", "username": default_username, "message": msg}

    except Exception as e:
        err_msg = f"Failed to ensure default admin: {e}"
        if logger:
            logger.error("bootstrap_admin %s", err_msg)
        return {"action": "error", "message": err_msg}
