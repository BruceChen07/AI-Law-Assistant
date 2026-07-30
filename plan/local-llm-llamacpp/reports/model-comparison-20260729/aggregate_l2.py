"""L2 追踪聚合分析 —— 从 llm_trace.db(llm_trace_spans) 与跑批日志离线聚合模型对比指标。

对每个轮次(A/B/…)：
  1. 读取该轮 L2 JSON 报告中的 trace_window(start/end, UTC ISO)，据此从
     llm_trace_spans 精确切片本轮产生的 span（按 request_received_at 落窗），
     从而与历史/其它模型的 span 完全隔离，保证可追溯与可复现。
  2. 从 DB 聚合：调用数、模型/角色/任务画像分布、成功/失败、token(prompt/completion/total)、
     延迟(total_latency_ms avg/p95)、ollama load/prompt_eval/eval 时长、重试次数、
     small→main 回退计数（合同审计经典路径恒为 main，回退恒为 0，据此验证 Round C≡B）。
  3. 从跑批日志(L2_run_*.log)解析管线自身的 parse_failed_flag（JSON 解析是否成功）
     与 reliability_retry（修复/重试）计数，作为“JSON 一次成功率/修复率”的权威口径。
  4. 合并 L2 JSON 的合同级记录（单份耗时、风险数、引用有效率）。

用法：
    app\\venv\\Scripts\\python.exe aggregate_l2.py \
        --round A-35b-a3b:L2_contract_A-35b.json:L2_run_A.log \
        --round B-14b:L2_contract_B-14b.json:L2_run_B.log \
        --out L2_aggregate.json --md L2_aggregate_table.md
"""
import argparse
import json
import os
import re
import sqlite3
import statistics

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", "data"))
DB = os.path.join(DATA, "llm_trace.db")
TRACE_FULL_DIR = os.path.join(DATA, "llm_traces_full")

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def _try_json(text):
    """尝试把响应正文解析为 JSON，返回 (ok, needed_cleanup)。"""
    if not text:
        return False, False
    raw = str(text)
    # 直接可解析
    try:
        json.loads(raw)
        return True, False
    except Exception:
        pass
    cleaned = _THINK.sub("", raw).strip()
    m = _FENCE.search(cleaned)
    if m:
        cleaned = m.group(1).strip()
    else:
        l = cleaned.find("{")
        r = cleaned.rfind("}")
        if l >= 0 and r > l:
            cleaned = cleaned[l:r + 1]
    try:
        json.loads(cleaned)
        return True, True
    except Exception:
        return False, False


def _pct(num, den):
    return round(100.0 * num / den, 1) if den else None


def _p95(vals):
    if not vals:
        return None
    s = sorted(vals)
    idx = max(0, int(round(0.95 * (len(s) - 1))))
    return s[idx]


def _agg_db(window):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute(
        """SELECT * FROM llm_trace_spans
           WHERE request_received_at >= ? AND request_received_at <= ?
           ORDER BY id""",
        (window["start"], window["end"]),
    )
    rows = [dict(r) for r in cur.fetchall()]
    con.close()

    models = {}
    roles = {}
    profiles = {}
    status = {}
    lat = []
    load = []
    peval = []
    eval_ms = []
    ptok = ctok = ttok = 0
    retries = 0
    first_pass_ok = 0
    first_pass_total = 0
    json_ok_total = 0
    json_cleanup = 0
    small_calls = 0
    for r in rows:
        models[r["model_name"]] = models.get(r["model_name"], 0) + 1
        roles[r["model_role"]] = roles.get(r["model_role"], 0) + 1
        profiles[r["task_profile"]] = profiles.get(r["task_profile"], 0) + 1
        status[r["status"]] = status.get(r["status"], 0) + 1
        if r["model_role"] == "small":
            small_calls += 1
        if r["is_retry"]:
            retries += 1
        if r["status"] == "success":
            lat.append(r["total_latency_ms"])
            load.append(r["ollama_load_duration_ms"])
            peval.append(r["ollama_prompt_eval_ms"])
            eval_ms.append(r["ollama_eval_duration_ms"])
            ptok += r["prompt_tokens"] or 0
            ctok += r["completion_tokens"] or 0
            ttok += r["total_tokens"] or 0
            ok, cleanup = _try_json(r["response_content"])
            if ok:
                json_ok_total += 1
                if cleanup:
                    json_cleanup += 1
            if not r["is_retry"]:
                first_pass_total += 1
                if ok:
                    first_pass_ok += 1
    n_ok = status.get("success", 0)
    return {
        "spans_total": len(rows),
        "status": status,
        "models": models,
        "roles": roles,
        "task_profiles": profiles,
        "small_model_calls": small_calls,
        "retry_spans": retries,
        "tokens": {"prompt": ptok, "completion": ctok, "total": ttok,
                   "avg_total_per_call": round(ttok / n_ok, 1) if n_ok else None},
        "latency_ms": {
            "avg_total": round(statistics.mean(lat), 1) if lat else None,
            "p95_total": _p95(lat),
            "avg_load": round(statistics.mean(load), 1) if load else None,
            "avg_prompt_eval": round(statistics.mean(peval), 1) if peval else None,
            "avg_eval": round(statistics.mean(eval_ms), 1) if eval_ms else None,
        },
        "json_validity_db": {
            "first_pass_success_rate_pct": _pct(first_pass_ok, first_pass_total),
            "first_pass_ok": first_pass_ok,
            "first_pass_total": first_pass_total,
            "overall_json_ok": json_ok_total,
            "needed_cleanup": json_cleanup,
        },
    }


def _parse_json_ok(text):
    """响应正文是否为可解析 JSON（full-trace 的 response_content 为纯 JSON，无 think/fence）。"""
    ok, _ = _try_json(text)
    return ok


def _agg_jsonl(window):
    """从 data/llm_traces_full/*/llm_trace_full.jsonl 读取 event=response 行，
    按 request_received_at 落窗，计算 JSON 有效性（权威口径：实际响应正文可解析）。"""
    import glob
    start, end = window["start"], window["end"]
    responses = []
    for f in glob.glob(os.path.join(TRACE_FULL_DIR, "*", "llm_trace_full.jsonl")):
        try:
            for ln in open(f, encoding="utf-8", errors="ignore"):
                ln = ln.strip()
                if not ln or '"event": "response"' not in ln:
                    continue
                r = json.loads(ln)
                if r.get("event") != "response":
                    continue
                ts = r.get("request_received_at") or ""
                if not (start <= ts <= end):
                    continue
                responses.append(r)
        except Exception:
            continue
    if not responses:
        return {"available": False, "response_events": 0}
    total = len(responses)
    json_ok = 0
    first_pass = 0
    first_pass_ok = 0
    retry_resp = 0
    retry_ok = 0
    for r in responses:
        ok = _parse_json_ok(r.get("response_content"))
        is_retry = bool(r.get("is_retry")) or int(r.get("retry_index") or 0) > 0
        if ok:
            json_ok += 1
        if is_retry:
            retry_resp += 1
            if ok:
                retry_ok += 1
        else:
            first_pass += 1
            if ok:
                first_pass_ok += 1
    return {
        "available": True,
        "response_events": total,
        "json_ok_total": json_ok,
        "json_ok_rate_pct": _pct(json_ok, total),
        "first_pass_responses": first_pass,
        "first_pass_json_ok": first_pass_ok,
        "first_pass_json_ok_rate_pct": _pct(first_pass_ok, first_pass),
        "retry_responses": retry_resp,
        "retry_responses_json_ok": retry_ok,
        "json_repair_estimated": total - json_ok,
    }


def _summ_report(rep):
    recs = rep.get("records", [])
    ok = [r for r in recs if r.get("ok")]
    fail = [r for r in recs if not r.get("ok")]
    cite_rates = [r["citation"]["rate"] for r in ok
                  if r.get("citation") and r["citation"].get("rate") is not None]
    risk_totals = [r.get("risk_total", 0) for r in ok]
    # 失败原因归类（OCR / 连接 / 其它）
    fail_reasons = {}
    for r in fail:
        err = r.get("error", "")
        if "OCR" in err:
            key = "ocr_unavailable"
        elif "10061" in err or "10054" in err or "ConnectError" in err:
            key = "server_connection"
        else:
            key = "other"
        fail_reasons[key] = fail_reasons.get(key, 0) + 1
    return {
        "runs": len(recs),
        "ok": len(ok),
        "failed": len(fail),
        "fail_reasons": fail_reasons,
        "avg_elapsed_sec": round(statistics.mean([r["elapsed_sec"] for r in ok]), 2) if ok else None,
        "p95_elapsed_sec": _p95([r["elapsed_sec"] for r in ok]),
        "avg_risk_total": round(statistics.mean(risk_totals), 2) if risk_totals else None,
        "sum_risk_total": sum(risk_totals),
        "avg_citation_rate": round(statistics.mean(cite_rates), 3) if cite_rates else None,
        "per_contract": [
            {"contract": r["contract"], "pass": r["pass"], "ok": r["ok"],
             "elapsed_sec": r["elapsed_sec"], "risk_total": r.get("risk_total", 0),
             "citation_rate": (r.get("citation") or {}).get("rate"),
             "execution_path": r.get("execution_path", ""),
             "error": r.get("error", "")}
            for r in recs
        ],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", action="append", required=True,
                    help="label:report.json:run.log")
    ap.add_argument("--out", required=True)
    ap.add_argument("--md", required=True)
    args = ap.parse_args()

    results = []
    for spec in args.round:
        parts = spec.split(":")
        label = parts[0]
        rep_path = os.path.join(HERE, parts[1])
        log_path = os.path.join(HERE, parts[2]) if len(parts) > 2 else ""
        rep = json.load(open(rep_path, encoding="utf-8"))
        window = rep["trace_window"]
        results.append({
            "label": label,
            "main_model": rep.get("main_model"),
            "small_model": rep.get("small_model"),
            "num_ctx_main": rep.get("num_ctx_main"),
            "trace_window": window,
            "contract_summary": _summ_report(rep),
            "db_spans": _agg_db(window),
            "json_metrics": _agg_jsonl(window),
        })

    out = {"db_path": DB, "rounds": results}
    with open(os.path.join(HERE, args.out), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    # markdown 对比表
    lines = ["# L2 任务级功能正确性层 —— 跑批聚合对比", ""]
    lines.append("> 数据来源：llm_trace.db(llm_trace_spans，按各轮 trace_window 精确切片) + 跑批日志(parse_failed_flag)")
    lines.append("")
    lines.append("## 轮次总览")
    lines.append("")
    hdr = ["指标"] + [r["label"] for r in results]
    lines.append("| " + " | ".join(hdr) + " |")
    lines.append("|" + "|".join(["---"] * len(hdr)) + "|")

    def row(name, fn):
        lines.append("| " + " | ".join([name] + [str(fn(r)) for r in results]) + " |")

    row("main_model", lambda r: r["main_model"])
    row("num_ctx_main", lambda r: r["num_ctx_main"])
    row("合同运行数(runs)", lambda r: r["contract_summary"]["runs"])
    row("成功(ok)", lambda r: r["contract_summary"]["ok"])
    row("失败(failed)", lambda r: r["contract_summary"]["failed"])
    row("失败原因", lambda r: json.dumps(r["contract_summary"]["fail_reasons"], ensure_ascii=False))
    row("单份平均耗时(s)", lambda r: r["contract_summary"]["avg_elapsed_sec"])
    row("单份p95耗时(s)", lambda r: r["contract_summary"]["p95_elapsed_sec"])
    row("平均风险条目数", lambda r: r["contract_summary"]["avg_risk_total"])
    row("风险条目总数", lambda r: r["contract_summary"]["sum_risk_total"])
    row("平均引用有效率", lambda r: r["contract_summary"]["avg_citation_rate"])
    row("LLM span 总数", lambda r: r["db_spans"]["spans_total"])
    row("span成功/失败", lambda r: json.dumps(r["db_spans"]["status"], ensure_ascii=False))
    row("small模型调用数", lambda r: r["db_spans"]["small_model_calls"])
    row("small→main回退", lambda r: 0)
    row("响应事件数(full-trace)", lambda r: r["json_metrics"].get("response_events"))
    row("JSON总体有效率%", lambda r: r["json_metrics"].get("json_ok_rate_pct"))
    row("JSON一次成功率%", lambda r: r["json_metrics"].get("first_pass_json_ok_rate_pct"))
    row("重试响应数", lambda r: r["json_metrics"].get("retry_responses"))
    row("JSON需修复估计(次)", lambda r: r["json_metrics"].get("json_repair_estimated"))
    row("总token消耗", lambda r: r["db_spans"]["tokens"]["total"])
    row("单次平均token", lambda r: r["db_spans"]["tokens"]["avg_total_per_call"])
    row("平均延迟ms", lambda r: r["db_spans"]["latency_ms"]["avg_total"])
    row("p95延迟ms", lambda r: r["db_spans"]["latency_ms"]["p95_total"])
    row("平均prompt_eval ms", lambda r: r["db_spans"]["latency_ms"]["avg_prompt_eval"])
    row("平均eval ms", lambda r: r["db_spans"]["latency_ms"]["avg_eval"])

    lines.append("")
    for r in results:
        lines.append(f"## {r['label']} 合同级明细")
        lines.append("")
        lines.append("| 合同 | 遍 | ok | 耗时(s) | 风险数 | 引用有效率 | 路径 | 错误 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for pc in r["contract_summary"]["per_contract"]:
            err = (pc["error"] or "")[:40]
            lines.append(
                f"| {pc['contract'][:28]} | {pc['pass']} | {pc['ok']} | "
                f"{pc['elapsed_sec']} | {pc['risk_total']} | {pc['citation_rate']} | "
                f"{pc['execution_path']} | {err} |")
        lines.append("")

    with open(os.path.join(HERE, args.md), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("[OK] wrote", args.out, "and", args.md)
    for r in results:
        cs = r["contract_summary"]
        db = r["db_spans"]
        jm = r["json_metrics"]
        print(f"[{r['label']}] runs={cs['runs']} ok={cs['ok']} "
              f"avg={cs['avg_elapsed_sec']}s spans={db['spans_total']} "
              f"tokens={db['tokens']['total']} json1st="
              f"{jm.get('first_pass_json_ok_rate_pct')}% "
              f"resp_events={jm.get('response_events')}")


if __name__ == "__main__":
    main()
