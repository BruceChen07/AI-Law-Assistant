import os
import sys
import argparse
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app.core.config import get_config
from app.core.database import get_conn
from app.core.auth import hash_password


def main():
    p = argparse.ArgumentParser(description="Modify user fields")
    p.add_argument("--username", required=True)
    p.add_argument("--role", choices=["user", "admin"])
    p.add_argument("--password")
    p.add_argument("--email")
    p.add_argument("--activate", action="store_true")
    p.add_argument("--deactivate", action="store_true")
    args = p.parse_args()

    updates = []
    params = []
    if args.role:
        updates.append("role = ?")
        params.append(args.role)
    if args.password:
        updates.append("password_hash = ?")
        params.append(hash_password(args.password))
    if args.email:
        updates.append("email = ?")
        params.append(args.email)
    if args.activate and not args.deactivate:
        updates.append("is_active = 1")
    if args.deactivate and not args.activate:
        updates.append("is_active = 0")

    if not updates:
        print("No fields to update")
        return

    updates.append("updated_at = ?")
    params.append(datetime.utcnow().isoformat())
    params.append(args.username)

    conn = get_conn(get_config())
    cur = conn.cursor()
    cur.execute(
        f"UPDATE users SET {', '.join(updates)} WHERE username = ?", tuple(params))
    conn.commit()
    print(f"User '{args.username}' updated, affected={cur.rowcount}")
    conn.close()


if __name__ == "__main__":
    main()
