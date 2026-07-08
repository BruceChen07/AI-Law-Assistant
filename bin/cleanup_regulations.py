import argparse
import os
import sys
from typing import Dict, List, Set, Tuple

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app.core.config import get_config
from app.core.database import get_conn


def _table_exists(cur, table_name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table_name,),
    )
    return cur.fetchone() is not None


def _load_targets(cfg: dict, job_ids: List[str], clear_all: bool) -> Tuple[List[str], List[str]]:
    with get_conn(cfg) as conn:
        cur = conn.cursor()
        if clear_all:
            cur.execute(
                """
                SELECT DISTINCT l.file_id, l.file_path
                FROM upload_log l
                LEFT JOIN regulation_version v ON v.source_file = l.file_path
                LEFT JOIN documents d ON d.file_path = l.file_path
                WHERE v.id IS NOT NULL OR d.category = 'legal'
                """
            )
            rows = cur.fetchall()
            ids = [str(r["file_id"]) for r in rows]
            paths = [str(r["file_path"]) for r in rows]
            return ids, paths

        if not job_ids:
            return [], []

        placeholders = ",".join("?" for _ in job_ids)
        cur.execute(
            f"SELECT file_id, file_path FROM upload_log WHERE file_id IN ({placeholders})",
            tuple(job_ids),
        )
        rows = cur.fetchall()
        ids = [str(r["file_id"]) for r in rows]
        paths = [str(r["file_path"]) for r in rows]
        return ids, paths


def _delete_from_chroma(cfg: dict, file_ids: List[str]) -> int:
    if not file_ids:
        return 0
    try:
        import chromadb
    except Exception:
        return 0
    data_dir = str(cfg.get("data_dir", "./data"))
    chroma_path = os.path.join(data_dir, "chroma_db")
    if not os.path.exists(chroma_path):
        return 0
    collection_name = str(
        cfg.get("vector_collection_name", "regulation_chunks"))
    deleted = 0
    client = chromadb.PersistentClient(path=chroma_path)
    collection = client.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},
    )
    for file_id in file_ids:
        collection.delete(where={"file_id": file_id})
        deleted += 1
    return deleted


def _cleanup_sqlite(cfg: dict, file_ids: List[str], file_paths: List[str], dry_run: bool) -> Dict[str, int]:
    if not file_paths:
        return {
            "versions": 0,
            "articles": 0,
            "embeddings": 0,
            "fts": 0,
            "regulations": 0,
            "documents": 0,
            "upload_logs": 0,
            "sqlite_vectors": 0,
        }
    stats = {
        "versions": 0,
        "articles": 0,
        "embeddings": 0,
        "fts": 0,
        "regulations": 0,
        "documents": 0,
        "upload_logs": 0,
        "sqlite_vectors": 0,
    }
    with get_conn(cfg) as conn:
        cur = conn.cursor()
        placeholders_path = ",".join("?" for _ in file_paths)
        cur.execute(
            f"SELECT id, regulation_id FROM regulation_version WHERE source_file IN ({placeholders_path})",
            tuple(file_paths),
        )
        versions = cur.fetchall()
        version_ids = [str(v["id"]) for v in versions]
        regulation_ids = list({str(v["regulation_id"])
                              for v in versions if v["regulation_id"]})

        if version_ids:
            placeholders_ver = ",".join("?" for _ in version_ids)
            cur.execute(
                f"SELECT id FROM article WHERE regulation_version_id IN ({placeholders_ver})",
                tuple(version_ids),
            )
            article_ids = [str(r["id"]) for r in cur.fetchall()]
        else:
            article_ids = []

        stats["versions"] = len(version_ids)
        stats["articles"] = len(article_ids)

        if article_ids:
            placeholders_article = ",".join("?" for _ in article_ids)
            cur.execute(
                f"SELECT COUNT(*) as cnt FROM article_embedding WHERE article_id IN ({placeholders_article})",
                tuple(article_ids),
            )
            stats["embeddings"] = int(cur.fetchone()["cnt"])
        if version_ids:
            placeholders_ver = ",".join("?" for _ in version_ids)
            cur.execute(
                f"SELECT COUNT(*) as cnt FROM article_fts WHERE regulation_version_id IN ({placeholders_ver})",
                tuple(version_ids),
            )
            stats["fts"] = int(cur.fetchone()["cnt"])

        if file_paths:
            cur.execute(
                f"SELECT COUNT(*) as cnt FROM documents WHERE category='legal' AND file_path IN ({placeholders_path})",
                tuple(file_paths),
            )
            stats["documents"] = int(cur.fetchone()["cnt"])

        if file_ids:
            placeholders_id = ",".join("?" for _ in file_ids)
            cur.execute(
                f"SELECT COUNT(*) as cnt FROM upload_log WHERE file_id IN ({placeholders_id})",
                tuple(file_ids),
            )
            stats["upload_logs"] = int(cur.fetchone()["cnt"])
            if _table_exists(cur, "vector_chunks"):
                cur.execute(
                    f"SELECT COUNT(*) as cnt FROM vector_chunks WHERE file_id IN ({placeholders_id})",
                    tuple(file_ids),
                )
                stats["sqlite_vectors"] = int(cur.fetchone()["cnt"])

        removable_regulations: Set[str] = set()
        for regulation_id in regulation_ids:
            cur.execute(
                "SELECT COUNT(*) as cnt FROM regulation_version WHERE regulation_id = ?",
                (regulation_id,),
            )
            total_versions = int(cur.fetchone()["cnt"])
            cur.execute(
                f"SELECT COUNT(*) as cnt FROM regulation_version WHERE regulation_id = ? AND source_file IN ({placeholders_path})",
                (regulation_id, *file_paths),
            )
            target_versions = int(cur.fetchone()["cnt"])
            if total_versions == target_versions:
                removable_regulations.add(regulation_id)
        stats["regulations"] = len(removable_regulations)

        if dry_run:
            return stats

        if file_ids:
            placeholders_id = ",".join("?" for _ in file_ids)
            if _table_exists(cur, "vector_chunks_fts"):
                cur.execute(
                    f"DELETE FROM vector_chunks_fts WHERE file_id IN ({placeholders_id})",
                    tuple(file_ids),
                )
            if _table_exists(cur, "vector_chunks"):
                cur.execute(
                    f"DELETE FROM vector_chunks WHERE file_id IN ({placeholders_id})",
                    tuple(file_ids),
                )

        if article_ids:
            placeholders_article = ",".join("?" for _ in article_ids)
            cur.execute(
                f"DELETE FROM article_embedding WHERE article_id IN ({placeholders_article})",
                tuple(article_ids),
            )
            cur.execute(
                f"DELETE FROM article_fts WHERE article_id IN ({placeholders_article})",
                tuple(article_ids),
            )

        if version_ids:
            placeholders_ver = ",".join("?" for _ in version_ids)
            cur.execute(
                f"DELETE FROM article_fts WHERE regulation_version_id IN ({placeholders_ver})",
                tuple(version_ids),
            )
            cur.execute(
                f"DELETE FROM article WHERE regulation_version_id IN ({placeholders_ver})",
                tuple(version_ids),
            )
            cur.execute(
                f"DELETE FROM regulation_version WHERE id IN ({placeholders_ver})",
                tuple(version_ids),
            )

        if removable_regulations:
            reg_ids = list(removable_regulations)
            placeholders_reg = ",".join("?" for _ in reg_ids)
            cur.execute(
                f"DELETE FROM regulation WHERE id IN ({placeholders_reg})",
                tuple(reg_ids),
            )

        cur.execute(
            f"DELETE FROM documents WHERE category='legal' AND file_path IN ({placeholders_path})",
            tuple(file_paths),
        )
        if file_ids:
            placeholders_id = ",".join("?" for _ in file_ids)
            cur.execute(
                f"DELETE FROM upload_log WHERE file_id IN ({placeholders_id})",
                tuple(file_ids),
            )

        conn.commit()
    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Cleanup regulation data by job_id or all imported regulations")
    parser.add_argument("--job-id", action="append", default=[],
                        help="Upload log file_id/job_id from /regulations/import")
    parser.add_argument("--all", action="store_true",
                        help="Cleanup all imported regulation data")
    parser.add_argument("--delete-files", action="store_true",
                        help="Also delete local source files")
    parser.add_argument("--dry-run", action="store_true",
                        help="Only show what would be deleted")
    parser.add_argument("--yes", action="store_true",
                        help="Confirm dangerous operation when using --all")
    args = parser.parse_args()

    if not args.all and not args.job_id:
        raise SystemExit("Please provide --job-id or use --all")
    if args.all and not args.yes:
        raise SystemExit("Using --all requires --yes")

    cfg = get_config()
    file_ids, file_paths = _load_targets(cfg, args.job_id, args.all)
    if not file_ids or not file_paths:
        print("No matching regulation upload records found.")
        return

    stats = _cleanup_sqlite(cfg, file_ids=file_ids,
                            file_paths=file_paths, dry_run=args.dry_run)
    chroma_deleted = 0
    if not args.dry_run:
        chroma_deleted = _delete_from_chroma(cfg, file_ids)
        if args.delete_files:
            removed = 0
            for p in set(file_paths):
                if os.path.exists(p):
                    try:
                        os.remove(p)
                        removed += 1
                    except Exception:
                        pass
            print(f"deleted_files={removed}")

    print(f"matched_file_ids={len(file_ids)}")
    print(f"target_versions={stats['versions']}")
    print(f"target_articles={stats['articles']}")
    print(f"target_embeddings={stats['embeddings']}")
    print(f"target_fts={stats['fts']}")
    print(f"target_regulations={stats['regulations']}")
    print(f"target_documents={stats['documents']}")
    print(f"target_upload_logs={stats['upload_logs']}")
    print(f"target_sqlite_vectors={stats['sqlite_vectors']}")
    print(f"target_chroma_file_groups={chroma_deleted}")
    print(f"dry_run={args.dry_run}")


if __name__ == "__main__":
    main()
