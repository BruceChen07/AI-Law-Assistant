"""汇总 L1 压测报告，输出按场景×模型的性能对比表（Markdown + 控制台）。"""
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

SCENARIO_ORDER = ["L1-short-200", "L1-medium-2000",
                  "L1-long-8000", "L1-xlong-14000"]


def _summ_row(model: str, s: dict) -> dict:
    return {
        "model": model,
        "prompt_tokens": s.get("_prompt_tokens", 0),
        "avg_first_token_ms": s.get("avg_first_token_ms", 0.0),
        "p95_first_token_ms": s.get("p95_first_token_ms", 0.0),
        "avg_latency_sec": s.get("avg_latency_sec", 0.0),
        "p95_latency_sec": s.get("p95_latency_sec", 0.0),
        "avg_decode_tps": s.get("avg_decode_tokens_per_sec", 0.0),
        "avg_prefill_ms": s.get("avg_prefill_ms", 0.0),
        "max_load_ms": s.get("max_load_duration_ms", 0.0),
        "peak_gpu_mem_mb": s.get("peak_gpu_memory_mb", 0.0),
        "peak_gpu_util": s.get("peak_gpu_util_percent", 0.0),
        "peak_ram_mb": s.get("peak_memory_mb", 0.0),
    }


def _prompt_tokens_from_rounds(block: dict) -> int:
    for r in block.get("rounds", []):
        if not r.get("warmup") and r.get("ok"):
            return int(r.get("prompt_tokens", 0))
    for r in block.get("rounds", []):
        if r.get("ok"):
            return int(r.get("prompt_tokens", 0))
    return 0


def main() -> int:
    rows = []
    for path in sorted(glob.glob(os.path.join(HERE, "L1_*.json"))):
        with open(path, "r", encoding="utf-8") as f:
            rep = json.load(f)
        label = rep.get("scenario_label") or os.path.basename(path)
        for key, model_field in (("ollama", "model"), ("ollama_b", "model")):
            block = rep.get(key) or {}
            if not block.get("ok"):
                continue
            s = dict(block.get("summary") or {})
            s["_prompt_tokens"] = _prompt_tokens_from_rounds(block)
            row = _summ_row(block.get("model", key), s)
            row["scenario"] = label
            row["num_ctx"] = rep.get("num_ctx")
            row["max_tokens"] = rep.get("max_tokens")
            rows.append(row)

    if not rows:
        print("[WARN] no L1 reports found")
        return 0

    def sort_key(r):
        try:
            idx = SCENARIO_ORDER.index(r["scenario"])
        except ValueError:
            idx = 99
        return (idx, r["model"])
    rows.sort(key=sort_key)

    header = ("| 场景 | 模型 | prompt_tokens | 首token延迟avg(ms) | 首token p95(ms) | "
              "端到端avg(s) | 端到端p95(s) | decode(tok/s) | prefill(ms) | 冷启load(ms) | "
              "峰值显存(MB) | GPU利用率% | 峰值内存(MB) |")
    sep = "|" + "---|" * 13
    lines = [header, sep]
    for r in rows:
        lines.append(
            f"| {r['scenario']} | {r['model']} | {r['prompt_tokens']} | "
            f"{r['avg_first_token_ms']} | {r['p95_first_token_ms']} | "
            f"{r['avg_latency_sec']} | {r['p95_latency_sec']} | {r['avg_decode_tps']} | "
            f"{r['avg_prefill_ms']} | {r['max_load_ms']} | {r['peak_gpu_mem_mb']} | "
            f"{r['peak_gpu_util']} | {r['peak_ram_mb']} |"
        )
    table = "\n".join(lines)
    print(table)
    out = os.path.join(HERE, "L1_summary_table.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("# L1 基础性能层压测汇总\n\n")
        f.write(table + "\n")
    print(f"\n[OK] -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
