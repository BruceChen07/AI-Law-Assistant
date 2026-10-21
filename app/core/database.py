import sqlite3

from app.core.migrations import apply_migrations


def get_conn(cfg):
    conn = sqlite3.connect(cfg["db_path"], timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _normalize_lang_tag(lang: str, default: str = "zh") -> str:
    s = str(lang or "").strip().lower()
    if s.startswith("en"):
        return "en"
    if s.startswith("zh"):
        return "zh"
    return default


def get_rag_db_path(cfg, lang: str):
    paths = cfg.get("rag_db_paths") if isinstance(
        cfg.get("rag_db_paths"), dict) else {}
    norm_lang = _normalize_lang_tag(lang, default="zh")
    p = str(paths.get(norm_lang) or "").strip()
    if p:
        return p
    fallback = str(cfg.get("db_path") or "").strip()
    return fallback


def get_rag_conn(cfg, lang: str):
    path = get_rag_db_path(cfg, lang)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init_db(cfg):
    conn = get_conn(cfg)
    cur = conn.cursor()
    apply_migrations(cur)
    conn.commit()
    conn.close()


def ensure_article_dsl_columns(cfg):
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(article)")
    cols = {r[1] for r in cur.fetchall()}
    if "rule_type" not in cols:
        cur.execute("ALTER TABLE article ADD COLUMN rule_type TEXT")
    if "conditions_json" not in cols:
        cur.execute("ALTER TABLE article ADD COLUMN conditions_json TEXT")
    if "constraints_json" not in cols:
        cur.execute("ALTER TABLE article ADD COLUMN constraints_json TEXT")
    conn.commit()
    conn.close()


def ensure_embedding_columns(cfg):
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(article_embedding)")
    cols = {r[1] for r in cur.fetchall()}
    if "lang" not in cols:
        cur.execute("ALTER TABLE article_embedding ADD COLUMN lang TEXT")
    if "model_id" not in cols:
        cur.execute("ALTER TABLE article_embedding ADD COLUMN model_id TEXT")
    conn.commit()
    conn.close()
