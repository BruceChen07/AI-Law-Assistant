from app.core.config import get_config
from app.core.database import init_db
from app.core.auth import create_user as auth_create_user, get_user_by_username
import os
import sys
import argparse

# Add repo root to sys.path so imports work when running by absolute path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


def main():
    p = argparse.ArgumentParser(
        description="Create a user (default role: user)")
    p.add_argument("--username", required=True)
    p.add_argument("--email", required=True)
    p.add_argument("--password", required=True)
    p.add_argument("--role", choices=["user", "admin"],
                   default="user", help="User role (default: user)")
    args = p.parse_args()

    cfg = get_config()
    init_db(cfg)

    existing = get_user_by_username(args.username)
    if existing:
        print(
            f"User '{args.username}' already exists (role={existing.get('role')}). Use bin\\user_modify.py to update.")
        sys.exit(1)

    user_id = auth_create_user(
        args.username, args.email, args.password, role=args.role)
    print(
        f"User created: id={user_id}, username={args.username}, role={args.role}")


if __name__ == "__main__":
    main()
