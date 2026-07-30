"""L2 任务级功能正确性层 —— 合同审计跑批器（进程内调用，模型可切换）。

按测试计划文档 L2 层要求，对 data/contracts 下的真实合同执行完整合同审计管线，
并为模型选型采集：单份合同总耗时、风险条目产出数、引用有效率，以及可从
llm_trace.db（llm_trace_spans 表）离线聚合的 JSON 一次成功率/修复率/回退率/token/延迟。

变量控制：每一轮次仅切换 local_llm.main_model.model（及 small_model / num_ctx）。
LLMService(cfg) 直接读取传入的 cfg 做路由，因此本脚本在同一进程内为每个轮次
构造独立的 cfg 变体 + 独立的 LLMService 实例，无需重启进程即可切换模型（已核实
app/core/llm.py: LLMService.__init__ 存储 self.cfg，_resolve_chat_target 用 self.cfg 路由）。

用法（务必在 app 目录下运行，使 config.json 的相对路径 ../data 生效）：
    cd app
    ..\\app\\venv\\Scripts\\python.exe ..\\plan\\...\\l2_contract_runner.py \
        --round-label B-14b --main-model qwen3:14b --small-model qwen3:4b \
        --num-ctx-main 8192 --num-ctx-small 8192 --contracts all --passes 2 \
        --out ..\\plan\\...\\L2_contract_B-14b.json
"""
import argparse
import copy
import glob
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone

# 允许从 app 目录运行时导入 app 包（app 目录的父目录加入 sys.path）
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from app.core.config import get_config  # noqa: E402
from app.core.embedding import EmbeddingService  # noqa: E402
from app.core.llm import LLMService  # noqa: E402
from app.app_factory import init_only  # noqa: E402
from app.services.audit_orchestrator import (  # noqa: E402
    AuditServices,
    run_contract_pipeline_bundle,
)

CONTRACTS_DIR = os.path.join(REPO, "data", "contracts")
# 合同审计端点仅接受 docx/pdf；.doc 与子目录跳过
ALLOWED_EXT = {".docx", ".pdf"}


def _list_contracts(selector: str):
    files = []
    for path in sorted(glob.glob(os.path.join(CONTRACTS_DIR, "*"))):
        if not os.path.isfile(path):
            continue
        if os.path.splitext(path)[1].lower() in ALLOWED_EXT:
            files.append(path)
    if selector and selector.lower() != "all":
        wanted = {s.strip() for s in selector.split(",") if s.strip()}
        files = [f for f in files if os.path.basename(f) in wanted]
    return files


def _build_cfg_variant(base_cfg, main_model, small_model,
                       num_ctx_main, num_ctx_small, keep_alive):
    cfg = copy.deepcopy(base_cfg)
    local = cfg.setdefault("local_llm", {})
    local["enabled"] = True
    local["routing_enabled"] = True
    mm = local.setdefault("main_model", {})
    mm["model"] = main_model
    if num_ctx_main > 0:
        mm["num_ctx"] = num_ctx_main
    mm["keep_alive"] = keep_alive
    sm = local.setdefault("small_model", {})
    sm["model"] = small_model
    if num_ctx_small > 0:
        sm["num_ctx"] = num_ctx_small
    sm["keep_alive"] = keep_alive
    # llm_config 作为非路由兜底，也同步为 main，避免误用旧基线
    lc = cfg.setdefault("llm_config", {})
    lc["model"] = main_model
    return cfg


def _citation_validity(audit):
    """引用有效率：合同审计风险项的法条引用是否有效落地。

    风险项 schema（见 memory_pipeline/prompt_templates.py）每项含
    citation_id / law_title / article_no，后处理可能补 citation_status=mapped。
    定义：
      - risk_total          风险项总数
      - with_ref            带任意法条引用（citation_id 或 law_title）的风险数
      - mapped              引用可落到 citation 池（citation_id 命中）或 citation_status==mapped
      - rate = mapped / risk_total  作为“引用有效率”
    """
    if not isinstance(audit, dict):
        return {"risk_total": 0, "with_ref": 0, "mapped": 0, "rate": None,
                "citation_pool_size": 0}
    pool = set()
    citations = audit.get("citations")
    if isinstance(citations, list):
        for c in citations:
            if isinstance(c, dict):
                cid = str(c.get("citation_id") or c.get("id") or "").strip()
                if cid:
                    pool.add(cid)
    risks = audit.get("risks") if isinstance(audit.get("risks"), list) else []
    risk_total = 0
    with_ref = 0
    mapped = 0
    for r in risks:
        if not isinstance(r, dict):
            continue
        risk_total += 1
        cid = str(r.get("citation_id") or "").strip()
        law = str(r.get("law_title") or "").strip()
        status = str(r.get("citation_status") or "").strip().lower()
        if cid or law:
            with_ref += 1
        if status == "mapped" or (cid and (not pool or cid in pool)):
            mapped += 1
    rate = (mapped / risk_total) if risk_total else None
    return {"risk_total": risk_total, "with_ref": with_ref, "mapped": mapped,
            "rate": rate, "citation_pool_size": len(pool)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--round-label", required=True)
    ap.add_argument("--main-model", required=True)
    ap.add_argument("--small-model", default="qwen3:4b")
    ap.add_argument("--num-ctx-main", type=int, default=0)
    ap.add_argument("--num-ctx-small", type=int, default=0)
    ap.add_argument("--keep-alive", default="30m")
    ap.add_argument("--contracts", default="all")
    ap.add_argument("--passes", type=int, default=2)
    ap.add_argument("--lang", default="zh")
    ap.add_argument("--warmup", type=int, default=1)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    base_cfg = get_config()
    init_only()

    cfg = _build_cfg_variant(
        base_cfg, args.main_model, args.small_model,
        args.num_ctx_main, args.num_ctx_small, args.keep_alive,
    )

    embedder = EmbeddingService(
        default_language=str(cfg.get("default_language", "zh")).lower())
    try:
        embedder.load_embedders(cfg)
    except Exception as e:  # 检索退化不影响审计主流程
        print(f"[WARN] embedder load failed: {e}")

    llm = LLMService(cfg)
    services = AuditServices(llm=llm, embedder=embedder)

    contracts = _list_contracts(args.contracts)
    if not contracts:
        print("[ERROR] no docx/pdf contracts found")
        return 1
    print(f"[INFO] round={args.round_label} main={args.main_model} "
          f"small={args.small_model} num_ctx_main={args.num_ctx_main} "
          f"contracts={len(contracts)} passes={args.passes}")

    retrieval_options = {"audit_mode": "rag", "risk_detection_mode": "balanced",
                         "tax_focus": "true"}

    # 预热：加载模型（不计入统计）
    for _ in range(max(0, args.warmup)):
        try:
            t0 = time.perf_counter()
            llm.chat([{"role": "user", "content": "回复OK"}],
                     overrides={"_task_profile": "default", "max_tokens": 8})
            print(f"[WARMUP] main ready in {time.perf_counter()-t0:.2f}s")
        except Exception as e:
            print(f"[WARMUP] failed: {e}")

    window_start = datetime.now(timezone.utc).isoformat()
    records = []
    for pass_idx in range(1, args.passes + 1):
        for path in contracts:
            name = os.path.basename(path)
            run_id = uuid.uuid4().hex[:8]
            started = datetime.now(timezone.utc).isoformat()
            t0 = time.perf_counter()
            ok = True
            err = ""
            risk_summary = {}
            cite = {}
            exec_path = ""
            try:
                bundle = run_contract_pipeline_bundle(
                    cfg, services, file_path=path, lang=args.lang,
                    retrieval_options=retrieval_options,
                )
                audit = bundle.get("audit") or {}
                risk_summary = bundle.get("risk_summary") or {}
                cite = _citation_validity(audit)
                exec_path = str((bundle.get("meta") or {}).get(
                    "execution_path") or "")
            except Exception as e:
                ok = False
                err = f"{type(e).__name__}: {e}"
            elapsed = round(time.perf_counter() - t0, 3)
            rec = {
                "run_id": run_id, "pass": pass_idx, "contract": name,
                "ok": ok, "error": err, "elapsed_sec": elapsed,
                "started_at": started,
                "ended_at": datetime.now(timezone.utc).isoformat(),
                "execution_path": exec_path,
                "risk_summary": risk_summary,
                "risk_total": sum(int(v) for v in risk_summary.values()) if risk_summary else 0,
                "citation": cite,
            }
            records.append(rec)
            print(f"[RUN] pass{pass_idx} {name} ok={ok} "
                  f"{elapsed}s risks={rec['risk_total']} "
                  f"cite_rate={cite.get('rate')} path={exec_path} {err}")
    window_end = datetime.now(timezone.utc).isoformat()

    ok_recs = [r for r in records if r["ok"]]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "round_label": args.round_label,
        "main_model": args.main_model,
        "small_model": args.small_model,
        "num_ctx_main": args.num_ctx_main,
        "num_ctx_small": args.num_ctx_small,
        "keep_alive": args.keep_alive,
        "provider": "ollama",
        "trace_window": {"start": window_start, "end": window_end},
        "contracts_tested": [os.path.basename(p) for p in contracts],
        "passes": args.passes,
        "totals": {
            "runs": len(records),
            "ok": len(ok_recs),
            "failed": len(records) - len(ok_recs),
            "avg_elapsed_sec": round(sum(r["elapsed_sec"] for r in ok_recs) / len(ok_recs), 3) if ok_recs else None,
            "avg_risk_total": round(sum(r["risk_total"] for r in ok_recs) / len(ok_recs), 2) if ok_recs else None,
        },
        "records": records,
    }
    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"[OK] -> {out_path}")
    print(f"[SUMMARY] runs={len(records)} ok={len(ok_recs)} "
          f"trace_window={window_start} .. {window_end}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
