import os
import uuid
import json
import csv
import io
from datetime import datetime, timedelta
from typing import Optional, List, Literal
from fastapi import APIRouter, HTTPException, Depends, Query, Request, Response
from pydantic import BaseModel
from app.core.auth import get_all_users, update_user_role, log_audit
from app.api.dependencies import require_admin
from app.core.database import get_conn
from app.core.config import get_config, update_config_patch
from app.core.llm import LLMService

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ============ Document Models ============
class DocumentResponse(BaseModel):
    id: str
    filename: str
    original_filename: str
    file_size: int
    mime_type: Optional[str]
    user_id: str
    username: Optional[str]
    title: Optional[str]
    category: Optional[str]
    status: str
    created_at: str


class DocumentListResponse(BaseModel):
    items: List[DocumentResponse]
    total: int
    page: int
    page_size: int


class DeleteResponse(BaseModel):
    message: str
    deleted_id: str


# ============ Stats Models ============
class StatsResponse(BaseModel):
    total_documents: int
    total_users: int
    total_size: int
    documents_by_category: dict
    documents_by_user: dict


class LLMConfigResponse(BaseModel):
    provider: str
    api_base: str
    model: str
    temperature: float
    max_tokens: int
    timeout: int
    headers: dict
    has_api_key: bool


class LLMConfigUpdate(BaseModel):
    provider: str
    api_base: str
    api_key: str
    model: str
    temperature: float
    max_tokens: int
    timeout: int
    headers: dict


class UIConfigResponse(BaseModel):
    show_citation_source: bool
    default_theme: Literal["dark", "light"]


class UIConfigUpdate(BaseModel):
    show_citation_source: bool
    default_theme: Optional[str] = None


class LLMTestRequest(BaseModel):
    prompt: Optional[str] = None


class LLMTestResponse(BaseModel):
    ok: bool
    prompt: str
    answer: str


class TokenUsageTotals(BaseModel):
    input_tokens: int
    output_tokens: int
    total_tokens: int
    request_count: int


class TokenUsageSeriesItem(BaseModel):
    bucket: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    request_count: int


class TokenUsageRankingItem(BaseModel):
    key: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    request_count: int


class TokenUsageAlert(BaseModel):
    level: str
    reason: str
    value: int
    threshold: int
    bucket: Optional[str] = None
    meta: Optional[dict] = None


class TokenUsageResponse(BaseModel):
    range_start: str
    range_end: str
    granularity: str
    rank_by: str
    totals: TokenUsageTotals
    series: List[TokenUsageSeriesItem]
    rankings: List[TokenUsageRankingItem]
    alerts: List[TokenUsageAlert]
    last_updated: str


def _clean_text(v: str) -> str:
    s = str(v or "").strip()
    s = s.strip("`").strip('"').strip("'").strip()
    return s


def _normalize_llm_payload(payload: dict) -> dict:
    data = dict(payload or {})
    data["api_base"] = _clean_text(data.get("api_base", ""))
    data["model"] = _clean_text(data.get("model", ""))
    data["api_key"] = _clean_text(data.get("api_key", ""))
    if not isinstance(data.get("headers"), dict):
        data["headers"] = {}
    return data


def _validate_llm_config(payload: dict):
    api_base = str(payload.get("api_base", "")).strip()
    model = str(payload.get("model", "")).strip()
    if not api_base.startswith("http"):
        raise HTTPException(
            status_code=400, detail="api_base must be a valid http(s) url")
    if not model:
        raise HTTPException(status_code=400, detail="model is required")
    temperature = float(payload.get("temperature", 0.2))
    if temperature < 0 or temperature > 2:
        raise HTTPException(status_code=400, detail="temperature out of range")
    max_tokens = int(payload.get("max_tokens", 2048))
    if max_tokens <= 0:
        raise HTTPException(
            status_code=400, detail="max_tokens must be positive")
    timeout = int(payload.get("timeout", 60))
    if timeout <= 0:
        raise HTTPException(status_code=400, detail="timeout must be positive")


def _normalize_theme(v: Optional[str]) -> str:
    s = str(v or "").strip().lower()
    return "light" if s == "light" else "dark"


def _get_ui_config(cfg: dict) -> dict:
    ui_cfg = cfg.get("ui_config") if isinstance(
        cfg.get("ui_config"), dict) else {}
    return {
        "show_citation_source": bool(ui_cfg.get("show_citation_source", False)),
        "default_theme": _normalize_theme(ui_cfg.get("default_theme")),
    }


def _llm_trace_dir(cfg: dict) -> str:
    trace_dir = str(cfg.get("llm_trace_dir") or "").strip()
    if not trace_dir:
        base = str(cfg.get("data_dir") or "").strip()
        trace_dir = os.path.join(
            base, "llm_interactions") if base else os.path.abspath("llm_interactions")
    return os.path.abspath(trace_dir)


def _parse_dt(v: Optional[str], fallback: Optional[datetime] = None) -> datetime:
    if not v:
        return fallback or datetime.utcnow()
    s = str(v).strip()
    if not s:
        return fallback or datetime.utcnow()
    if len(s) == 10 and s.count("-") == 2:
        try:
            return datetime.fromisoformat(s)
        except Exception:
            return fallback or datetime.utcnow()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return fallback or datetime.utcnow()


def _bucket_key(dt: datetime, granularity: str) -> str:
    if granularity == "hour":
        return dt.strftime("%Y-%m-%d %H:00")
    if granularity == "week":
        iso_year, iso_week, _ = dt.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
    return dt.strftime("%Y-%m-%d")


def _iter_trace_rows(trace_dir: str, start_dt: datetime, end_dt: datetime, max_rows: int):
    day = datetime(start_dt.year, start_dt.month, start_dt.day)
    end_day = datetime(end_dt.year, end_dt.month, end_dt.day)
    count = 0
    while day <= end_day:
        day_dir = os.path.join(trace_dir, day.strftime("%Y-%m-%d"))
        file_path = os.path.join(day_dir, "llm_trace.jsonl")
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    for line in f:
                        if max_rows and count >= max_rows:
                            return
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            row = json.loads(line)
                        except Exception:
                            continue
                        ts = _parse_dt(row.get("ts"))
                        if ts < start_dt or ts > end_dt:
                            continue
                        count += 1
                        yield row
            except Exception:
                pass
        day += timedelta(days=1)


# ============ Document CRUD ============
@router.get("/documents", response_model=DocumentListResponse)
def list_documents(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    search: Optional[str] = None,
    category: Optional[str] = None,
    user_id: Optional[str] = None,
    current_user: dict = Depends(require_admin)
):
    conn = get_conn(get_config())
    cur = conn.cursor()

    where_clauses = ["d.status = 'active'"]
    params = []

    if search:
        where_clauses.append("(d.original_filename LIKE ? OR d.title LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])

    if category:
        if category == "legal":
            where_clauses.append(
                "(d.category = ? OR ((d.category IS NULL OR TRIM(d.category) = '') AND EXISTS (SELECT 1 FROM regulation_version v WHERE v.source_file = d.file_path)))"
            )
            params.append(category)
        else:
            where_clauses.append("d.category = ?")
            params.append(category)

    if user_id:
        where_clauses.append("d.user_id = ?")
        params.append(user_id)

    where_sql = " AND ".join(where_clauses)

    cur.execute(f"""
        SELECT COUNT(*) as total FROM documents d WHERE {where_sql}
    """, params)
    total = cur.fetchone()[0]

    offset = (page - 1) * page_size
    cur.execute(f"""
        SELECT d.*, u.username 
        FROM documents d 
        LEFT JOIN users u ON d.user_id = u.id
        WHERE {where_sql}
        ORDER BY d.created_at DESC
        LIMIT ? OFFSET ?
    """, params + [page_size, offset])

    rows = cur.fetchall()
    conn.close()

    items = []
    for row in rows:
        items.append(DocumentResponse(
            id=row["id"],
            filename=row["filename"],
            original_filename=row["original_filename"],
            file_size=row["file_size"],
            mime_type=row["mime_type"],
            user_id=row["user_id"],
            username=row["username"],
            title=row["title"],
            category=row["category"],
            status=row["status"],
            created_at=row["created_at"]
        ))

    return DocumentListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size
    )


@router.delete("/documents/{doc_id}", response_model=DeleteResponse)
def delete_document(
    doc_id: str,
    request: Request,
    current_user: dict = Depends(require_admin)
):
    conn = get_conn(get_config())
    cur = conn.cursor()

    cur.execute("SELECT * FROM documents WHERE id = ?", (doc_id,))
    doc = cur.fetchone()

    if not doc:
        conn.close()
        raise HTTPException(status_code=404, detail="Document not found")

    cur.execute("UPDATE documents SET status = 'deleted', deleted_at = ? WHERE id = ?",
                (datetime.utcnow().isoformat(), doc_id))
    conn.commit()
    conn.close()

    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    log_audit(
        current_user["id"], "delete", "document", doc_id,
        ip_address, user_agent, f"Deleted document: {doc['original_filename']}"
    )

    return DeleteResponse(message="Document deleted successfully", deleted_id=doc_id)


# ============ User Management ============
@router.get("/users")
def list_users(current_user: dict = Depends(require_admin)):
    users = get_all_users()
    return users


@router.put("/users/{user_id}/role")
def update_role(
    user_id: str,
    role: str,
    request: Request,
    current_user: dict = Depends(require_admin)
):
    if role not in ["user", "admin"]:
        raise HTTPException(status_code=400, detail="Invalid role")

    success = update_user_role(user_id, role)
    if not success:
        raise HTTPException(status_code=404, detail="User not found")

    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    log_audit(
        current_user["id"], "update_role", "user", user_id,
        ip_address, user_agent, f"Changed role to {role}"
    )

    return {"message": f"Role updated to {role}"}


# ============ Statistics ============
@router.get("/stats", response_model=StatsResponse)
def get_stats(current_user: dict = Depends(require_admin)):
    conn = get_conn(get_config())
    cur = conn.cursor()

    cur.execute(
        "SELECT COUNT(*) as cnt, COALESCE(SUM(file_size), 0) as total_size FROM documents WHERE status = 'active'")
    doc_stats = cur.fetchone()

    cur.execute("SELECT COUNT(*) as cnt FROM users")
    user_count = cur.fetchone()[0]

    cur.execute("""
        SELECT COALESCE(category, 'uncategorized') as cat, COUNT(*) as cnt 
        FROM documents WHERE status = 'active' GROUP BY category
    """)
    by_category = {row["cat"]: row["cnt"] for row in cur.fetchall()}

    cur.execute("""
        SELECT u.username, COUNT(d.id) as cnt 
        FROM users u LEFT JOIN documents d ON u.id = d.user_id AND d.status = 'active'
        GROUP BY u.id
    """)
    by_user = {row["username"]: row["cnt"] for row in cur.fetchall()}

    conn.close()

    return StatsResponse(
        total_documents=doc_stats["cnt"],
        total_users=user_count,
        total_size=doc_stats["total_size"],
        documents_by_category=by_category,
        documents_by_user=by_user
    )


@router.get("/llm-config", response_model=LLMConfigResponse)
def get_llm_config(current_user: dict = Depends(require_admin)):
    cfg = get_config()
    llm_cfg = cfg.get("llm_config") or {}
    return LLMConfigResponse(
        provider=str(llm_cfg.get("provider", "")),
        api_base=str(llm_cfg.get("api_base", "")),
        model=str(llm_cfg.get("model", "")),
        temperature=float(llm_cfg.get("temperature", 0.2)),
        max_tokens=int(llm_cfg.get("max_tokens", 2048)),
        timeout=int(llm_cfg.get("timeout", 60)),
        headers=llm_cfg.get("headers") if isinstance(
            llm_cfg.get("headers"), dict) else {},
        has_api_key=bool(llm_cfg.get("api_key"))
    )


@router.put("/llm-config", response_model=LLMConfigResponse)
def update_llm_config(payload: LLMConfigUpdate, request: Request, current_user: dict = Depends(require_admin)):
    data = _normalize_llm_payload(payload.dict())
    _validate_llm_config(data)
    current = get_config().get("llm_config") or {}
    if not data.get("api_key"):
        data["api_key"] = _clean_text(current.get("api_key", ""))
    cfg = update_config_patch({"llm_config": data})
    if hasattr(request.app.state, "llm"):
        request.app.state.llm.cfg = cfg
    llm_cfg = cfg.get("llm_config") or {}
    return LLMConfigResponse(
        provider=str(llm_cfg.get("provider", "")),
        api_base=str(llm_cfg.get("api_base", "")),
        model=str(llm_cfg.get("model", "")),
        temperature=float(llm_cfg.get("temperature", 0.2)),
        max_tokens=int(llm_cfg.get("max_tokens", 2048)),
        timeout=int(llm_cfg.get("timeout", 60)),
        headers=llm_cfg.get("headers") if isinstance(
            llm_cfg.get("headers"), dict) else {},
        has_api_key=bool(llm_cfg.get("api_key"))
    )


@router.get("/ui-config", response_model=UIConfigResponse)
def get_ui_config(current_user: dict = Depends(require_admin)):
    cfg = get_config()
    ui_cfg = _get_ui_config(cfg)
    return UIConfigResponse(**ui_cfg)


@router.put("/ui-config", response_model=UIConfigResponse)
def update_ui_config(payload: UIConfigUpdate, current_user: dict = Depends(require_admin)):
    cfg_current = get_config()
    current_ui = cfg_current.get("ui_config") if isinstance(
        cfg_current.get("ui_config"), dict) else {}
    next_theme = _normalize_theme(
        payload.default_theme if payload.default_theme is not None else current_ui.get("default_theme"))
    data = {
        "ui_config": {
            "show_citation_source": bool(payload.show_citation_source),
            "default_theme": next_theme,
        }
    }
    cfg = update_config_patch(data)
    ui_cfg = _get_ui_config(cfg)
    return UIConfigResponse(**ui_cfg)


@router.get("/token-usage", response_model=TokenUsageResponse)
def get_token_usage(
    start: Optional[str] = None,
    end: Optional[str] = None,
    granularity: Literal["hour", "day", "week"] = "day",
    rank_by: Literal["file_path", "stage", "model"] = "file_path",
    top_n: int = Query(10, ge=1, le=50),
    max_rows: int = Query(50000, ge=1000, le=200000),
    alert_total_tokens: int = Query(12000, ge=1000, le=200000),
    alert_bucket_tokens: int = Query(80000, ge=1000, le=1000000),
    current_user: dict = Depends(require_admin)
):
    cfg = get_config()
    trace_dir = _llm_trace_dir(cfg)
    end_dt = _parse_dt(end, datetime.utcnow())
    start_dt = _parse_dt(start, end_dt - timedelta(days=7))
    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt
    if (end_dt - start_dt).days > 31:
        start_dt = end_dt - timedelta(days=31)
    series_map = {}
    ranking_map = {}
    totals = {"input_tokens": 0, "output_tokens": 0,
              "total_tokens": 0, "request_count": 0}
    alerts = []
    for row in _iter_trace_rows(trace_dir, start_dt, end_dt, max_rows):
        usage = row.get("usage") if isinstance(row.get("usage"), dict) else {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or (
            prompt_tokens + completion_tokens))
        if total_tokens <= 0:
            continue
        totals["input_tokens"] += prompt_tokens
        totals["output_tokens"] += completion_tokens
        totals["total_tokens"] += total_tokens
        totals["request_count"] += 1
        ts = _parse_dt(row.get("ts"))
        bucket = _bucket_key(ts, granularity)
        agg = series_map.setdefault(bucket, {
                                    "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "request_count": 0})
        agg["input_tokens"] += prompt_tokens
        agg["output_tokens"] += completion_tokens
        agg["total_tokens"] += total_tokens
        agg["request_count"] += 1
        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        if rank_by == "stage":
            key = str(meta.get("stage") or "unknown")
        elif rank_by == "model":
            key = str(row.get("model") or "unknown")
        else:
            key = str(meta.get("file_path") or meta.get(
                "document_id") or "unknown")
        r = ranking_map.setdefault(
            key, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "request_count": 0})
        r["input_tokens"] += prompt_tokens
        r["output_tokens"] += completion_tokens
        r["total_tokens"] += total_tokens
        r["request_count"] += 1
        if total_tokens >= alert_total_tokens:
            alerts.append({
                "level": "warning",
                "reason": "single_request_tokens_high",
                "value": total_tokens,
                "threshold": alert_total_tokens,
                "bucket": bucket,
                "meta": {"model": row.get("model"), "stage": meta.get("stage"), "file_path": meta.get("file_path")}
            })
    series = [
        TokenUsageSeriesItem(
            bucket=b,
            input_tokens=v["input_tokens"],
            output_tokens=v["output_tokens"],
            total_tokens=v["total_tokens"],
            request_count=v["request_count"]
        )
        for b, v in sorted(series_map.items(), key=lambda x: x[0])
    ]
    for item in series:
        if item.total_tokens >= alert_bucket_tokens:
            alerts.append({
                "level": "critical",
                "reason": "bucket_tokens_high",
                "value": item.total_tokens,
                "threshold": alert_bucket_tokens,
                "bucket": item.bucket
            })
    rankings = [
        TokenUsageRankingItem(
            key=k,
            input_tokens=v["input_tokens"],
            output_tokens=v["output_tokens"],
            total_tokens=v["total_tokens"],
            request_count=v["request_count"]
        )
        for k, v in sorted(ranking_map.items(), key=lambda x: x[1]["total_tokens"], reverse=True)[:top_n]
    ]
    return TokenUsageResponse(
        range_start=start_dt.isoformat(),
        range_end=end_dt.isoformat(),
        granularity=granularity,
        rank_by=rank_by,
        totals=TokenUsageTotals(**totals),
        series=series,
        rankings=rankings,
        alerts=[TokenUsageAlert(**a) for a in alerts][:100],
        last_updated=datetime.utcnow().isoformat()
    )


@router.get("/token-usage/csv")
def export_token_usage_csv(
    start: Optional[str] = None,
    end: Optional[str] = None,
    granularity: Literal["hour", "day", "week"] = "day",
    max_rows: int = Query(50000, ge=1000, le=200000),
    current_user: dict = Depends(require_admin)
):
    cfg = get_config()
    trace_dir = _llm_trace_dir(cfg)
    end_dt = _parse_dt(end, datetime.utcnow())
    start_dt = _parse_dt(start, end_dt - timedelta(days=7))
    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt
    if (end_dt - start_dt).days > 31:
        start_dt = end_dt - timedelta(days=31)
    series_map = {}
    for row in _iter_trace_rows(trace_dir, start_dt, end_dt, max_rows):
        usage = row.get("usage") if isinstance(row.get("usage"), dict) else {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or (
            prompt_tokens + completion_tokens))
        if total_tokens <= 0:
            continue
        ts = _parse_dt(row.get("ts"))
        bucket = _bucket_key(ts, granularity)
        agg = series_map.setdefault(bucket, {
                                    "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "request_count": 0})
        agg["input_tokens"] += prompt_tokens
        agg["output_tokens"] += completion_tokens
        agg["total_tokens"] += total_tokens
        agg["request_count"] += 1
    output = []
    output.append(["bucket", "input_tokens", "output_tokens",
                  "total_tokens", "request_count"])
    for b, v in sorted(series_map.items(), key=lambda x: x[0]):
        output.append([b, v["input_tokens"], v["output_tokens"],
                      v["total_tokens"], v["request_count"]])
    csv_buf = io.StringIO()
    writer = csv.writer(csv_buf)
    for row in output:
        writer.writerow(row)
    content = csv_buf.getvalue()
    headers = {"Content-Disposition": "attachment; filename=token_usage.csv"}
    return Response(content=content, media_type="text/csv", headers=headers)


@router.post("/llm-test", response_model=LLMTestResponse)
def test_llm(payload: Optional[LLMTestRequest] = None, request: Request = None, current_user: dict = Depends(require_admin)):
    cfg = get_config()
    llm = request.app.state.llm if request is not None and hasattr(
        request.app.state, "llm") else LLMService(cfg)
    prompt = ""
    if payload:
        prompt = str(payload.prompt or "").strip()
    if not prompt:
        prompt = "你是什么模型？"
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": prompt}
    ]
    try:
        answer, _ = llm.chat(messages)
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"llm test failed: {str(e)}")
    return LLMTestResponse(ok=True, prompt=prompt, answer=answer)
