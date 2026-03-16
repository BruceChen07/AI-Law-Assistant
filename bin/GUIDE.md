# User Management Scripts Guide (bin)

This guide explains how to manage users from the `bin` folder using Windows PowerShell.

- Database: `d:\Workspace\AI-Law-Assistant\data\app.db` (`users` table)
- Config resolution: `app/config.json` if present, otherwise `app/config.example.json`
- All scripts auto-append the repository root to `sys.path`, so they can run from any working directory

> Tip: After changing roles or passwords, log out from the web UI and log in again to refresh the cached user info.

---

## Quick Start (Windows PowerShell)

```powershell
# Optional: activate your virtualenv if you use one
# .\.venv\Scripts\Activate.ps1

# List all users
python d:\Workspace\AI-Law-Assistant\bin\list_users.py

# Create a normal user
python d:\Workspace\AI-Law-Assistant\bin\create_user.py --username alice --email alice@example.com --password AlicePass123!

# Create or promote admin (resets password to admin123 by default)
python d:\Workspace\AI-Law-Assistant\bin\create_admin.py

# Promote existing user to admin (example)
python d:\Workspace\AI-Law-Assistant\bin\user_modify.py --username admin --role admin

# Reset password (example)
python d:\Workspace\AI-Law-Assistant\bin\user_modify.py --username admin --password NewStrongPass123!
```

---

## Scripts Overview

### 1) list_users.py
- Purpose: List users, optionally filtered by username
- Output columns: `id | username | email | role | is_active | created_at`

Arguments:
- `--username <str>`: Optional. Filter by username

Examples:
```powershell
python d:\Workspace\AI-Law-Assistant\bin\list_users.py
python d:\Workspace\AI-Law-Assistant\bin\list_users.py --username admin
```

### 2) create_admin.py
- Purpose: Create a new admin user if not exists, or promote an existing user to admin
- Behavior:
  - If the user exists: set `role='admin'`, update `email` (if provided)
  - By default also reset password to the provided value (default `admin123`)
  - Use `--no-reset-password` to avoid resetting the password for existing users

Arguments:
- `--username <str>`: Default `admin`
- `--email <str>`: Default `admin@example.com`
- `--password <str>`: Default `admin123`
- `--no-reset-password`: Do not reset password if the user already exists

Examples:
```powershell
# Create admin (or promote if exists) and reset password to admin123
python d:\Workspace\AI-Law-Assistant\bin\create_admin.py

# Specify fields explicitly
python d:\Workspace\AI-Law-Assistant\bin\create_admin.py --username admin --email admin@example.com --password admin123

# Promote without resetting password
python d:\Workspace\AI-Law-Assistant\bin\create_admin.py --username admin --no-reset-password
```

### 3) create_user.py
- Purpose: Create a regular user (default role `user`). You may also create an admin by specifying `--role admin`, but prefer `create_admin.py` for admin setup.

Arguments:
- `--username <str>`: Required
- `--email <str>`: Required
- `--password <str>`: Required
- `--role <user|admin>`: Optional, default `user`

Examples:
```powershell
# Create a normal user
python d:\Workspace\AI-Law-Assistant\bin\create_user.py --username alice --email alice@example.com --password AlicePass123!

# Create an admin user (prefer using create_admin.py)
python d:\Workspace\AI-Law-Assistant\bin\create_user.py --username bob --email bob@example.com --password BobPass123! --role admin
```

### 4) user_modify.py
- Purpose: Update a user's fields (role/password/email/activation)

Arguments:
- `--username <str>`: Required
- `--role <user|admin>`: Optional
- `--password <str>`: Optional (will be hashed)
- `--email <str>`: Optional
- `--activate`: Optional, set `is_active=1`
- `--deactivate`: Optional, set `is_active=0`

Examples:
```powershell
# Promote to admin
python d:\Workspace\AI-Law-Assistant\bin\user_modify.py --username admin --role admin

# Reset password
python d:\Workspace\AI-Law-Assistant\bin\user_modify.py --username admin --password NewStrongPass123!

# Update email
python d:\Workspace\AI-Law-Assistant\bin\user_modify.py --username admin --email admin@example.com

# Activate / Deactivate user
python d:\Workspace\AI-Law-Assistant\bin\user_modify.py --username alice --activate
python d:\Workspace\AI-Law-Assistant\bin\user_modify.py --username alice --deactivate
```

---

## Troubleshooting

- `ModuleNotFoundError: No module named 'app'`
  - These scripts already prepend the repo root to `sys.path`. If you still see this error:
    - Ensure you are running with Python that can access the repo (try from the repo root)
    - Or set `PYTHONPATH` to the repository root

- Database not found / empty
  - Initialize DB:
    ```powershell
    python -m app.main --init
    ```
  - Then run:
    ```powershell
    python d:\Workspace\AI-Law-Assistant\bin\list_users.py
    ```

- Web UI still shows wrong role after changes
  - Click “Logout” in the UI and log in again to refresh the cached user data.

---

## Internals (for reference)

- DB schema and helpers: `app/core/database.py`
- Password hashing and auth helpers: `app/core/auth.py`
- Admin permission check: `app/api/dependencies.py` (requires `role == 'admin'`)