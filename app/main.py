import os
import re
import json
import sys
import uuid
import time
import sqlite3
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pypdf import PdfReader
from docx import Document
import numpy as np
import onnxruntime as ort
from transformers import AutoTokenizer


def load_config():
    config_path = os.environ.get("APP_CONFIG", "config.json")
    if not os.path.exists(config_path):
        alt = os.path.join(os.path.dirname(__file__), "config.example.json")
        if os.path.exists(alt):
            with open(alt, "r", encoding="utf-8") as f:
                return json.load(f)
        raise RuntimeError("config.json not found")
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def ensure_dirs(cfg):
    for key in ["data_dir", "files_dir", "static_dir"]:
        d = cfg.get(key)
        if d and not os.path.exists(d):
            os.makedirs(d, exist_ok=True)


def setup_logging(cfg):
    level_name = str(cfg.get("log_level", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)
    log_dir = cfg.get("log_dir") or os.path.join(cfg.get("data_dir", "data"), "logs")
    os.makedirs(log_dir, exist_ok=True)
    logger = logging.getLogger("law_assistant")
    logger.setLevel(level)
    logger.handlers = []
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    logger.addHandler(stream)
    file_handler = RotatingFileHandler(
        os.path.join(log_dir, "app.log"),
        maxBytes=int(cfg.get("log_max_bytes", 5 * 1024 * 1024)),
        backupCount=int(cfg.get("log_backup_count", 3)),
        encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)
    logger.propagate = False
    return logger


def get_conn(cfg):
    conn = sqlite3.connect(cfg["db_path"])
    conn.row_factory = sqlite3.Row
    return conn


def init_db(cfg):
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS regulation(
        id TEXT PRIMARY KEY,
        title TEXT,
        doc_no TEXT,
        issuer TEXT,
        reg_type TEXT,
        status TEXT,
        version_group_id TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS regulation_version(
        id TEXT PRIMARY KEY,
        regulation_id TEXT,
        effective_date TEXT,
        expiry_date TEXT,
        is_current INTEGER,
        region TEXT,
        industry TEXT,
        source_file TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS article(
        id TEXT PRIMARY KEY,
        regulation_version_id TEXT,
        article_no TEXT,
        level_path TEXT,
        content TEXT,
        keywords TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ingest_job(
        id TEXT PRIMARY KEY,
        status TEXT,
        error TEXT,
        created_at TEXT,
        finished_at TEXT
    )
    """)
    cur.execute("""
    CREATE VIRTUAL TABLE IF NOT EXISTS article_fts
    USING fts5(content, article_id, regulation_version_id, tokenize='unicode61')
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS article_embedding(
        article_id TEXT PRIMARY KEY,
        lang TEXT,
        model_id TEXT,
        dim INTEGER,
        vec BLOB
    )
    """)
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


def extract_text(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".txt":
        with open(path, "rb") as f:
            data = f.read()
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("gbk", errors="ignore")
    if ext == ".docx":
        doc = Document(path)
        return "\n".join([p.text for p in doc.paragraphs])
    if ext == ".pdf":
        reader = PdfReader(path)
        pages = []
        for p in reader.pages:
            pages.append(p.extract_text() or "")
        return "\n".join(pages)
    raise ValueError("unsupported file type")


def split_articles(text):
    text = re.sub(r"\r\n", "\n", text)
    parts = re.split(r"(第[一二三四五六七八九十百千0-9]+条)", text)
    items = []
    if len(parts) >= 3:
        i = 1
        while i < len(parts) - 1:
            article_no = parts[i].strip()
            content = parts[i + 1].strip()
            if content:
                items.append((article_no, content))
            i += 2
    if not items:
        paras = [p.strip() for p in text.split("\n") if p.strip()]
        for idx, p in enumerate(paras, 1):
            items.append((f"段{idx}", p))
    return items

_embed_registry: Dict[str, Dict[str, Any]] = {}
_default_embed_lang = "zh"


def _resolve_path(p: str) -> str:
    if not p:
        return ""
    if os.path.isabs(p):
        return p
    return os.path.abspath(os.path.join(os.path.dirname(__file__), p))


def _default_instruction(lang: str) -> str:
    return "Represent this sentence for retrieving relevant passages:" if lang == "en" else "为这个句子生成表示以用于检索相关文章："


def _mean_pool(last_hidden: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
    mask = attention_mask.astype(np.float32)[..., None]
    summed = (last_hidden * mask).sum(axis=1)
    denom = np.clip(mask.sum(axis=1), 1e-9, None)
    return summed / denom


def _load_one_embedder(lang: str, p: Dict[str, Any]) -> bool:
    model_path = _resolve_path(str(p.get("embedding_model", "")))
    tokenizer_dir = _resolve_path(str(p.get("embedding_tokenizer_dir", "")))
    if not model_path or not os.path.exists(model_path):
        logger.warning("embedding_model_missing lang=%s path=%s", lang, model_path)
        return False
    if not tokenizer_dir or not os.path.exists(tokenizer_dir):
        logger.warning("embedding_tokenizer_missing lang=%s path=%s", lang, tokenizer_dir)
        return False
    so = ort.SessionOptions()
    so.intra_op_num_threads = int(p.get("embedding_threads", 2))
    sess = ort.InferenceSession(model_path, sess_options=so, providers=["CPUExecutionProvider"])
    tokenizer = AutoTokenizer.from_pretrained(tokenizer_dir, local_files_only=True, use_fast=True)
    prof = {
        "lang": lang,
        "model_id": str(p.get("embedding_model_id", "unknown")),
        "source": str(p.get("embedding_source", "local")),
        "model_path": model_path,
        "tokenizer_dir": tokenizer_dir,
        "max_len": int(p.get("embedding_max_seq_len", 512)),
        "pooling": str(p.get("embedding_pooling", "cls")).lower(),
        "query_instruction": str(p.get("embedding_query_instruction", _default_instruction(lang))).strip(),
        "inputs": [i.name for i in sess.get_inputs()],
        "sess": sess,
        "tokenizer": tokenizer,
    }
    _embed_registry[lang] = prof
    logger.info("embedding_ready lang=%s model_id=%s source=%s model=%s", lang, prof["model_id"], prof["source"], model_path)
    return True


def load_embedders(cfg):
    global _default_embed_lang
    _embed_registry.clear()
    _default_embed_lang = str(cfg.get("default_language", "zh")).lower()
    profiles = cfg.get("embedding_profiles")
    if not isinstance(profiles, dict) or not profiles:
        profiles = {
            _default_embed_lang: {
                "embedding_model": cfg.get("embedding_model", ""),
                "embedding_tokenizer_dir": cfg.get("embedding_tokenizer_dir", ""),
                "embedding_model_id": cfg.get("embedding_model_id", "unknown"),
                "embedding_source": cfg.get("embedding_source", "local"),
                "embedding_max_seq_len": cfg.get("embedding_max_seq_len", 512),
                "embedding_pooling": cfg.get("embedding_pooling", "cls"),
                "embedding_query_instruction": cfg.get("embedding_query_instruction", _default_instruction(_default_embed_lang)),
                "embedding_threads": cfg.get("embedding_threads", 2),
            }
        }
    ok = 0
    for lang, p in profiles.items():
        if isinstance(p, dict) and _load_one_embedder(str(lang).lower(), p):
            ok += 1
    return ok


def get_embed_profile(lang: Optional[str]):
    k = (lang or _default_embed_lang or "zh").lower()
    return _embed_registry.get(k) or _embed_registry.get(_default_embed_lang)


def compute_embedding(text: str, is_query: bool = False, lang: Optional[str] = None):
    prof = get_embed_profile(lang)
    if not prof:
        return None
    payload = text.strip()
    if is_query and prof["query_instruction"]:
        payload = f"{prof['query_instruction']}{payload}"
    encoded = prof["tokenizer"](payload, truncation=True, max_length=prof["max_len"], padding="max_length", return_tensors="np")
    feed = {}
    for k in prof["inputs"]:
        if k in encoded:
            feed[k] = encoded[k].astype(np.int64)
        elif k == "token_type_ids":
            feed[k] = np.zeros_like(encoded["input_ids"], dtype=np.int64)
    out = prof["sess"].run(None, feed)
    if not out:
        return None
    first = out[0]
    if first.ndim == 3:
        vec = _mean_pool(first.astype(np.float32), encoded.get("attention_mask", np.ones((first.shape[0], first.shape[1]), dtype=np.int64)).astype(np.int64))[0] if prof["pooling"] == "mean" else first[:, 0, :].astype(np.float32)[0]
    elif first.ndim == 2:
        vec = first.astype(np.float32)[0]
    else:
        return None
    norm = np.linalg.norm(vec) + 1e-9
    return vec / norm


def tokenize_query(q: str) -> List[str]:
    words = re.findall(r"[\u4e00-\u9fff]+", q)
    words += re.findall(r"[A-Za-z]+", q)
    words += re.findall(r"[0-9]+", q)
    return [w for w in words if len(w) >= 2][:10]


def best_sentence(text: str, tokens: List[str]) -> tuple[str, int]:
    sents = re.split(r"[。；;\n\r]+", text)
    best = ("", 0)
    for s in sents:
        score = sum(1 for t in tokens if t in s)
        if score > best[1]:
            best = (s.strip(), score)
    return best


def upsert_job(cfg, job_id, status, error=None, finished_at=None):
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("UPDATE ingest_job SET status=?, error=?, finished_at=? WHERE id=?",
                (status, error, finished_at, job_id))
    conn.commit()
    conn.close()


def insert_job(cfg, job_id):
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("INSERT INTO ingest_job(id,status,created_at) VALUES(?,?,?)",
                (job_id, "running", datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()


def create_regulation(cfg, title, doc_no, issuer, reg_type, status):
    rid = str(uuid.uuid4())
    version_group_id = str(uuid.uuid4())
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO regulation(id,title,doc_no,issuer,reg_type,status,version_group_id,created_at)
        VALUES(?,?,?,?,?,?,?,?)
    """, (rid, title, doc_no, issuer, reg_type, status, version_group_id, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()
    return rid


def create_version(cfg, regulation_id, effective_date, expiry_date, region, industry, source_file):
    vid = str(uuid.uuid4())
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO regulation_version(id,regulation_id,effective_date,expiry_date,is_current,region,industry,source_file,created_at)
        VALUES(?,?,?,?,?,?,?,?,?)
    """, (vid, regulation_id, effective_date, expiry_date, 1, region, industry, source_file, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()
    return vid


def insert_articles(cfg, version_id, items, job_id=None, language: str = "zh"):
    conn = get_conn(cfg)
    cur = conn.cursor()
    total = len(items)
    for idx, (article_no, content) in enumerate(items, 1):
        aid = str(uuid.uuid4())
        cur.execute("""
            INSERT INTO article(id,regulation_version_id,article_no,level_path,content,keywords)
            VALUES(?,?,?,?,?,?)
        """, (aid, version_id, article_no, article_no, content, ""))
        cur.execute("""
            INSERT INTO article_fts(content,article_id,regulation_version_id)
            VALUES(?,?,?)
        """, (content, aid, version_id))
        prof = get_embed_profile(language)
        v = compute_embedding(content, lang=language)
        if v is not None:
            cur.execute("""
                INSERT OR REPLACE INTO article_embedding(article_id, lang, model_id, dim, vec)
                VALUES(?,?,?,?,?)
            """, (aid, (prof or {}).get("lang", language), (prof or {}).get("model_id", "unknown"), int(v.shape[0]), v.tobytes()))
        if job_id and total > 0 and (idx % max(1, total // 10) == 0 or idx == total):
            upsert_job(cfg, job_id, "running")
    conn.commit()
    conn.close()


def process_import(cfg, job_id, file_path, title, doc_no, issuer, reg_type, status,
                   effective_date, expiry_date, region, industry, regulation_id, language):
    try:
        logger.info("import_start job_id=%s file=%s", job_id, file_path)
        text = extract_text(file_path)
        articles = split_articles(text)
        if not regulation_id:
            regulation_id = create_regulation(
                cfg, title, doc_no, issuer, reg_type, status)
        version_id = create_version(
            cfg, regulation_id, effective_date, expiry_date, region, industry, file_path)
        insert_articles(cfg, version_id, articles, language=language)
        logger.info("import_embedding_lang job_id=%s language=%s", job_id, language)
        upsert_job(cfg, job_id, "done", None, datetime.utcnow().isoformat())
        logger.info("import_done job_id=%s version_id=%s article_count=%s", job_id, version_id, len(articles))
    except Exception as e:
        upsert_job(cfg, job_id, "failed", str(
            e), datetime.utcnow().isoformat())
        logger.exception("import_failed job_id=%s error=%s", job_id, str(e))


class SearchQuery(BaseModel):
    query: str
    language: str = "zh"
    top_k: int = 10
    date: Optional[str] = None
    region: Optional[str] = None
    industry: Optional[str] = None
    use_semantic: bool = False
    semantic_weight: float = 0.6
    bm25_weight: float = 0.4
    candidate_size: int = 200


class EmbeddingRequest(BaseModel):
    text: str
    is_query: bool = False
    language: str = "zh"


app = FastAPI(title="Law Assistant POC")
cfg = load_config()
ensure_dirs(cfg)
logger = setup_logging(cfg)
init_db(cfg)
ensure_embedding_columns(cfg)
embedder_count = load_embedders(cfg)
logger.info("service_start db=%s embedding_ready=%s langs=%s", cfg.get("db_path"), embedder_count > 0, list(_embed_registry.keys()))

app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg.get("cors_allow_origins", ["*"]),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

static_dir = cfg.get("static_dir")
if static_dir and os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir, html=True), name="static")


@app.middleware("http")
async def access_log(request: Request, call_next):
    t0 = time.perf_counter()
    try:
        response = await call_next(request)
        ms = int((time.perf_counter() - t0) * 1000)
        logger.info("http %s %s status=%s cost_ms=%s", request.method, request.url.path, response.status_code, ms)
        return response
    except Exception:
        ms = int((time.perf_counter() - t0) * 1000)
        logger.exception("http %s %s status=500 cost_ms=%s", request.method, request.url.path, ms)
        raise


@app.get("/health")
def health():
    return {
        "status": "ok",
        "embedding_ready": len(_embed_registry) > 0,
        "embedding_default_language": _default_embed_lang,
        "embedding_languages": list(_embed_registry.keys())
    }


@app.get("/embeddings/info")
def embedding_info():
    models = {}
    for k, v in _embed_registry.items():
        models[k] = {
            "model_id": v.get("model_id"),
            "source": v.get("source"),
            "model_path": v.get("model_path"),
            "tokenizer_dir": v.get("tokenizer_dir"),
            "max_seq_len": v.get("max_len"),
            "pooling": v.get("pooling"),
            "inputs": v.get("inputs", [])
        }
    return {
        "ready": len(_embed_registry) > 0,
        "default_language": _default_embed_lang,
        "models": models
    }


@app.post("/embeddings/encode")
def encode_embedding(req: EmbeddingRequest):
    v = compute_embedding(req.text, is_query=req.is_query, lang=req.language)
    prof = get_embed_profile(req.language)
    if v is None or not prof:
        raise HTTPException(status_code=503, detail=f"embedding model not ready for language={req.language}")
    return {
        "dim": int(v.shape[0]),
        "is_query": req.is_query,
        "language": prof.get("lang"),
        "model_id": prof.get("model_id"),
        "vector": v.tolist()
    }


@app.post("/regulations/import")
async def import_regulation(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    title: str = Form(...),
    doc_no: str = Form(""),
    issuer: str = Form(""),
    reg_type: str = Form(""),
    status: str = Form("current"),
    effective_date: str = Form(""),
    expiry_date: str = Form(""),
    region: str = Form(""),
    industry: str = Form(""),
    regulation_id: str = Form(""),
    language: str = Form("zh")
):
    job_id = str(uuid.uuid4())
    insert_job(cfg, job_id)
    ext = os.path.splitext(file.filename)[1].lower()
    save_path = os.path.join(cfg["files_dir"], f"{job_id}{ext}")
    with open(save_path, "wb") as f:
        f.write(await file.read())
    background_tasks.add_task(
        process_import,
        cfg,
        job_id,
        save_path,
        title,
        doc_no,
        issuer,
        reg_type,
        status,
        effective_date,
        expiry_date,
        region,
        industry,
        regulation_id,
        language
    )
    logger.info("import_queued job_id=%s filename=%s language=%s", job_id, file.filename, language)
    return {"job_id": job_id}


@app.get("/regulations/import/{job_id}")
def import_status(job_id: str):
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("SELECT * FROM ingest_job WHERE id=?", (job_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="job not found")
    return dict(row)


@app.get("/regulations")
def list_regulations():
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("SELECT * FROM regulation ORDER BY created_at DESC")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


@app.get("/regulations/{regulation_id}/articles")
def list_articles(regulation_id: str, version_id: Optional[str] = None):
    conn = get_conn(cfg)
    cur = conn.cursor()
    if version_id:
        cur.execute("""
            SELECT a.* FROM article a
            WHERE a.regulation_version_id=?
            ORDER BY a.article_no
        """, (version_id,))
    else:
        cur.execute("""
            SELECT a.* FROM article a
            JOIN regulation_version v ON v.id=a.regulation_version_id
            WHERE v.regulation_id=?
            ORDER BY a.article_no
        """, (regulation_id,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


@app.post("/regulations/search")
def search(q: SearchQuery):
    if not q.query.strip():
        return []
    lang = (q.language or _default_embed_lang).lower()
    prof = get_embed_profile(lang)
    active_lang = (prof or {}).get("lang", lang)
    if q.use_semantic and not prof:
        raise HTTPException(status_code=503, detail=f"semantic search enabled but embedding model is not ready for language={lang}")
    logger.info("search_start query=%s lang=%s active_lang=%s top_k=%s semantic=%s model_id=%s", q.query[:80], lang, active_lang, q.top_k, q.use_semantic, (prof or {}).get("model_id", "none"))
    tokens = tokenize_query(q.query)
    conn = get_conn(cfg)
    cur = conn.cursor()
    candidate_n = max(q.top_k, q.candidate_size)

    bm_sql = """
    SELECT
      a.id as article_id,
      a.article_no,
      a.content,
      v.id as version_id,
      r.id as regulation_id,
      r.title,
      v.effective_date,
      v.expiry_date,
      v.region,
      v.industry,
      bm25(article_fts) as bm25_raw
    FROM article_fts
    JOIN article a ON a.id=article_fts.article_id
    JOIN regulation_version v ON v.id=article_fts.regulation_version_id
    JOIN regulation r ON r.id=v.regulation_id
    WHERE article_fts MATCH ?
    """
    bm_params = [q.query]
    if q.region:
        bm_sql += " AND (v.region='' OR v.region=?)"
        bm_params.append(q.region)
    if q.industry:
        bm_sql += " AND (v.industry='' OR v.industry=?)"
        bm_params.append(q.industry)
    if q.date:
        bm_sql += " AND (v.effective_date='' OR v.effective_date<=?) AND (v.expiry_date='' OR v.expiry_date>=?)"
        bm_params.extend([q.date, q.date])
    bm_sql += " ORDER BY bm25_raw LIMIT ?"
    bm_params.append(candidate_n)

    cur.execute(bm_sql, bm_params)
    bm_rows = [dict(r) for r in cur.fetchall()]
    logger.info("bm25_candidates query=%s count=%s", q.query[:80], len(bm_rows))
    for idx, r in enumerate(bm_rows):
        r["bm25_score"] = 1.0 - (idx / max(1, len(bm_rows)))

    merged = {r["article_id"]: r for r in bm_rows}

    if q.use_semantic:
        qe = compute_embedding(q.query, is_query=True, lang=active_lang)
        if qe is not None:
            sem_sql = """
            SELECT
              ae.article_id,
              ae.vec,
              a.article_no,
              a.content,
              v.id as version_id,
              r.id as regulation_id,
              r.title,
              v.effective_date,
              v.expiry_date,
              v.region,
              v.industry
            FROM article_embedding ae
            JOIN article a ON a.id=ae.article_id
            JOIN regulation_version v ON v.id=a.regulation_version_id
            JOIN regulation r ON r.id=v.regulation_id
            WHERE ae.lang=?
            """
            sem_params = [active_lang]
            if q.region:
                sem_sql += " AND (v.region='' OR v.region=?)"
                sem_params.append(q.region)
            if q.industry:
                sem_sql += " AND (v.industry='' OR v.industry=?)"
                sem_params.append(q.industry)
            if q.date:
                sem_sql += " AND (v.effective_date='' OR v.effective_date<=?) AND (v.expiry_date='' OR v.expiry_date>=?)"
                sem_params.extend([q.date, q.date])
            cur.execute(sem_sql, sem_params)
            sem_rows = []
            for row in cur.fetchall():
                v = np.frombuffer(row[1], dtype=np.float32)
                sim = float(np.dot(qe, v))
                sem_rows.append({
                    "article_id": row[0],
                    "article_no": row[2],
                    "content": row[3],
                    "version_id": row[4],
                    "regulation_id": row[5],
                    "title": row[6],
                    "effective_date": row[7],
                    "expiry_date": row[8],
                    "region": row[9],
                    "industry": row[10],
                    "semantic_raw": sim
                })
            sem_rows.sort(key=lambda x: x["semantic_raw"], reverse=True)
            sem_rows = sem_rows[:candidate_n]
            logger.info("semantic_candidates query=%s count=%s", q.query[:80], len(sem_rows))
            for idx, r in enumerate(sem_rows):
                r["semantic_score"] = 1.0 - (idx / max(1, len(sem_rows)))
                found = merged.get(r["article_id"])
                if found:
                    found["semantic_raw"] = r["semantic_raw"]
                    found["semantic_score"] = r["semantic_score"]
                else:
                    merged[r["article_id"]] = r
        else:
            logger.warning("semantic_enabled_but_embedder_unavailable query=%s lang=%s", q.query[:80], lang)

    rows = list(merged.values())
    for r in rows:
        r.setdefault("bm25_score", 0.0)
        r.setdefault("semantic_score", 0.0)
        r.setdefault("semantic_raw", 0.0)
        if q.use_semantic:
            r["final_score"] = q.bm25_weight * r["bm25_score"] + q.semantic_weight * r["semantic_score"]
        else:
            r["final_score"] = r["bm25_score"]
    rows.sort(key=lambda x: x.get("final_score", 0), reverse=True)
    rows = rows[:q.top_k]

    conn.close()
    for r in rows:
        r["effective_status"] = "active"
        if q.date and r.get("effective_date") and r["effective_date"] > q.date:
            r["effective_status"] = "not_effective"
        if q.date and r.get("expiry_date") and r["expiry_date"] < q.date:
            r["effective_status"] = "expired"
        ans, score = best_sentence(r["content"], tokens) if tokens else ("", 0)
        r["answer"] = ans
        r["answer_score"] = score
        r["match_tokens"] = [t for t in tokens if t in r["content"]]
        r["citation_id"] = f"{r['regulation_id']}:{r['version_id']}:{r['article_id']}"
    logger.info("search_done query=%s results=%s", q.query[:80], len(rows))
    return rows


if __name__ == "__main__":
    if "--init" in sys.argv:
        init_db(cfg)
        sys.exit(0)
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000)
