"""Token usage reporting and CSV export."""
from __future__ import annotations

import csv
import io

from app.api.dependencies import require_admin
from app.core.config import get_config
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Query, Response
from typing import Literal, Optional
from app.api.routers.admin._common import (
    _bucket_key,
    _iter_trace_rows,
    _llm_trace_dir,
    _parse_dt,
)
from app.api.routers.admin.schemas import (
    TokenUsageAlert,
    TokenUsageRankingItem,
    TokenUsageResponse,
    TokenUsageSeriesItem,
    TokenUsageTotals,
)

router = APIRouter()


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
