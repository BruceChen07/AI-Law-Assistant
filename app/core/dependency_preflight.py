"""服务启动前的依赖自检。

目标：让「装错环境 / 缺依赖」在启动时就以可读的方式暴露，而不是等用户点
「开始审计」后才看到一条 OCR 报错。

检查项：
- python_deps    当前进程缺不缺 OCR/审计必需的 Python 包
- mineru_cli     MinerU 命令行是否存在、能否运行
- mineru_env     MinerU 所在的解释器是否装了 pipeline 依赖
                 （mineru.exe 绑定的是它自己 venv 的 Python，和本服务无关）
- mineru_models  model_source=local 时本地权重是否完整
- embedding      向量模型文件是否存在
- llm_endpoint   llama.cpp / OpenAI 兼容端点是否可达（仅告警）
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# 服务进程本身在审计时才懒加载的模块。注意：pyclipper/omegaconf/dill 属于
# MinerU 子进程所在环境，不能要求本进程安装，否则会误报（真实案例见
# docs/mineru-ocr-setup.zh-CN.md）。
REQUIRED_MODULES = [
    ("onnxruntime", "onnxruntime"),
    ("jieba", "jieba"),
    ("numpy", "numpy"),
    ("httpx", "httpx"),
]

# MinerU 子进程所在环境必须具备的 pipeline 依赖（在其解释器里探测）。
MINERU_PIPELINE_MODULES = ("pyclipper", "omegaconf", "dill")

MINERU_PIP_CMD = 'pip install "pyclipper>=1.3.0" "omegaconf>=2.3,<3" "dill>=0.3.8,<1"'

_LAST_REPORT: Dict[str, Any] = {}


def _is_test_process() -> bool:
    return "pytest" in sys.modules or "PYTEST_CURRENT_TEST" in os.environ


def _module_present(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _resolve_mineru_cli() -> str:
    try:
        from app.core.mineru_ocr import _resolve_mineru_cli as resolve

        return str(resolve() or "")
    except Exception:
        return ""


def _cli_python(cli_path: str) -> str:
    """MinerU 命令行绑定的是它所在 venv 的 Python。"""
    if cli_path:
        candidate = Path(cli_path).resolve().parent / (
            "python.exe" if os.name == "nt" else "python")
        if candidate.exists():
            return str(candidate)
    return sys.executable


def _probe_python(python_path: str, code: str, timeout: int = 30) -> Dict[str, Any]:
    try:
        proc = subprocess.run(
            [python_path, "-c", code],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": proc.returncode == 0,
        "stdout": (proc.stdout or "").strip(),
        "stderr": (proc.stderr or "").strip()[-600:],
        "returncode": proc.returncode,
    }


def _check_python_deps() -> Dict[str, Any]:
    missing = [
        {"module": name, "install": pkg}
        for name, pkg in REQUIRED_MODULES
        if not _module_present(name)
    ]
    return {
        "name": "service_deps",
        "label": "服务依赖",
        "ok": not missing,
        "critical": True,
        "detail": {"missing": missing},
        "hint": "" if not missing else (
            "当前 Python 环境缺少: "
            + ", ".join(item["module"] for item in missing)
            + "。修复: python -m pip install "
            + " ".join(item["install"] for item in missing)
        ),
    }


def _check_mineru_cli() -> Dict[str, Any]:
    if _is_test_process():
        return {
            "name": "mineru_cli",
            "label": "MinerU 命令行",
            "ok": True,
            "critical": True,
            "skipped": True,
            "detail": {"reason": "test_process"},
            "hint": "",
        }
    cli = _resolve_mineru_cli()
    if not cli:
        return {
            "name": "mineru_cli",
            "label": "MinerU 命令行",
            "ok": False,
            "critical": True,
            "detail": {"cli": ""},
            "hint": "未找到 mineru 命令，请安装 MinerU: pip install \"mineru[pipeline]==3.1.5\"",
        }
    python_path = _cli_python(cli)
    probe = _probe_python(cli, "--version", timeout=30)
    if not probe.get("ok"):
        probe = _probe_python(cli, "--help", timeout=30)
    return {
        "name": "mineru_cli",
        "label": "MinerU 命令行",
        "ok": bool(probe.get("ok")),
        "critical": True,
        "detail": {
            "cli": cli,
            "cli_python": python_path,
            "returncode": probe.get("returncode"),
            "stderr": probe.get("stderr", ""),
        },
        "hint": "" if probe.get("ok") else (
            f"MinerU 命令行无法运行，请检查安装: {python_path} -m pip install "
            "\"mineru[pipeline]==3.1.5\""
        ),
    }


def _check_mineru_env() -> Dict[str, Any]:
    if _is_test_process():
        return {
            "name": "mineru_env",
            "label": "MinerU 运行环境",
            "ok": True,
            "critical": True,
            "skipped": True,
            "detail": {"reason": "test_process"},
            "hint": "",
        }
    cli = _resolve_mineru_cli()
    python_path = _cli_python(cli)
    code = "import " + ", ".join(MINERU_PIPELINE_MODULES) + "; print('ok')"
    probe = _probe_python(python_path, code, timeout=30) if python_path else {
        "ok": False, "error": "cli python not found"}
    ok = bool(probe.get("ok"))
    return {
        "name": "mineru_env",
        "label": "MinerU 运行环境",
        "ok": ok,
        "critical": True,
        "detail": {
            "cli": cli,
            "cli_python": python_path,
            "stderr": probe.get("stderr", "") or probe.get("error", ""),
        },
        "hint": "" if ok else (
            "MinerU 所在的 Python 环境缺少 pipeline 依赖（pyclipper/omegaconf/dill）。"
            f"修复: \"{python_path}\" -m {MINERU_PIP_CMD}"
        ),
    }


def _check_mineru_models(cfg: Dict[str, Any]) -> Dict[str, Any]:
    mineru_cfg = cfg.get("mineru") if isinstance(cfg.get("mineru"), dict) else {}
    source = str(mineru_cfg.get("model_source") or "").strip().lower()
    if source != "local":
        return {
            "name": "mineru_models",
            "label": "MinerU 本地模型",
            "ok": True,
            "critical": False,
            "skipped": True,
            "detail": {"model_source": source or "unset"},
            "hint": "",
        }
    try:
        from app.core.mineru_models import inspect_local_models

        status = inspect_local_models(cfg)
    except Exception as exc:  # pragma: no cover - defensive
        return {
            "name": "mineru_models",
            "label": "MinerU 本地模型",
            "ok": False,
            "critical": True,
            "detail": {"error": str(exc)},
            "hint": "无法检查本地模型，请确认 app/core/mineru_models.py 可用",
        }
    missing = list(status.get("missing") or [])
    return {
        "name": "mineru_models",
        "label": "MinerU 本地模型",
        "ok": bool(status.get("ok")),
        "critical": True,
        "detail": {
            "root": status.get("root", ""),
            "missing": missing[:8],
        },
        "hint": "" if status.get("ok") else (
            "本地模型不完整，缺失: " + ", ".join(missing[:5])
            + "。请把 models/mineru 补全后再启动，或把 mineru.model_source 设为在线来源"
        ),
    }


def _check_embedding(cfg: Dict[str, Any]) -> Dict[str, Any]:
    profiles = cfg.get("embedding_profiles")
    profiles = profiles if isinstance(profiles, dict) else {}
    base_dir = ""
    try:
        from app.core.config import get_config_path

        base_dir = os.path.dirname(os.path.abspath(get_config_path()))
    except Exception:
        base_dir = os.getcwd()
    missing: List[str] = []
    for lang, profile in profiles.items():
        if not isinstance(profile, dict):
            continue
        model = str(profile.get("embedding_model") or "").strip()
        if not model:
            continue
        path = model if os.path.isabs(model) else os.path.join(base_dir, model)
        if not os.path.exists(os.path.normpath(path)):
            missing.append(f"{lang}:{model}")
    return {
        "name": "embedding",
        "label": "向量模型文件",
        "ok": not missing,
        "critical": True,
        "detail": {"missing": missing},
        "hint": "" if not missing else (
            "向量模型文件缺失: " + ", ".join(missing)
            + "。请按部署文档准备 models/embedding"
        ),
    }


def _check_llm(cfg: Dict[str, Any]) -> Dict[str, Any]:
    llm_cfg = cfg.get("llm_config") if isinstance(cfg.get("llm_config"), dict) else {}
    api_base = str(llm_cfg.get("api_base") or "").strip().rstrip("/")
    provider = str(llm_cfg.get("provider") or "").strip().lower()
    if not api_base:
        return {
            "name": "llm_endpoint",
            "label": "LLM 服务",
            "ok": False,
            "critical": False,
            "detail": {"provider": provider},
            "hint": "未配置 llm_config.api_base，审计无法调用模型",
        }
    if _is_test_process():
        return {
            "name": "llm_endpoint",
            "label": "LLM 服务",
            "ok": True,
            "critical": False,
            "skipped": True,
            "detail": {"reason": "test_process"},
            "hint": "",
        }
    url = f"{api_base}/models"
    try:
        import urllib.request

        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=3) as resp:
            ok = 200 <= int(getattr(resp, "status", 0)) < 300
        error = ""
    except Exception as exc:
        ok = False
        error = str(exc)
    return {
        "name": "llm_endpoint",
        "label": "LLM 服务",
        "ok": ok,
        "critical": False,
        "detail": {"provider": provider, "api_base": api_base, "error": error},
        "hint": "" if ok else (
            f"LLM 端点不可达（{api_base}）。审计前请先启动 llama.cpp，"
            "或运行 bin/start-services.py 一并拉起"
        ),
    }


def collect_report(cfg: Dict[str, Any]) -> Dict[str, Any]:
    checks = [
        _check_python_deps(),
        _check_mineru_cli(),
        _check_mineru_env(),
        _check_mineru_models(cfg),
        _check_embedding(cfg),
        _check_llm(cfg),
    ]
    critical = [c for c in checks if c.get("critical") and not c.get("ok")]
    warnings = [
        c for c in checks
        if not c.get("critical") and not c.get("ok") and not c.get("skipped")
    ]
    return {
        "ok": not critical,
        "critical_count": len(critical),
        "warning_count": len(warnings),
        "checks": checks,
        "critical": critical,
        "warnings": warnings,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def _ensure_console_encoding() -> None:
    """Windows 控制台代码页是 UTF-8 而 Python 仍用 GBK 时，中文会乱码。"""
    if os.name != "nt":
        return
    try:
        import ctypes

        code_page = int(ctypes.windll.kernel32.GetConsoleOutputCP())
    except Exception:
        return
    if code_page != 65001:
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def format_report(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    header = "启动依赖自检"
    if report.get("ok"):
        lines.append(f"[OK] {header}：关键依赖齐全")
        if report.get("warnings"):
            lines.append(f"     有 {len(report['warnings'])} 项告警（不影响启动）")
    else:
        lines.append(f"[FAIL] {header}：发现 {report.get('critical_count', 0)} 项阻塞问题")
    for check in report.get("checks") or []:
        if check.get("skipped"):
            continue
        mark = "OK  " if check.get("ok") else (
            "FAIL" if check.get("critical") else "WARN")
        lines.append(f"  [{mark}] {check.get('label')}")
        if not check.get("ok"):
            hint = str(check.get("hint") or "").strip()
            if hint:
                lines.append(f"         → {hint}")
    return "\n".join(lines)


def run_startup_preflight(
    cfg: Dict[str, Any],
    logger: Optional[Any] = None,
    *,
    strict: Optional[bool] = None,
) -> Dict[str, Any]:
    report = collect_report(cfg)
    _LAST_REPORT.clear()
    _LAST_REPORT.update(report)

    text = format_report(report)
    if logger is not None:
        if report.get("ok"):
            logger.info("startup_dependency_check ok warnings=%s",
                        report.get("warning_count", 0))
        else:
            logger.error("startup_dependency_check failed critical=%s warnings=%s",
                         report.get("critical_count", 0),
                         report.get("warning_count", 0))
        for check in (report.get("critical") or []) + (report.get("warnings") or []):
            logger.error("startup_dependency_issue check=%s detail=%s hint=%s",
                         check.get("name"), json.dumps(
                             check.get("detail") or {}, ensure_ascii=False),
                         check.get("hint"))
    try:
        _ensure_console_encoding()
        print(text, file=sys.stderr, flush=True)
    except Exception:  # pragma: no cover - console may be unavailable
        pass

    if strict is None:
        strict = bool(cfg.get("startup_dependency_strict", False))
    if strict and not report.get("ok"):
        raise RuntimeError(
            "启动依赖自检未通过:\n" + text + "\n修复后重启服务。")
    return report


def get_last_report() -> Dict[str, Any]:
    return dict(_LAST_REPORT)
