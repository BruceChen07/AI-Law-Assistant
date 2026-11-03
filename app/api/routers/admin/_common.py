"""Shared helpers: text cleaning, datetime bucketing, llm trace rows."""
from __future__ import annotations

import json
import logging
import os

from datetime import datetime, timedelta
from fastapi import APIRouter
from typing import Optional

logger = logging.getLogger("law_assistant")

router = APIRouter()


def _clean_text(v: str) -> str:
    s = str(v or "").strip()
    s = s.strip("`").strip('"').strip("'").strip()
    return s


def _normalize_theme(v: Optional[str]) -> str:
    s = str(v or "").strip().lower()
    return "light" if s == "light" else "dark"


def _clean_optional_text(value: Optional[str]) -> str:
    return str(value or "").strip()


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


def _llm_trace_dir(cfg: dict) -> str:
    trace_dir = str(cfg.get("llm_trace_dir") or "").strip()
    if not trace_dir:
        base = str(cfg.get("data_dir") or "").strip()
        trace_dir = os.path.join(
            base, "llm_interactions") if base else os.path.abspath("llm_interactions")
    return os.path.abspath(trace_dir)


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
            except OSError as e:
                logger.warning(
                    "llm_trace_read_failed file=%s err=%s",
                    file_path,
                    str(e),
                )
        day += timedelta(days=1)
