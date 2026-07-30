import argparse
import csv
import json
import math
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from shlex import split as shlex_split


DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_LLAMACPP_URL = "http://127.0.0.1:18080"
DEFAULT_PROMPT = "请用一句话解释合同审计的核心目标。"


def _clean_text(value) -> str:
    return str(value or "").strip()


def _normalize_base_url(value: str, default_url: str) -> str:
    url = _clean_text(value) or default_url
    for suffix in ("/v1/chat/completions", "/chat/completions", "/api/chat", "/v1", "/api"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    return url.rstrip("/")


def _http_json(url: str, *, method: str = "GET", payload: dict | None = None, timeout: int = 30) -> tuple[int, dict]:
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        parsed = json.loads(raw) if raw else {}
        return int(response.status), parsed


def _wait_for_ready(url: str, health_paths: list[str], timeout: int) -> dict:
    deadline = time.time() + max(1, timeout)
    attempts = []
    while time.time() <= deadline:
        for path in health_paths:
            full_url = url.rstrip("/") + path
            started = time.perf_counter()
            try:
                status, payload = _http_json(full_url, timeout=5)
                if status == 200:
                    return {
                        "ok": True,
                        "url": full_url,
                        "latency_ms": int((time.perf_counter() - started) * 1000),
                        "attempts": attempts,
                        "payload": payload,
                    }
            except Exception as exc:
                attempts.append({"url": full_url, "error": str(exc)})
        time.sleep(1)
    return {
        "ok": False,
        "url": "",
        "latency_ms": 0,
        "attempts": attempts,
        "payload": {},
    }


def _launch_runtime(command: str, cwd: Path, log_path: Path) -> dict:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("a", encoding="utf-8")
    creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    proc = subprocess.Popen(
        shlex_split(command, posix=False),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        cwd=str(cwd),
        creationflags=creationflags,
    )
    return {
        "pid": proc.pid,
        "log_path": str(log_path),
        "command": command,
    }


def _tasklist_memory_mb(process_name: str) -> float:
    if not _clean_text(process_name):
        return 0.0
    try:
        result = subprocess.run(
            ["tasklist", "/FI",
                f"IMAGENAME eq {process_name}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return 0.0
        total_mb = 0.0
        for row in csv.reader([line for line in result.stdout.splitlines() if line.strip()]):
            if len(row) < 5 or "No tasks are running" in row[0]:
                continue
            raw = row[4].replace(",", "").replace(
                " K", "").replace(" KB", "").strip()
            try:
                total_mb += float(raw) / 1024.0
            except Exception:
                continue
        return round(total_mb, 2)
    except Exception:
        return 0.0


def _gpu_snapshot() -> dict:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return {"available": False, "error": result.stderr.strip() or result.stdout.strip()}
        rows = []
        for line in result.stdout.splitlines():
            parts = [item.strip() for item in line.split(",")]
            if len(parts) != 4:
                continue
            rows.append(
                {
                    "name": parts[0],
                    "gpu_util_percent": float(parts[1]),
                    "memory_used_mb": float(parts[2]),
                    "memory_total_mb": float(parts[3]),
                }
            )
        return {"available": bool(rows), "gpus": rows}
    except Exception as exc:
        return {"available": False, "error": str(exc)}


class Sampler:
    def __init__(self, process_name: str, sample_interval_sec: float = 0.5):
        self.process_name = process_name
        self.sample_interval_sec = max(0.1, sample_interval_sec)
        self.samples: list[dict] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        def _run():
            while not self._stop.is_set():
                self.samples.append(
                    {
                        "ts": time.time(),
                        "memory_mb": _tasklist_memory_mb(self.process_name),
                        "gpu": _gpu_snapshot(),
                    }
                )
                self._stop.wait(self.sample_interval_sec)

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def summary(self) -> dict:
        memory_values = [sample["memory_mb"]
                         for sample in self.samples if sample.get("memory_mb") is not None]
        gpu_utils = []
        gpu_memory = []
        for sample in self.samples:
            gpu = sample.get("gpu") or {}
            rows = gpu.get("gpus") if isinstance(gpu.get("gpus"), list) else []
            if rows:
                gpu_utils.append(
                    max(float(item.get("gpu_util_percent", 0)) for item in rows))
                gpu_memory.append(
                    max(float(item.get("memory_used_mb", 0)) for item in rows))
        return {
            "sample_count": len(self.samples),
            "peak_memory_mb": round(max(memory_values), 2) if memory_values else 0.0,
            "avg_memory_mb": round(sum(memory_values) / len(memory_values), 2) if memory_values else 0.0,
            "peak_gpu_util_percent": round(max(gpu_utils), 2) if gpu_utils else 0.0,
            "avg_gpu_util_percent": round(sum(gpu_utils) / len(gpu_utils), 2) if gpu_utils else 0.0,
            "peak_gpu_memory_mb": round(max(gpu_memory), 2) if gpu_memory else 0.0,
        }


def _estimate_tokens(text: str) -> int:
    s = _clean_text(text)
    if not s:
        return 0
    cjk = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
    non_cjk = len(s) - cjk
    return max(1, int(cjk * 1.1 + non_cjk / 3.8))


def _extract_openai_content(payload: dict) -> tuple[str, int, int]:
    choices = payload.get("choices") if isinstance(
        payload.get("choices"), list) else []
    message = choices[0].get("message") if choices and isinstance(
        choices[0], dict) else {}
    content = _clean_text(message.get("content")
                          if isinstance(message, dict) else "")
    usage = payload.get("usage") if isinstance(
        payload.get("usage"), dict) else {}
    prompt_tokens = int(usage.get("prompt_tokens")
                        or _estimate_tokens(DEFAULT_PROMPT))
    completion_tokens = int(usage.get("completion_tokens")
                            or _estimate_tokens(content))
    return content, prompt_tokens, completion_tokens


def _extract_ollama_content(payload: dict) -> tuple[str, int, int]:
    message = payload.get("message") if isinstance(
        payload.get("message"), dict) else {}
    content = _clean_text(message.get("content"))
    prompt_tokens = int(payload.get("prompt_eval_count")
                        or _estimate_tokens(DEFAULT_PROMPT))
    completion_tokens = int(payload.get("eval_count")
                            or _estimate_tokens(content))
    return content, prompt_tokens, completion_tokens


def _extract_ollama_timings(payload: dict) -> dict:
    def _ns_to_ms(key: str) -> float:
        try:
            return round(float(payload.get(key) or 0) / 1_000_000.0, 2)
        except Exception:
            return 0.0

    load_ms = _ns_to_ms("load_duration")
    prefill_ms = _ns_to_ms("prompt_eval_duration")
    eval_ms = _ns_to_ms("eval_duration")
    eval_count = int(payload.get("eval_count") or 0)
    prompt_eval_count = int(payload.get("prompt_eval_count") or 0)
    decode_tps = round(eval_count * 1000.0 / eval_ms, 2) if eval_ms > 0 else 0.0
    prefill_tps = round(prompt_eval_count * 1000.0 /
                        prefill_ms, 2) if prefill_ms > 0 else 0.0
    return {
        "load_duration_ms": load_ms,
        "prompt_eval_ms": prefill_ms,
        "eval_duration_ms": eval_ms,
        "total_duration_ms": _ns_to_ms("total_duration"),
        "first_token_ms": round(load_ms + prefill_ms, 2),
        "decode_tokens_per_sec": decode_tps,
        "prefill_tokens_per_sec": prefill_tps,
    }


def _ollama_unload_model(base_url: str, model: str) -> None:
    try:
        _http_json(
            base_url.rstrip("/") + "/api/generate",
            method="POST",
            payload={"model": model, "keep_alive": 0},
            timeout=60,
        )
    except Exception:
        pass


def _request_payload(runtime: str, model: str, prompt: str, max_tokens: int, num_ctx: int = 0) -> tuple[str, dict]:
    if runtime == "ollama":
        options = {
            "temperature": 0,
            "num_predict": max_tokens,
        }
        if num_ctx > 0:
            options["num_ctx"] = num_ctx
        return (
            "/api/chat",
            {
                "model": model,
                "messages": [
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "think": False,
                "options": options,
            },
        )
    return (
        "/v1/chat/completions",
        {
            "model": model,
            "messages": [
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "max_tokens": max_tokens,
        },
    )


def _benchmark_runtime(
    *,
    runtime: str,
    base_url: str,
    model: str,
    process_name: str,
    prompt: str,
    max_tokens: int,
    rounds: int,
    timeout: int,
    warmup: int,
    start_command: str,
    repo_root: Path,
    num_ctx: int = 0,
) -> dict:
    health_paths = [
        "/api/version", "/v1/models"] if runtime == "ollama" else ["/health", "/v1/models"]
    launch = {
        "attempted": False,
        "started": False,
        "pid": 0,
        "log_path": "",
        "command": "",
        "ready_after_launch_sec": 0.0,
    }
    ready = _wait_for_ready(base_url, health_paths, min(timeout, 5))
    if (not ready["ok"]) and _clean_text(start_command):
        launch["attempted"] = True
        launch_path = repo_root / "logs" / \
            "benchmarks" / f"{runtime}-launch.log"
        launch_meta = _launch_runtime(start_command, repo_root, launch_path)
        launch.update(launch_meta)
        launch["started"] = True
        launch_started = time.perf_counter()
        ready = _wait_for_ready(base_url, health_paths, timeout)
        launch["ready_after_launch_sec"] = round(
            max(0.0, time.perf_counter() - launch_started), 4)
    result = {
        "runtime": runtime,
        "base_url": base_url,
        "model": model,
        "ready": ready,
        "launch": launch,
        "rounds": [],
        "summary": {},
        "ok": False,
    }
    if not ready["ok"]:
        result["summary"] = {"error": f"{runtime} endpoint is not reachable"}
        return result

    total_runs = max(0, warmup) + max(1, rounds)
    metrics = []
    for idx in range(total_runs):
        path, payload = _request_payload(
            runtime, model, prompt, max_tokens, num_ctx)
        sampler = Sampler(process_name)
        sampler.start()
        started = time.perf_counter()
        try:
            status, response = _http_json(
                base_url.rstrip("/") + path,
                method="POST",
                payload=payload,
                timeout=timeout,
            )
            latency_sec = max(0.001, time.perf_counter() - started)
            if runtime == "ollama":
                content, prompt_tokens, completion_tokens = _extract_ollama_content(
                    response)
            else:
                content, prompt_tokens, completion_tokens = _extract_openai_content(
                    response)
            round_result = {
                "index": idx,
                "warmup": idx < warmup,
                "ok": status == 200,
                "status_code": status,
                "latency_sec": round(latency_sec, 4),
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "tokens_per_sec": round(completion_tokens / latency_sec, 2),
                "response_chars": len(content),
                "content_preview": content[:200],
            }
            if runtime == "ollama":
                round_result["ollama_timings"] = _extract_ollama_timings(
                    response)
        except Exception as exc:
            latency_sec = max(0.001, time.perf_counter() - started)
            round_result = {
                "index": idx,
                "warmup": idx < warmup,
                "ok": False,
                "status_code": 0,
                "latency_sec": round(latency_sec, 4),
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "tokens_per_sec": 0.0,
                "response_chars": 0,
                "content_preview": "",
                "error": str(exc),
            }
        finally:
            sampler.stop()
            round_result["resource"] = sampler.summary()
        result["rounds"].append(round_result)
        if not round_result["warmup"] and round_result["ok"]:
            metrics.append(round_result)

    if not metrics:
        result["summary"] = {
            "error": f"{runtime} benchmark did not produce successful measured rounds"}
        return result

    latency_values = [item["latency_sec"] for item in metrics]
    tps_values = [item["tokens_per_sec"] for item in metrics]
    mem_values = [float(item["resource"]["peak_memory_mb"])
                  for item in metrics]
    gpu_util_values = [
        float(item["resource"]["peak_gpu_util_percent"]) for item in metrics]
    gpu_mem_values = [float(item["resource"]["peak_gpu_memory_mb"])
                      for item in metrics]
    result["summary"] = {
        "successful_rounds": len(metrics),
        "avg_latency_sec": round(sum(latency_values) / len(latency_values), 4),
        "p95_latency_sec": round(sorted(latency_values)[math.ceil(len(latency_values) * 0.95) - 1], 4),
        "avg_tokens_per_sec": round(sum(tps_values) / len(tps_values), 2),
        "peak_memory_mb": round(max(mem_values), 2),
        "peak_gpu_util_percent": round(max(gpu_util_values), 2) if gpu_util_values else 0.0,
        "peak_gpu_memory_mb": round(max(gpu_mem_values), 2) if gpu_mem_values else 0.0,
    }
    timing_rows = [item.get("ollama_timings")
                   for item in metrics if item.get("ollama_timings")]
    if timing_rows:
        first_token_values = [float(row["first_token_ms"])
                              for row in timing_rows]
        prefill_values = [float(row["prompt_eval_ms"]) for row in timing_rows]
        decode_values = [float(row["decode_tokens_per_sec"])
                         for row in timing_rows]
        load_values = [float(row["load_duration_ms"]) for row in timing_rows]
        result["summary"].update(
            {
                "avg_first_token_ms": round(sum(first_token_values) / len(first_token_values), 2),
                "p95_first_token_ms": round(sorted(first_token_values)[math.ceil(len(first_token_values) * 0.95) - 1], 2),
                "avg_prefill_ms": round(sum(prefill_values) / len(prefill_values), 2),
                "avg_decode_tokens_per_sec": round(sum(decode_values) / len(decode_values), 2),
                "max_load_duration_ms": round(max(load_values), 2),
            }
        )
    result["ok"] = True
    return result


def _improvement(ollama_value: float, llamacpp_value: float, higher_better: bool) -> dict:
    if ollama_value == 0:
        return {"delta": 0.0, "ratio": 0.0}
    if higher_better:
        delta = llamacpp_value - ollama_value
        ratio = llamacpp_value / ollama_value
    else:
        delta = ollama_value - llamacpp_value
        ratio = ollama_value / llamacpp_value if llamacpp_value else 0.0
    return {"delta": round(delta, 4), "ratio": round(ratio, 4)}


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Benchmark Ollama and llama.cpp on the same hardware and emit a JSON comparison report."
    )
    parser.add_argument(
        "--ollama-url", default=DEFAULT_OLLAMA_URL, help="Ollama base URL.")
    parser.add_argument(
        "--llama-cpp-url", default=DEFAULT_LLAMACPP_URL, help="llama.cpp base URL.")
    parser.add_argument("--ollama-model", required=True,
                        help="Ollama model name for the benchmark.")
    parser.add_argument("--ollama-model-b", default="",
                        help="Optional second Ollama model; enables ollama-vs-ollama comparison on the same endpoint and skips llama.cpp.")
    parser.add_argument("--llama-cpp-model", default="",
                        help="llama.cpp model alias for the benchmark (required unless --ollama-model-b is used).")
    parser.add_argument("--ollama-process", default="ollama.exe",
                        help="Process name used for memory sampling.")
    parser.add_argument("--llama-cpp-process", default="llama-server.exe",
                        help="Process name used for memory sampling.")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT,
                        help="Prompt used for the benchmark.")
    parser.add_argument("--prompt-file", default="",
                        help="Optional file containing the benchmark prompt.")
    parser.add_argument("--rounds", type=int, default=3,
                        help="Measured rounds per runtime.")
    parser.add_argument("--warmup-rounds", type=int,
                        default=1, help="Warmup rounds per runtime.")
    parser.add_argument("--max-tokens", type=int,
                        default=256, help="Max generation tokens.")
    parser.add_argument("--num-ctx", type=int, default=0,
                        help="Optional Ollama num_ctx override for each request.")
    parser.add_argument("--scenario-label", default="",
                        help="Optional scenario label recorded in the report.")
    parser.add_argument("--unload-between", action="store_true", default=False,
                        help="Unload each Ollama model after its benchmark (keep_alive=0) to avoid VRAM contention.")
    parser.add_argument("--single-model", action="store_true", default=False,
                        help="Benchmark only --ollama-model in isolation (skip the second runtime).")
    parser.add_argument("--timeout", type=int, default=120,
                        help="Request timeout in seconds.")
    parser.add_argument("--ollama-start-command", default="",
                        help="Optional command used to cold-start Ollama.")
    parser.add_argument("--llama-cpp-start-command", default="",
                        help="Optional command used to cold-start llama.cpp.")
    parser.add_argument("--report-path", default="",
                        help="Optional explicit report output path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    prompt = _clean_text(args.prompt)
    if args.prompt_file:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8").strip()
    dual_ollama = bool(_clean_text(args.ollama_model_b))
    single_model = bool(args.single_model)
    if not single_model and not dual_ollama and not _clean_text(args.llama_cpp_model):
        print("[ERROR] Provide --llama-cpp-model or --ollama-model-b (or --single-model).")
        return 1
    mode_slug = ("ollama-single" if single_model
                 else "ollama-vs-ollama" if dual_ollama else "ollama-vs-llamacpp")
    report_path = Path(args.report_path).resolve() if args.report_path else (
        repo_root
        / "plan"
        / "local-llm-llamacpp"
        / "reports"
        / f"benchmark-{mode_slug}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)

    ollama_base_url = _normalize_base_url(args.ollama_url, DEFAULT_OLLAMA_URL)
    if dual_ollama and args.unload_between:
        _ollama_unload_model(ollama_base_url, args.ollama_model_b)
    ollama_result = _benchmark_runtime(
        runtime="ollama",
        base_url=ollama_base_url,
        model=args.ollama_model,
        process_name=args.ollama_process,
        prompt=prompt,
        max_tokens=args.max_tokens,
        rounds=args.rounds,
        timeout=args.timeout,
        warmup=args.warmup_rounds,
        start_command=args.ollama_start_command,
        repo_root=repo_root,
        num_ctx=args.num_ctx,
    )
    if single_model:
        if args.unload_between:
            _ollama_unload_model(ollama_base_url, args.ollama_model)
        second_result = {"ok": False, "skipped": True}
    elif dual_ollama:
        if args.unload_between:
            _ollama_unload_model(ollama_base_url, args.ollama_model)
        second_result = _benchmark_runtime(
            runtime="ollama",
            base_url=ollama_base_url,
            model=args.ollama_model_b,
            process_name=args.ollama_process,
            prompt=prompt,
            max_tokens=args.max_tokens,
            rounds=args.rounds,
            timeout=args.timeout,
            warmup=args.warmup_rounds,
            start_command=args.ollama_start_command,
            repo_root=repo_root,
            num_ctx=args.num_ctx,
        )
        if args.unload_between:
            _ollama_unload_model(ollama_base_url, args.ollama_model_b)
    else:
        second_result = _benchmark_runtime(
            runtime="llama_cpp",
            base_url=_normalize_base_url(
                args.llama_cpp_url, DEFAULT_LLAMACPP_URL),
            model=args.llama_cpp_model,
            process_name=args.llama_cpp_process,
            prompt=prompt,
            max_tokens=args.max_tokens,
            rounds=args.rounds,
            timeout=args.timeout,
            warmup=args.warmup_rounds,
            start_command=args.llama_cpp_start_command,
            repo_root=repo_root,
        )

    comparison = {"ready": False}
    if single_model:
        comparison = {"ready": False, "single_model": True}
    if not single_model and ollama_result.get("ok") and second_result.get("ok"):
        ollama_summary = ollama_result["summary"]
        second_summary = second_result["summary"]
        comparison = {
            "ready": True,
            "avg_latency_sec": _improvement(
                float(ollama_summary["avg_latency_sec"]),
                float(second_summary["avg_latency_sec"]),
                higher_better=False,
            ),
            "avg_tokens_per_sec": _improvement(
                float(ollama_summary["avg_tokens_per_sec"]),
                float(second_summary["avg_tokens_per_sec"]),
                higher_better=True,
            ),
            "cold_start_ready_sec": _improvement(
                float(ollama_result.get("launch", {}).get(
                    "ready_after_launch_sec", 0.0)),
                float(second_result.get("launch", {}).get(
                    "ready_after_launch_sec", 0.0)),
                higher_better=False,
            ),
            "peak_memory_mb": _improvement(
                float(ollama_summary["peak_memory_mb"]),
                float(second_summary["peak_memory_mb"]),
                higher_better=False,
            ),
            "peak_gpu_util_percent": _improvement(
                float(ollama_summary["peak_gpu_util_percent"]),
                float(second_summary["peak_gpu_util_percent"]),
                higher_better=True,
            ),
            "peak_gpu_memory_mb": _improvement(
                float(ollama_summary["peak_gpu_memory_mb"]),
                float(second_summary["peak_gpu_memory_mb"]),
                higher_better=False,
            ),
        }
        if dual_ollama and "avg_first_token_ms" in ollama_summary and "avg_first_token_ms" in second_summary:
            comparison["avg_first_token_ms"] = _improvement(
                float(ollama_summary["avg_first_token_ms"]),
                float(second_summary["avg_first_token_ms"]),
                higher_better=False,
            )
            comparison["avg_decode_tokens_per_sec"] = _improvement(
                float(ollama_summary["avg_decode_tokens_per_sec"]),
                float(second_summary["avg_decode_tokens_per_sec"]),
                higher_better=True,
            )

    second_key = "ollama_b" if dual_ollama else "llama_cpp"
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode_slug,
        "scenario_label": _clean_text(args.scenario_label),
        "runtime_policy": {
            "edge_only": True,
            "cloud_fallback_allowed": False,
        },
        "hardware_note": (
            "Two Ollama models benchmarked sequentially on the same endpoint with identical prompts; models optionally unloaded between runs to avoid VRAM contention."
            if dual_ollama
            else "Run the legacy Ollama baseline and the target llama.cpp runtime sequentially on the same host with the same business prompt set; ModelScope llama.cpp artifacts are treated as an independent runtime target."
        ),
        "prompt_chars": len(prompt),
        "prompt_tokens_estimated": _estimate_tokens(prompt),
        "num_ctx": args.num_ctx,
        "max_tokens": args.max_tokens,
        "rounds": args.rounds,
        "warmup_rounds": args.warmup_rounds,
        "ollama": ollama_result,
        second_key: second_result,
        "comparison": comparison,
        "ok": bool(ollama_result.get("ok")) if single_model else bool(comparison.get("ready")),
    }
    with report_path.open("w", encoding="utf-8") as file_obj:
        json.dump(report, file_obj, ensure_ascii=False, indent=2)
        file_obj.write("\n")

    print(f"[REPORT] {report_path}")
    print(f"[RESULT] ok={report['ok']}")
    if comparison.get("ready"):
        print(
            f"[COMPARE] latency gain ratio={comparison['avg_latency_sec']['ratio']}")
        print(
            f"[COMPARE] throughput gain ratio={comparison['avg_tokens_per_sec']['ratio']}")
    else:
        print("[COMPARE] benchmark comparison not ready; inspect the JSON report for failed runtime checks.")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
