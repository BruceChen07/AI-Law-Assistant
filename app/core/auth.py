import os
import uuid
from datetime import datetime, timedelta
from typing import Optional
import jwt
from passlib.context import CryptContext
from app.core.database import get_conn

SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
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
    now = datetime.utcnow().isoformat()
    
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
    cur.execute("SELECT id, username, email, role, created_at, is_active FROM users WHERE id = ?", (user_id,))
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
    expires_at = (datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)).isoformat()
    now = datetime.utcnow().isoformat()
    
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
    cur.execute("SELECT id, username, email, role, created_at, is_active FROM users ORDER BY created_at DESC")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_user_role(user_id: str, role: str) -> bool:
    from app.core.config import get_config
    
    cfg = get_config()
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("UPDATE users SET role = ?, updated_at = ? WHERE id = ?", 
                (role, datetime.utcnow().isoformat(), user_id))
    conn.commit()
    affected = cur.rowcount
    conn.close()
    return affected > 0


def delete_user(user_id: str) -> bool:
    from app.core.config import get_config
    
    cfg = get_config()
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    affected = cur.rowcount
    conn.close()
    return affected > 0


def log_audit(user_id: str, action: str, resource_type: str = None, resource_id: str = None, 
              ip_address: str = None, user_agent: str = None, details: str = None):
    from app.core.config import get_config
    
    log_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    
    cfg = get_config()
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO audit_logs (id, user_id, action, resource_type, resource_id, ip_address, user_agent, details, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (log_id, user_id, action, resource_type, resource_id, ip_address, user_agent, details, now))
    conn.commit()
    conn.close()