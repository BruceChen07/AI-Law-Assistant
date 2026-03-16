from app.core.database import get_conn
from app.core.config import get_config
import os
import sys
import uuid
import mimetypes
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


def guess_mime(path):
    typ, _ = mimetypes.guess_type(path)
    return typ or "application/octet-stream"


def main():
    cfg = get_config()
    conn = get_conn(cfg)
    cur = conn.cursor()

    # pick admin id if exists, otherwise first user
    cur.execute("SELECT id FROM users WHERE username='admin'")
    row = cur.fetchone()
    if row:
        user_id = row["id"]
    else:
        cur.execute("SELECT id FROM users ORDER BY created_at LIMIT 1")
        r = cur.fetchone()
        if not r:
            print("No user found; please create a user first.")
            conn.close()
            return
        user_id = r["id"]

    # existing document file_paths to avoid duplicates
    cur.execute("SELECT file_path FROM documents")
    existing = {r["file_path"] for r in cur.fetchall()}

    # source files from regulation_version
    cur.execute(
        "SELECT id, source_file FROM regulation_version WHERE source_file IS NOT NULL AND TRIM(source_file)<>''")
    rows = cur.fetchall()

    inserted = 0
    for r in rows:
        path = r["source_file"]
        if not path or path in existing:
            continue
        if not os.path.exists(path):
            continue
        doc_id = str(uuid.uuid4())
        filename = os.path.basename(path)
        size = os.path.getsize(path)
        mime = guess_mime(path)
        cur.execute("""
            INSERT INTO documents (id, filename, original_filename, file_path, file_size, mime_type, user_id, title, category, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (doc_id, filename, filename, path, size, mime, user_id, None, None, "active", datetime.utcnow().isoformat()))
        inserted += 1

    conn.commit()
    conn.close()
    print(f"Backfill done. Inserted {inserted} document(s).")


if __name__ == "__main__":
    main()
