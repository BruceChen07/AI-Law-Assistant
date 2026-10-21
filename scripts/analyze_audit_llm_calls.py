#!/usr/bin/env python3
"""Break the contract-audit `auditing` stage down into LLM round-trips.

Reads the runtime logs, finds every audit that has stage-level progress markers,
then counts the `llm_request_done` events that fall inside its `auditing` window
and sums their `latency_ms`.

Usage:
    app/.venv/Scripts/python.exe scripts/analyze_audit_llm_calls.py
    app/.venv/Scripts/python.exe scripts/analyze_audit_llm_calls.py --audit 3cf5503d
"""
from __future__ import annotations

import argparse
import re
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TS = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})")
PROG = re.compile(r"contract_audit_progress audit_id=([0-9a-fA-F-]+).*?stage=(\S+)")
DONE = re.compile(r"contract_audit_done audit_id=([0-9a-fA-F-]+)")
LLM = re.compile(r"llm_request_done .*?latency_ms=(\d+)")
LLM_TOK = re.compile(r"total_tokens=(\d+)")


def parse_ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S,%f")


def read_text(p: Path) -> str:
    for enc in ("utf-8", "gbk"):
        try:
            return p.read_text(encoding=enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return p.read_text(encoding="utf-8", errors="replace")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", default="", help="only this audit id (prefix ok)")
    args = ap.parse_args()

    auditing_start: dict[str, datetime] = {}
    done_at: dict[str, datetime] = {}
    llm_calls: list[tuple[datetime, float | None, int]] = []

    files = sorted(set(list(ROOT.joinpath("logs").glob("**/*.log"))
                       + list(ROOT.joinpath("logs").glob("**/*.log.*"))))
    seen_llm = set()
    for p in files:
        for line in read_text(p).splitlines():
            if "llm_request_done" in line:
                m_ts = TS.search(line)
                if not m_ts:
                    continue
                lat = LLM.search(line)
                tok = LLM_TOK.search(line)
                key = (m_ts.group(1), lat.group(1) if lat else "")
                if key in seen_llm:      # same event duplicated across log files
                    continue
                seen_llm.add(key)
                llm_calls.append((
                    parse_ts(m_ts.group(1)),
                    int(lat.group(1)) / 1000 if lat else None,
                    int(tok.group(1)) if tok else 0,
                ))
                continue

            if "contract_audit_" not in line:
                continue
            m_ts = TS.search(line)
            if not m_ts:
                continue
            ts = parse_ts(m_ts.group(1))

            m = PROG.search(line)
            if m and m.group(2) == "auditing":
                auditing_start.setdefault(m.group(1), ts)
                continue
            m = DONE.search(line)
            if m:
                done_at.setdefault(m.group(1), ts)

    llm_calls.sort(key=lambda c: c[0])

    rows = []
    for aid, st in auditing_start.items():
        dn = done_at.get(aid)
        if not dn:
            continue
        window = [c for c in llm_calls if st <= c[0] <= dn]
        lat = [c[1] for c in window if c[1]]
        rows.append({
            "audit_id": aid,
            "auditing_s": round((dn - st).total_seconds(), 1),
            "calls": len(window),
            "llm_sum_s": round(sum(lat), 1),
            "llm_mean_s": round(statistics.mean(lat), 2) if lat else 0,
            "llm_min_s": round(min(lat), 2) if lat else 0,
            "llm_max_s": round(max(lat), 2) if lat else 0,
            "tokens": sum(c[2] for c in window),
        })

    if args.audit:
        rows = [r for r in rows if r["audit_id"].startswith(args.audit)]

    print("=" * 100)
    print("AUDITING STAGE = LLM ROUND-TRIP TIME")
    print("=" * 100)
    if not rows:
        print("no audit with stage-level markers found")
        return

    print(f"  {'audit':<10}{'auditing_s':>12}{'llm_calls':>11}{'llm_sum_s':>11}"
          f"{'mean_s':>9}{'max_s':>8}{'tokens':>9}{'llm%':>8}")
    for r in sorted(rows, key=lambda r: -r["auditing_s"]):
        pct = r["llm_sum_s"] / r["auditing_s"] * 100 if r["auditing_s"] else 0
        print(f"  {r['audit_id'][:8]:<10}{r['auditing_s']:>12.1f}{r['calls']:>11}"
              f"{r['llm_sum_s']:>11.1f}{r['llm_mean_s']:>9.2f}{r['llm_max_s']:>8.2f}"
              f"{r['tokens']:>9,}{pct:>7.0f}%")

    n = len(rows)
    calls = [r["calls"] for r in rows]
    print(f"\n  across {n} audits with stage detail:")
    print(f"    LLM calls per audit : median {statistics.median(calls):.0f}  "
          f"range {min(calls)}-{max(calls)}")
    print(f"    mean latency        : {statistics.mean([r['llm_mean_s'] for r in rows]):.2f}s")
    print(f"    auditing stage      : median {statistics.median([r['auditing_s'] for r in rows]):.1f}s")
    print(f"    tokens per audit    : median {statistics.median([r['tokens'] for r in rows]):,.0f}")
    share = statistics.mean([
        r["llm_sum_s"] / r["auditing_s"] * 100 for r in rows if r["auditing_s"]]) 
    print(f"    LLM share of stage  : {share:.0f}%  (rest is prompt building / parsing)")

    print("\n  serialization evidence (one audit, consecutive call gaps):")
    aid = max(rows, key=lambda r: r["calls"])["audit_id"]
    st = auditing_start[aid]
    dn = done_at[aid]
    seq = [c for c in llm_calls if st <= c[0] <= dn]
    gaps = []
    for a, b in zip(seq, seq[1:]):
        gaps.append((b[0] - timedelta(seconds=b[1] or 0) - a[0]).total_seconds())
    if gaps:
        print(f"    audit {aid[:8]}: {len(seq)} calls, gap between calls "
              f"mean {statistics.mean(gaps):+.2f}s  max {max(gaps):+.2f}s")
        print("    -> gaps ~0 means every call starts the instant the previous returns:")
        print("       the stage is pure serialized API latency, no local CPU cost.")


if __name__ == "__main__":
    main()
