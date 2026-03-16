import os
import sys
import uuid
import argparse
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app.core.config import get_config
from app.core.database import get_conn, init_db
from app.core.auth import hash_password


def main():
    p = argparse.ArgumentParser(description="Create or promote admin user")
    p.add_argument("--username", default="admin")
    p.add_argument("--email", default="admin@example.com")
    p.add_argument("--password", default="admin123")
    p.add_argument("--no-reset-password", action="store_true")
    args = p.parse_args()

    cfg = get_config()
    init_db(cfg)
    conn = get_conn(cfg)
    cur = conn.cursor()

    cur.execute("SELECT id FROM users WHERE username = ?", (args.username,))
    row = cur.fetchone()
    now = datetime.utcnow().isoformat()
    if row:
        sets = ["role = 'admin'", "updated_at = ?"]
        params = [now]
        if not args.no_reset_password:
            sets.append("password_hash = ?")
            params.insert(0, hash_password(args.password))
        if args.email:
            sets.insert(0, "email = ?")
            params.insert(0, args.email)
        params.append(args.username)
        sql = f"UPDATE users SET {', '.join(sets)} WHERE username = ?"
        cur.execute(sql, tuple(params))
        conn.commit()
        print(f"User '{args.username}' promoted to admin")
    else:
        user_id = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO users (id, username, email, password_hash, role, created_at) VALUES (?, ?, ?, ?, 'admin', ?)",
            (user_id, args.username, args.email,
             hash_password(args.password), now),
        )
        conn.commit()
        print(f"Admin user '{args.username}' created")
    conn.close()


if __name__ == "__main__":
    main()
