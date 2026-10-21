#!/usr/bin/env python3
"""Audit-result completeness monitor.

Reads every stored contract audit from data/app.db and reports how many chunks
failed to parse / were truncated, plus the resulting risk count. This is the
regression signal for the classic multi-chunk audit path: when
`CLASSIC_AUDIT_MAX_TOKENS` is too small, chunks truncate, json_repair salvages
partial output, and risks are silently lost.

Usage:
    app/.venv/Scripts/python.exe scripts/analyze_audit_quality.py
    app/.venv/Scripts/python.exe scripts/analyze_audit_quality.py --out report.txt
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "app.db"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "logs" / "audit_quality_report.txt"))
    args = ap.parse_args()

    if not DB.exists():
        print(f"database not found: {DB}", file=sys.stderr)
        sys.exit(1)

    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    rows = list(c.execute(
        "SELECT id, document_id, status, model_name, created_at, result_json "
        "FROM contract_audit ORDER BY created_at DESC"))

    lines = []
    lines.append(f"contract_audit rows: {len(rows)}")
    lines.append("")
    hdr = (f"{'created_at':<26}{'id':<10}{'status':<9}{'risks':>6}{'incompl':>9}"
           f"{'chunks':>8}{'parsefail':>11}{'trunc':>7}  summary")
    lines.append(hdr)
    lines.append("-" * len(hdr))

    incomplete = zero = total_done = 0
    for r in rows:
        try:
            p = json.loads(r["result_json"] or "{}")
        except Exception:
            p = {}
        if not isinstance(p, dict):
            p = {}
        risks = p.get("risks") if isinstance(p.get("risks"), list) else []
        inc = bool(p.get("audit_incomplete"))
        if r["status"] == "done":
            total_done += 1
            incomplete += 1 if inc else 0
            zero += 1 if not risks else 0
        s = str(p.get("summary") or "").replace("\n", " ")
        ch = re.search(r"(\d+)\s*(?:个片[段块]|chunks?)", s)
        pf = re.search(r"(\d+)\s*个片[段块](?:解析|失败)", s)
        tr = re.search(r"(\d+)\s*个片[段块](?:的输出)?被?截断", s)
        lines.append(
            f"{str(r['created_at'])[:25]:<26}{r['id'][:8]:<10}{str(r['status'])[:8]:<9}"
            f"{len(risks):>6}{str(inc):>9}"
            f"{(ch.group(1) if ch else '-'):>8}"
            f"{(pf.group(1) if pf else '-'):>11}"
            f"{(tr.group(1) if tr else '-'):>7}  {s[:90]}")

    lines.append("")
    lines.append(f"done audits                    : {total_done}")
    lines.append(f"  with audit_incomplete=True   : {incomplete}"
                 f"  ({incomplete / total_done * 100:.0f}%)" if total_done else "")
    lines.append(f"  returning ZERO risks         : {zero}"
                 f"  ({zero / total_done * 100:.0f}%)" if total_done else "")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")

    # ASCII-only console summary so Windows code pages cannot mangle it
    print(f"rows={len(rows)}  done={total_done}  "
          f"incomplete={incomplete}  zero_risk={zero}")
    print(f"full report (UTF-8) -> {out}")


if __name__ == "__main__":
    main()
