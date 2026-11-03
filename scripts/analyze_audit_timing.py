#!/usr/bin/env python3
"""Reconstruct contract-audit stage timings from the runtime logs.

The audit progress writer emits one structured line per stage transition:

    contract_audit_progress audit_id=<id> status=running progress=<N> stage=<S> task=<T> message=<M>

plus a terminal `contract_audit_start` / `contract_audit_done` pair. This script
joins those into a per-audit timeline and reports where the wall-clock time goes.

Usage:
    app/.venv/Scripts/python.exe scripts/analyze_audit_timing.py
    app/.venv/Scripts/python.exe scripts/analyze_audit_timing.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TS = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})")
START = re.compile(r"contract_audit_start audit_id=([0-9a-fA-F-]+)")
DONE = re.compile(r"contract_audit_done audit_id=([0-9a-fA-F-]+)")
PROG = re.compile(
    r"contract_audit_progress audit_id=([0-9a-fA-F-]+) status=(\w+) "
    r"progress=(\d+) stage=(\S+) task=(\S+)")
META = re.compile(r"lang=(\w+) mode=(\w+)(?: risk_detection_mode=(\w+))?")


def parse_ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S,%f")


def read_text(p: Path) -> str:
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return p.read_text(encoding=enc, errors="strict")
        except (UnicodeDecodeError, LookupError):
            continue
    return p.read_text(encoding="utf-8", errors="replace")


def log_files() -> list[Path]:
    out = []
    for pat in ("**/*.log", "**/*.log.*"):
        out.extend(ROOT.joinpath("logs").glob(pat))
    return sorted(set(out))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    starts: dict[str, datetime] = {}
    dones: dict[str, dict] = {}
    events: dict[str, list] = defaultdict(list)
    seen: set = set()
    files = log_files()

    for p in files:
        text = read_text(p)
        for line in text.splitlines():
            if "contract_audit_" not in line:
                continue
            m_ts = TS.search(line)
            if not m_ts:
                continue
            ts = parse_ts(m_ts.group(1))

            m = START.search(line)
            if m:
                starts.setdefault(m.group(1), ts)
                continue

            m = DONE.search(line)
            if m:
                meta = META.search(line)
                dones.setdefault(m.group(1), {
                    "ts": ts,
                    "ocr_used": "ocr_used=True" in line,
                    "text_length": int((re.search(r"text_length=(\d+)", line) or ["", 0])[1] or 0),
                    "page_count": int((re.search(r"page_count=(\d+)", line) or ["", 0])[1] or 0),
                })
                continue

            m = PROG.search(line)
            if m:
                aid, status, prog, stage, task = m.groups()
                key = (aid, m_ts.group(1), stage)
                if key in seen:
                    continue
                seen.add(key)
                events[aid].append({
                    "ts": ts,
                    "progress": int(prog),
                    "stage": stage,
                    "task": task,
                })

    # ------------------------------------------------------------- timelines
    timelines = []
    for aid, st in starts.items():
        dn = dones.get(aid)
        evs = sorted(events.get(aid, []), key=lambda e: e["ts"])
        # collapse consecutive duplicates of the same stage (retries / repeats)
        seq = []
        for e in evs:
            if seq and seq[-1]["stage"] == e["stage"]:
                continue
            seq.append(e)

        # NOTE: `contract_audit_start` is emitted AFTER the PDF-digitization step
        # (contracts.py:722), so start->done measures only the audit core and hides
        # the digitization time. The true request-to-response wall clock starts at
        # the first progress event (progress=5, stage=received).
        ref = seq[0]["ts"] if seq else st
        row = {
            "audit_id": aid,
            "start": st.isoformat(sep=" "),
            "request_start": ref.isoformat(sep=" "),
            "completed": dn is not None,
            "has_stage_detail": bool(seq),
            "stages": [],
        }
        if dn:
            row["total_s"] = round((dn["ts"] - ref).total_seconds(), 1)
            row["audit_core_s"] = round((dn["ts"] - st).total_seconds(), 1)
            row["digitized"] = any(s["stage"] == "digitizing" for s in seq)
            row["text_length"] = dn["text_length"]
            row["ocr_used"] = dn["ocr_used"]

        # duration owned by each stage = gap until the next transition
        for i, e in enumerate(seq):
            nxt = seq[i + 1]["ts"] if i + 1 < len(seq) else (dn["ts"] if dn else None)
            if nxt is None:
                dur = None
            else:
                dur = round(max(0.0, (nxt - e["ts"]).total_seconds()), 1)
            row["stages"].append({
                "stage": e["stage"],
                "task": e["task"],
                "progress": e["progress"],
                "at": e["ts"].strftime("%H:%M:%S"),
                "dur_s": dur,
            })
        timelines.append(row)

    timelines.sort(key=lambda r: r["start"], reverse=True)

    # ------------------------------------------------------------- aggregates
    completed = [t for t in timelines if t["completed"] and t.get("total_s")]
    totals = sorted(t["total_s"] for t in completed)

    def pct(v: float) -> float:
        return round(totals[min(len(totals) - 1, int(len(totals) * v))], 1)

    per_stage: dict[str, list[float]] = defaultdict(list)
    for t in completed:
        for s in t["stages"]:
            if s["dur_s"] is not None:
                per_stage[s["stage"]].append(s["dur_s"])

    report = {
        "log_files_scanned": len(files),
        "audit_runs_detected": len(timelines),
        "audit_runs_completed": len(completed),
        "total_time_s": {
            "min": totals[0] if totals else None,
            "median": statistics.median(totals) if totals else None,
            "mean": round(statistics.mean(totals), 1) if totals else None,
            "p90": pct(0.9) if totals else None,
            "max": totals[-1] if totals else None,
        },
        "with_digitization_s": sorted(
            t["total_s"] for t in completed if t.get("digitized")),
        "without_digitization_s": sorted(
            t["total_s"] for t in completed
            if t.get("has_stage_detail") and not t.get("digitized")),
        "stage_stats": {
            k: {
                "n": len(v),
                "median_s": round(statistics.median(v), 1),
                "mean_s": round(statistics.mean(v), 1),
                "max_s": max(v),
                "total_s": round(sum(v), 1),
            } for k, v in sorted(per_stage.items(), key=lambda kv: -statistics.median(kv[1]))
        },
        "timelines": timelines,
    }

    # ------------------------------------------------------------------ print
    print("=" * 100)
    print("CONTRACT AUDIT TIMING ANALYSIS")
    print("=" * 100)
    print(f"log files scanned     : {len(files)}")
    print(f"audit runs detected   : {len(timelines)}")
    print(f"audit runs completed  : {len(completed)}  (have start + done)")
    if totals:
        print(f"\ntotal wall-clock per audit (seconds):")
        print(f"  min {totals[0]:8.1f}   median {statistics.median(totals):8.1f}   "
              f"mean {statistics.mean(totals):8.1f}   p90 {pct(0.9):8.1f}   max {totals[-1]:8.1f}")
        wd = report["with_digitization_s"]
        wod = report["without_digitization_s"]
        if wd:
            print(f"\n  needed PDF digitization ({len(wd)} runs): median "
                  f"{statistics.median(wd):7.1f}s   range {wd[0]:.1f}-{wd[-1]:.1f}s")
        if wod:
            print(f"  no digitization        ({len(wod)} runs): median "
                  f"{statistics.median(wod):7.1f}s   range {wod[0]:.1f}-{wod[-1]:.1f}s")

    print(f"\n\nPER-STAGE TIME (seconds, over {len(completed)} completed audits)")
    print("-" * 100)
    print(f"  {'stage':<26}{'n':>4}{'median':>9}{'mean':>9}{'max':>9}{'total':>10}{'share':>8}")
    grand = sum(v["total_s"] for v in report["stage_stats"].values()) or 1
    for k, v in report["stage_stats"].items():
        print(f"  {k:<26}{v['n']:>4}{v['median_s']:>9.1f}{v['mean_s']:>9.1f}"
              f"{v['max_s']:>9.1f}{v['total_s']:>10.1f}{v['total_s']/grand*100:>7.1f}%")

    print(f"\n\nSTAGE SEQUENCE (most recent completed run)")
    for t in timelines:
        if t["completed"] and t["stages"]:
            print(f"  audit {t['audit_id'][:8]}  request {t['request_start']}  "
                  f"total {t['total_s']}s   (audit core {t.get('audit_core_s')}s)")
            for s in t["stages"]:
                bar = "#" * int((s["dur_s"] or 0) / max(1, t["total_s"]) * 40)
                print(f"    {s['at']}  {s['stage']:<18} p{s['progress']:<3} "
                      f"{(s['dur_s'] if s['dur_s'] is not None else 0):8.1f}s  {bar}")
            break

    print(f"\n\nALL COMPLETED RUNS")
    print("-" * 100)
    print(f"  {'audit':<10}{'start':<21}{'total_s':>9}{'text_len':>10}  dominant stage")
    for t in completed:
        dom = max((s for s in t["stages"] if s["dur_s"] is not None),
                  key=lambda s: s["dur_s"], default=None)
        print(f"  {t['audit_id'][:8]:<10}{t['start']:<21}{t['total_s']:>9.1f}"
              f"{t.get('text_length', 0):>10}  "
              f"{(dom['stage'] + ' ' + str(dom['dur_s']) + 's') if dom else '-'}")

    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\njson -> {args.json}")


if __name__ == "__main__":
    main()
