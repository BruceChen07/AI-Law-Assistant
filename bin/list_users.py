import os
import sys
import argparse

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app.core.config import get_config
from app.core.database import get_conn


def main():
    p = argparse.ArgumentParser(description="List users")
    p.add_argument("--username", help="Filter by username")
    args = p.parse_args()

    conn = get_conn(get_config())
    cur = conn.cursor()
    if args.username:
        cur.execute(
            "SELECT id, username, email, role, is_active, created_at FROM users WHERE username = ?", (args.username,))
    else:
        cur.execute(
            "SELECT id, username, email, role, is_active, created_at FROM users ORDER BY created_at DESC")
    rows = cur.fetchall()
    for r in rows:
        try:
            d = dict(r)
        except Exception:
            d = {"id": r[0], "username": r[1], "email": r[2], "role": r[3],
                 "is_active": r[4], "created_at": r[5] if len(r) > 5 else ""}
        print(f"{d['id']} | {d['username']} | {d.get('email', '')} | {d['role']} | {d.get('is_active', 1)} | {d.get('created_at', '')}")
    conn.close()


if __name__ == "__main__":
    main()
