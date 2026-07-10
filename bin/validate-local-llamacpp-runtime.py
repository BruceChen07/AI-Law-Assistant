import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


DEFAULT_CONFIG_PATH = Path("app/config.json")
DEFAULT_LLAMACPP_URL = "http://127.0.0.1:18080"
DEFAULT_LLAMACPP_SMALL_URL = "http://127.0.0.1:18081"
DEFAULT_BACKEND_URL = "http://127.0.0.1:8000"


def _clean_text(value) -> str:
    return str(value or "").strip()


def _normalize_host(value: str, default_host: str) -> str:
    host = _clean_text(value) or default_host
    for suffix in ("/v1/chat/completions", "/chat/completions", "/api/chat", "/api/generate", "/v1"):
        if host.endswith(suffix):
            host = host[: -len(suffix)]
    return host.rstrip("/")


def _http_json(url: str, *, method: str = "GET", payload: dict | None = None, timeout: int = 10) -> tuple[int, dict]:
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


def _probe_endpoint(name: str, url: str, timeout: int) -> dict:
    started = time.perf_counter()
    try:
        status, payload = _http_json(url, timeout=timeout)
        return {
            "name": name,
            "ok": True,
            "status_code": status,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "payload": payload,
            "error": "",
        }
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        return {
            "name": name,
            "ok": False,
            "status_code": int(exc.code),
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "payload": {},
            "error": body or str(exc),
        }
    except Exception as exc:
        return {
            "name": name,
            "ok": False,
            "status_code": 0,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "payload": {},
            "error": str(exc),
        }


def _load_config(config_path: Path) -> dict:
    with config_path.open("r", encoding="utf-8") as file_obj:
        return json.load(file_obj)


def _config_summary(cfg: dict) -> dict:
    llm_cfg = cfg.get("llm_config") if isinstance(
        cfg.get("llm_config"), dict) else {}
    local_cfg = cfg.get("local_llm") if isinstance(
        cfg.get("local_llm"), dict) else {}
    main_cfg = local_cfg.get("main_model") if isinstance(
        local_cfg.get("main_model"), dict) else {}
    small_cfg = local_cfg.get("small_model") if isinstance(
        local_cfg.get("small_model"), dict) else {}
    network_policy = cfg.get("network_policy") if isinstance(
        cfg.get("network_policy"), dict) else {}
    return {
        "llm_provider": _clean_text(llm_cfg.get("provider")),
        "llm_api_base": _clean_text(llm_cfg.get("api_base")),
        "llm_model": _clean_text(llm_cfg.get("model")),
        "local_llm_enabled": bool(local_cfg.get("enabled", False)),
        "main_provider": _clean_text(main_cfg.get("provider")),
        "main_api_base": _clean_text(main_cfg.get("api_base")),
        "main_model": _clean_text(main_cfg.get("model")),
        "small_provider": _clean_text(small_cfg.get("provider")),
        "small_api_base": _clean_text(small_cfg.get("api_base")),
        "small_model": _clean_text(small_cfg.get("model")),
        "offline_mode": _clean_text(network_policy.get("mode")),
        "allowed_hosts": list(network_policy.get("allowed_hosts") or []),
    }


def _evaluate_config(summary: dict) -> list[dict]:
    findings: list[dict] = []

    def add_result(check: str, ok: bool, detail: str) -> None:
        findings.append({"check": check, "ok": ok, "detail": detail})

    add_result(
        "llm_config.provider == llama_cpp",
        summary["llm_provider"] == "llama_cpp",
        f"current={summary['llm_provider'] or '<empty>'}",
    )
    add_result(
        "local_llm.enabled == true",
        bool(summary["local_llm_enabled"]),
        f"current={summary['local_llm_enabled']}",
    )
    add_result(
        "local_llm.main_model.provider == llama_cpp",
        summary["main_provider"] == "llama_cpp",
        f"current={summary['main_provider'] or '<empty>'}",
    )
    add_result(
        "local_llm.small_model.provider == llama_cpp",
        summary["small_provider"] == "llama_cpp",
        f"current={summary['small_provider'] or '<empty>'}",
    )
    add_result(
        "network_policy.mode == offline_strict",
        summary["offline_mode"] == "offline_strict",
        f"current={summary['offline_mode'] or '<empty>'}",
    )
    allowed_hosts = {str(item).strip().lower()
                     for item in summary["allowed_hosts"]}
    add_result(
        "allowed_hosts contains loopback",
        "127.0.0.1" in allowed_hosts or "localhost" in allowed_hosts,
        f"current={sorted(allowed_hosts)}",
    )
    add_result(
        "allowed_hosts contains llamacpp.intra",
        "llamacpp.intra" in allowed_hosts,
        f"current={sorted(allowed_hosts)}",
    )
    return findings


def _extract_model_names(models_payload: dict) -> list[str]:
    rows = models_payload.get("data") if isinstance(
        models_payload.get("data"), list) else []
    return [
        _clean_text(item.get("id") or item.get("model"))
        for item in rows
        if isinstance(item, dict) and _clean_text(item.get("id") or item.get("model"))
    ]


def _extract_ollama_names(models_payload: dict) -> list[str]:
    rows = models_payload.get("models") if isinstance(
        models_payload.get("models"), list) else []
    return [
        _clean_text(item.get("name") or item.get("model"))
        for item in rows
        if isinstance(item, dict) and _clean_text(item.get("name") or item.get("model"))
    ]


def _chat_smoke(base_url: str, model: str, timeout: int) -> dict:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a concise local model."},
            {"role": "user", "content": "Reply with exactly: OK"},
        ],
        "temperature": 0,
        "max_tokens": 16,
    }
    started = time.perf_counter()
    try:
        status, response = _http_json(
            f"{base_url.rstrip('/')}/v1/chat/completions",
            method="POST",
            payload=payload,
            timeout=timeout,
        )
        choices = response.get("choices") if isinstance(
            response.get("choices"), list) else []
        message = choices[0].get("message") if choices and isinstance(
            choices[0], dict) else {}
        content = _clean_text(message.get("content")
                              if isinstance(message, dict) else "")
        return {
            "ok": True,
            "status_code": status,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "content": content,
            "error": "",
        }
    except Exception as exc:
        return {
            "ok": False,
            "status_code": 0,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "content": "",
            "error": str(exc),
        }


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Validate local llama.cpp runtime cutover readiness and emit a JSON report."
    )
    parser.add_argument(
        "--config-path",
        default=str((repo_root / DEFAULT_CONFIG_PATH).resolve()),
        help="Path to config JSON used for local runtime validation.",
    )
    parser.add_argument(
        "--llama-cpp-url",
        default=DEFAULT_LLAMACPP_URL,
        help="Base URL for local llama.cpp server.",
    )
    parser.add_argument(
        "--small-llama-cpp-url",
        default=DEFAULT_LLAMACPP_SMALL_URL,
        help="Base URL for the small-model llama.cpp server.",
    )
    parser.add_argument(
        "--backend-url",
        default=DEFAULT_BACKEND_URL,
        help="Base URL for local app backend.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=15,
        help="Per-request timeout in seconds.",
    )
    parser.add_argument(
        "--skip-backend",
        action="store_true",
        help="Skip backend /health validation.",
    )
    parser.add_argument(
        "--skip-chat",
        action="store_true",
        help="Skip direct llama.cpp chat smoke test.",
    )
    parser.add_argument(
        "--report-path",
        default="",
        help="Optional explicit report output path.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    config_path = Path(args.config_path).resolve()
    if not config_path.exists():
        print(f"[ERROR] config not found: {config_path}")
        return 1

    cfg = _load_config(config_path)
    summary = _config_summary(cfg)
    config_checks = _evaluate_config(summary)

    llama_cpp_url = _normalize_host(
        args.llama_cpp_url or summary["main_api_base"], DEFAULT_LLAMACPP_URL)
    small_llama_cpp_url = _normalize_host(
        args.small_llama_cpp_url or summary["small_api_base"], DEFAULT_LLAMACPP_SMALL_URL)
    backend_url = _normalize_host(args.backend_url, DEFAULT_BACKEND_URL)

    llama_models = _probe_endpoint(
        "llama_cpp_main_models", f"{llama_cpp_url}/v1/models", args.timeout)
    small_llama_models = _probe_endpoint(
        "llama_cpp_small_models", f"{small_llama_cpp_url}/v1/models", args.timeout)
    backend_health = {
        "name": "backend_health",
        "skipped": bool(args.skip_backend),
    }
    if not args.skip_backend:
        backend_health = _probe_endpoint(
            "backend_health", f"{backend_url}/health", args.timeout)

    llama_model_names = _extract_model_names(llama_models.get("payload") or {})
    small_llama_model_names = _extract_model_names(
        small_llama_models.get("payload") or {})

    smoke_result = {
        "skipped": bool(args.skip_chat),
        "ok": False,
        "status_code": 0,
        "latency_ms": 0,
        "content": "",
        "error": "",
    }
    if not args.skip_chat:
        target_model = summary["main_model"] or (
            llama_model_names[0] if llama_model_names else "")
        if target_model:
            smoke_result = _chat_smoke(
                llama_cpp_url, target_model, args.timeout)
            smoke_result["model"] = target_model
        else:
            smoke_result["error"] = "no llama.cpp model available for smoke test"

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config_path": str(config_path),
        "repo_root": str(repo_root),
        "runtime_policy": {
            "edge_only": True,
            "cloud_fallback_allowed": False,
        },
        "config_summary": summary,
        "config_checks": config_checks,
        "service_checks": {
            "llama_cpp_main_models": llama_models,
            "llama_cpp_small_models": small_llama_models,
            "backend_health": backend_health,
        },
        "discovered_models": {
            "llama_cpp_main": llama_model_names,
            "llama_cpp_small": small_llama_model_names,
        },
        "smoke_chat": smoke_result,
    }

    ok = all(item["ok"] for item in config_checks)
    ok = ok and bool(llama_models.get("ok", False))
    ok = ok and bool(small_llama_models.get("ok", False))
    if not args.skip_chat:
        ok = ok and bool(smoke_result.get("ok", False))
    if not args.skip_backend:
        ok = ok and bool(backend_health.get("ok", False))
    report["ok"] = ok

    report_path = Path(args.report_path).resolve() if args.report_path else (
        repo_root
        / "plan"
        / "local-llm-llamacpp"
        / "reports"
        / f"runtime-validation-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8") as file_obj:
        json.dump(report, file_obj, ensure_ascii=False, indent=2)
        file_obj.write("\n")

    print(f"[REPORT] {report_path}")
    print(f"[RESULT] ok={report['ok']}")
    print(
        f"[CHECK] llama.cpp main /v1/models ok={llama_models.get('ok', False)}")
    print(
        f"[CHECK] llama.cpp small /v1/models ok={small_llama_models.get('ok', False)}")
    if not args.skip_backend:
        print(f"[CHECK] backend /health ok={backend_health.get('ok', False)}")
    if not args.skip_chat:
        print(
            f"[CHECK] llama.cpp smoke chat ok={smoke_result.get('ok', False)}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
