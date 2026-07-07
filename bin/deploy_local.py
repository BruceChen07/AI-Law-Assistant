from __future__ import annotations

import argparse
import ctypes
import importlib.util
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import venv
from pathlib import Path
from typing import Any, Dict, List, Optional


REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "app"
WEB_DIR = REPO_ROOT / "web"
RUNTIME_DIR = REPO_ROOT / ".runtime"
REPORT_DIR = RUNTIME_DIR / "reports"
LOG_DIR = RUNTIME_DIR / "logs"
DEFAULT_CONFIG_PATH = APP_DIR / "config.json"
EXAMPLE_CONFIG_PATH = APP_DIR / "config.example.json"
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_TRANSLATION_MODEL = "tencent/HY-MT1.5-1.8B"


class DeployError(RuntimeError):
    pass


def log(message: str) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {message}", flush=True)


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as file_obj:
        return json.load(file_obj)


def save_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file_obj:
        json.dump(payload, file_obj, ensure_ascii=False, indent=2)
        file_obj.write("\n")


def run_command(
    cmd: List[str],
    *,
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
    check: bool = True,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    printable = " ".join(cmd)
    log(f"$ {printable}")
    result = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        capture_output=capture_output,
    )
    if capture_output:
        if result.stdout:
            print(result.stdout, end="", flush=True)
        if result.stderr:
            print(result.stderr, end="", flush=True)
    if check and result.returncode != 0:
        raise DeployError(f"命令执行失败: {printable} (exit={result.returncode})")
    return result


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def detect_os() -> Dict[str, str]:
    system = platform.system().lower()
    info = {"system": system, "release": platform.release(), "machine": platform.machine()}
    if system == "linux":
        os_release = Path("/etc/os-release")
        if os_release.exists():
            data: Dict[str, str] = {}
            for line in os_release.read_text(encoding="utf-8").splitlines():
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                data[key.strip()] = value.strip().strip('"')
            info["distro_id"] = data.get("ID", "")
            info["distro_like"] = data.get("ID_LIKE", "")
            info["distro_name"] = data.get("PRETTY_NAME", "")
    return info


def get_python_version_text() -> str:
    return ".".join(str(x) for x in sys.version_info[:3])


def get_node_version_text() -> str:
    if not command_exists("node"):
        return ""
    try:
        result = run_command(["node", "--version"], capture_output=True, check=True)
        return str(result.stdout).strip().lstrip("v")
    except Exception:
        return ""


def parse_major(version_text: str) -> int:
    cleaned = str(version_text or "").strip().lstrip("v")
    if not cleaned:
        return 0
    parts = cleaned.split(".")
    try:
        return int(parts[0])
    except Exception:
        return 0


def get_total_memory_bytes() -> int:
    system = platform.system().lower()
    if system == "windows":
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullTotalPhys)
        return 0
    if hasattr(os, "sysconf"):
        try:
            page_size = int(os.sysconf("SC_PAGE_SIZE"))
            page_count = int(os.sysconf("SC_PHYS_PAGES"))
            return page_size * page_count
        except Exception:
            pass
    if system == "darwin":
        try:
            result = run_command(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True,
                check=True,
            )
            return int(str(result.stdout).strip())
        except Exception:
            return 0
    return 0


def get_physical_cores() -> int:
    system = platform.system().lower()
    if system == "windows":
        try:
            result = run_command(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "(Get-CimInstance Win32_Processor | Measure-Object -Property NumberOfCores -Sum).Sum",
                ],
                capture_output=True,
                check=True,
            )
            return int(str(result.stdout).strip() or "0")
        except Exception:
            return os.cpu_count() or 0
    if system == "darwin":
        try:
            result = run_command(
                ["sysctl", "-n", "hw.physicalcpu"],
                capture_output=True,
                check=True,
            )
            return int(str(result.stdout).strip() or "0")
        except Exception:
            return os.cpu_count() or 0
    if system == "linux":
        try:
            result = run_command(["lscpu", "-p=CORE,SOCKET"], capture_output=True, check=True)
            pairs = set()
            for line in str(result.stdout).splitlines():
                if not line or line.startswith("#"):
                    continue
                parts = [item.strip() for item in line.split(",")]
                if len(parts) >= 2:
                    pairs.add((parts[0], parts[1]))
            if pairs:
                return len(pairs)
        except Exception:
            pass
    return os.cpu_count() or 0


def get_gpu_info() -> List[Dict[str, str]]:
    if not command_exists("nvidia-smi"):
        return []
    try:
        result = run_command(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader",
            ],
            capture_output=True,
            check=True,
        )
    except Exception:
        return []
    rows = []
    for line in str(result.stdout).splitlines():
        parts = [item.strip() for item in line.split(",")]
        if len(parts) >= 3:
            rows.append(
                {
                    "name": parts[0],
                    "memory_total": parts[1],
                    "driver_version": parts[2],
                }
            )
    return rows


def bytes_to_gb(value: int) -> float:
    return round(float(value) / (1024 ** 3), 2)


def read_text_tail(path: Path, max_chars: int = 2000) -> str:
    if not path.exists():
        return ""
    data = path.read_text(encoding="utf-8", errors="ignore")
    if len(data) <= max_chars:
        return data
    return data[-max_chars:]


def resolve_venv_python(venv_dir: Path) -> Path:
    if platform.system().lower() == "windows":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def ensure_config_exists(config_path: Path) -> None:
    if config_path.exists():
        return
    config_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(EXAMPLE_CONFIG_PATH, config_path)
    log(f"[OK] 已从示例生成配置文件: {config_path}")


def build_model_profile(profile_name: str, memory_gb: float, physical_cores: int) -> Dict[str, Any]:
    if profile_name == "full":
        return {
            "profile": "full",
            "main_model": "qwen3.6:27b",
            "small_model": "llama3.2:3b",
            "required_ram_gb": 64,
            "required_cores": 16,
            "required_disk_gb": 120,
            "reason": "完整本地能力档，优先保证效果。",
        }
    if profile_name == "balanced":
        return {
            "profile": "balanced",
            "main_model": "qwen2.5-coder:14b",
            "small_model": "llama3.2:3b",
            "required_ram_gb": 48,
            "required_cores": 12,
            "required_disk_gb": 80,
            "reason": "平衡档，兼顾效果与吞吐。",
        }
    if profile_name == "lite":
        return {
            "profile": "lite",
            "main_model": "qwen3.5:9b",
            "small_model": "llama3.2:3b",
            "required_ram_gb": 32,
            "required_cores": 6,
            "required_disk_gb": 50,
            "reason": "轻量档，适合资源较紧的开发机。",
        }
    if memory_gb >= 128 and physical_cores >= 24:
        return build_model_profile("full", memory_gb, physical_cores)
    if memory_gb >= 64 and physical_cores >= 16:
        return build_model_profile("balanced", memory_gb, physical_cores)
    return build_model_profile("lite", memory_gb, physical_cores)


def dependency_catalog(selected_models: Dict[str, Any]) -> List[Dict[str, Any]]:
    translation_dir = REPO_ROOT / "models" / "translation" / DEFAULT_TRANSLATION_MODEL.replace("/", "__")
    return [
        {
            "name": "python",
            "required_version": "3.12+",
            "role": "后端运行时与虚拟环境",
            "channel": "python.org / winget / homebrew / 系统包管理器",
        },
        {
            "name": "node",
            "required_version": "22+",
            "role": "前端开发服务器与构建",
            "channel": "nodejs.org / winget / homebrew / NodeSource",
        },
        {
            "name": "mineru",
            "required_version": "3.1.5",
            "role": "唯一 OCR 引擎，负责 PDF/图片文档识别",
            "channel": "PyPI / 内网镜像 pip 安装",
        },
        {
            "name": "ollama",
            "required_version": "0.31+",
            "role": "本地 LLM 运行时",
            "channel": "winget / brew / 官方安装脚本",
        },
        {
            "name": "embedding_models",
            "required_version": "BAAI/bge-small-zh-v1.5 + en",
            "role": "法规与检索向量化",
            "channel": "ModelScope / ensure_local_models.py",
        },
        {
            "name": "reranker_models",
            "required_version": "BAAI/bge-reranker-base",
            "role": "召回结果重排",
            "channel": "ModelScope / ensure_local_models.py",
        },
        {
            "name": "translation_model",
            "required_version": DEFAULT_TRANSLATION_MODEL,
            "role": "跨语种检索与本地翻译增强",
            "channel": f"Hugging Face / {translation_dir}",
        },
        {
            "name": "ollama_main_model",
            "required_version": selected_models["main_model"],
            "role": "主审计模型",
            "channel": "Ollama 官方模型仓库",
        },
        {
            "name": "ollama_small_model",
            "required_version": selected_models["small_model"],
            "role": "轻量侧车模型",
            "channel": "Ollama 官方模型仓库",
        },
    ]


def detect_cli_version(command: List[str]) -> str:
    try:
        result = run_command(command, capture_output=True, check=True)
        return str(result.stdout).splitlines()[0].strip()
    except Exception:
        return ""


def inspect_ollama_models() -> Dict[str, Any]:
    if not command_exists("ollama"):
        return {"available": False, "models": []}
    try:
        result = run_command(["ollama", "list"], capture_output=True, check=True)
    except Exception:
        return {"available": True, "models": []}
    models: List[str] = []
    for line in str(result.stdout).splitlines()[1:]:
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        if parts:
            models.append(parts[0])
    return {"available": True, "models": models}


def inspect_aux_models(config_path: Path, include_optional: bool) -> Dict[str, Any]:
    env = os.environ.copy()
    env["APP_CONFIG"] = str(config_path)
    cmd = [sys.executable, str(REPO_ROOT / "bin" / "ensure_local_models.py"), "--check-only", "--types", "all"]
    if include_optional:
        cmd.append("--include-optional")
    try:
        result = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as exc:
        return {"available": False, "error": str(exc), "models": []}
    if result.returncode != 0:
        return {
            "available": False,
            "error": (result.stdout or "") + (result.stderr or ""),
            "models": [],
        }
    try:
        parsed = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return {"available": False, "error": result.stdout, "models": []}
    return {"available": True, **parsed}


def collect_missing_items(
    selected_models: Dict[str, Any],
    config_path: Path,
    include_optional: bool,
) -> List[Dict[str, Any]]:
    catalog = dependency_catalog(selected_models)
    current_map: Dict[str, Dict[str, Any]] = {}

    python_version = get_python_version_text()
    current_map["python"] = {
        "current_version": python_version,
        "available": sys.version_info >= (3, 12),
    }

    node_version = get_node_version_text()
    current_map["node"] = {
        "current_version": node_version,
        "available": parse_major(node_version) >= 22,
    }

    current_map["mineru"] = {
        "current_version": detect_cli_version(["mineru", "--version"]),
        "available": bool(shutil.which("mineru")) or (importlib.util.find_spec("mineru") is not None),
    }
    current_map["ollama"] = {
        "current_version": detect_cli_version(["ollama", "--version"]),
        "available": command_exists("ollama"),
    }

    ollama_info = inspect_ollama_models()
    installed_tags = set(ollama_info.get("models") or [])
    current_map["ollama_main_model"] = {
        "current_version": selected_models["main_model"],
        "available": selected_models["main_model"] in installed_tags,
    }
    current_map["ollama_small_model"] = {
        "current_version": selected_models["small_model"],
        "available": selected_models["small_model"] in installed_tags,
    }

    aux = inspect_aux_models(config_path, include_optional=include_optional)
    if aux.get("available"):
        rows = aux.get("models") or []
        by_type: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            by_type.setdefault(str(row.get("type") or ""), []).append(row)
        current_map["embedding_models"] = {
            "current_version": ",".join(str(r.get("name") or "") for r in by_type.get("embedding", [])),
            "available": all(bool(r.get("ok", False)) for r in by_type.get("embedding", [])) if by_type.get("embedding") else False,
        }
        current_map["reranker_models"] = {
            "current_version": ",".join(str(r.get("name") or "") for r in by_type.get("reranker", [])),
            "available": all(bool(r.get("ok", False)) for r in by_type.get("reranker", [])) if by_type.get("reranker") else False,
        }
        translation_rows = by_type.get("translation", [])
        current_map["translation_model"] = {
            "current_version": ",".join(str(r.get("model_id") or "") for r in translation_rows),
            "available": (not include_optional) or (
                all(bool(r.get("ok", False)) for r in translation_rows) if translation_rows else False
            ),
        }
    else:
        current_map["embedding_models"] = {"current_version": "", "available": False}
        current_map["reranker_models"] = {"current_version": "", "available": False}
        current_map["translation_model"] = {"current_version": "", "available": not include_optional}

    missing = []
    for item in catalog:
        merged = dict(item)
        merged.update(current_map.get(item["name"], {}))
        if not merged.get("available", False):
            missing.append(merged)
    return missing


def detect_package_manager(os_info: Dict[str, str]) -> str:
    system = os_info["system"]
    if system == "windows":
        if command_exists("winget"):
            return "winget"
        if command_exists("choco"):
            return "choco"
        return ""
    if system == "darwin":
        return "brew" if command_exists("brew") else ""
    distro_id = os_info.get("distro_id", "")
    distro_like = os_info.get("distro_like", "")
    candidates = " ".join([distro_id, distro_like]).lower()
    if "debian" in candidates or "ubuntu" in candidates:
        return "apt"
    if "rhel" in candidates or "fedora" in candidates or "centos" in candidates or "rocky" in candidates or "alma" in candidates:
        if command_exists("dnf"):
            return "dnf"
        return "yum"
    if "suse" in candidates and command_exists("zypper"):
        return "zypper"
    if command_exists("pacman"):
        return "pacman"
    return ""


def is_admin() -> bool:
    system = platform.system().lower()
    if system == "windows":
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    return os.geteuid() == 0 if hasattr(os, "geteuid") else False


def ensure_mineru_cli_available(venv_python: Optional[Path] = None) -> None:
    if command_exists("mineru"):
        log("[OK] MinerU CLI 已就绪。")
        return
    if venv_python is not None:
        cli_name = "mineru.exe" if platform.system().lower() == "windows" else "mineru"
        candidate = venv_python.parent / cli_name
        if candidate.exists():
            log(f"[OK] MinerU CLI 已安装在虚拟环境: {candidate}")
            return
    raise DeployError("未检测到 mineru CLI。请先安装 Python 依赖，或确认虚拟环境中的 Scripts/bin 已加入 PATH。")


def ensure_ocr_dependencies(os_info: Dict[str, str], venv_python: Optional[Path] = None) -> None:
    if command_exists("mineru"):
        log("[OK] MinerU OCR 依赖已就绪。")
        return
    system = os_info["system"]
    if venv_python is not None and venv_python.exists():
        run_command([str(venv_python), "-m", "pip", "install", "-U", "mineru==3.1.5"])
        ensure_mineru_cli_available(venv_python)
        return
    if system == "windows":
        run_command(["cmd", "/c", str(REPO_ROOT / "bin" / "install_ocr_windows.bat")], cwd=REPO_ROOT)
        ensure_mineru_cli_available()
        return
    if system == "darwin":
        run_command(["bash", str(REPO_ROOT / "bin" / "install_ocr_macos.sh")], cwd=REPO_ROOT)
        ensure_mineru_cli_available()
        return
    raise DeployError("无法自动安装 MinerU。请先完成 Python 依赖安装后重试。")


def install_node_if_needed(os_info: Dict[str, str]) -> None:
    version_text = get_node_version_text()
    if parse_major(version_text) >= 22:
        log(f"[OK] Node.js 已满足要求: {version_text}")
        return
    system = os_info["system"]
    manager = detect_package_manager(os_info)
    if system == "windows":
        if manager == "winget":
            run_command(["winget", "install", "-e", "--id", "OpenJS.NodeJS.LTS"])
        elif manager == "choco":
            run_command(["choco", "install", "nodejs-lts", "-y"])
        else:
            raise DeployError("未找到 winget/choco，无法自动安装 Node.js 22+。")
    elif system == "darwin":
        run_command(["brew", "install", "node@22"])
    else:
        distro_id = os_info.get("distro_id", "")
        distro_like = os_info.get("distro_like", "")
        family = " ".join([distro_id, distro_like]).lower()
        if "debian" in family or "ubuntu" in family or "rhel" in family or "fedora" in family or "centos" in family or "rocky" in family or "alma" in family:
            run_command(
                [
                    "bash",
                    "-lc",
                    "curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - || curl -fsSL https://rpm.nodesource.com/setup_22.x | sudo bash -",
                ]
            )
            if detect_package_manager(os_info) == "apt":
                run_command(["sudo", "apt-get", "install", "-y", "nodejs"])
            elif detect_package_manager(os_info) == "dnf":
                run_command(["sudo", "dnf", "install", "-y", "nodejs"])
            else:
                run_command(["sudo", "yum", "install", "-y", "nodejs"])
        else:
            raise DeployError("当前 Linux 发行版未内置 Node.js 22 自动安装逻辑，请手动安装。")
    refreshed = get_node_version_text()
    if parse_major(refreshed) < 22:
        raise DeployError(f"Node.js 版本仍未达到 22+: 当前 {refreshed or 'unknown'}")


def install_ollama_if_needed(os_info: Dict[str, str]) -> None:
    if command_exists("ollama"):
        log("[OK] Ollama 已安装。")
        return
    system = os_info["system"]
    manager = detect_package_manager(os_info)
    if system == "windows":
        if manager == "winget":
            run_command(["winget", "install", "-e", "--id", "Ollama.Ollama"])
        elif manager == "choco":
            run_command(["choco", "install", "ollama", "-y"])
        else:
            raise DeployError("未找到 winget/choco，无法自动安装 Ollama。")
    elif system == "darwin":
        run_command(["brew", "install", "ollama"])
    else:
        run_command(["bash", "-lc", "curl -fsSL https://ollama.com/install.sh | sh"])
    if not command_exists("ollama"):
        raise DeployError("Ollama 安装后仍不可用，请重新打开终端或手动确认 PATH。")


def create_or_update_venv(venv_dir: Path) -> Path:
    if not venv_dir.exists():
        log(f"[START] 创建虚拟环境: {venv_dir}")
        builder = venv.EnvBuilder(with_pip=True, clear=False, upgrade_deps=False)
        builder.create(str(venv_dir))
    else:
        log(f"[SKIP] 虚拟环境已存在: {venv_dir}")
    venv_python = resolve_venv_python(venv_dir)
    if not venv_python.exists():
        raise DeployError(f"未找到虚拟环境 Python: {venv_python}")
    return venv_python


def install_python_dependencies(venv_python: Path) -> None:
    run_command([str(venv_python), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"])
    run_command([str(venv_python), "-m", "pip", "install", "-r", str(APP_DIR / "requirements.txt")])
    ensure_ocr_dependencies(detect_os(), venv_python=venv_python)


def install_frontend_dependencies() -> None:
    npm_cmd = "npm.cmd" if platform.system().lower() == "windows" else "npm"
    if not command_exists(npm_cmd) and not command_exists("npm"):
        raise DeployError("未找到 npm，可先安装 Node.js 22+。")
    actual_npm = shutil.which(npm_cmd) or shutil.which("npm") or "npm"
    run_command([actual_npm, "install"], cwd=WEB_DIR)


def translation_model_dir(model_id: str) -> str:
    safe = model_id.replace("/", "__").replace("\\", "__").replace(":", "_")
    return str((REPO_ROOT / "models" / "translation" / safe).resolve())


def write_local_config(
    config_path: Path,
    selected_models: Dict[str, Any],
    backend_port: int,
    frontend_port: int,
    *,
    enable_translation: bool,
) -> Dict[str, Any]:
    config = load_json(config_path)
    ollama_api_base = f"{DEFAULT_OLLAMA_HOST}/v1"
    config["llm_config"] = {
        "provider": "ollama",
        "api_base": ollama_api_base,
        "api_key": "",
        "model": selected_models["main_model"],
        "temperature": 0.2,
        "max_tokens": 2048,
        "timeout": 60,
        "headers": {},
    }
    config["local_llm"] = {
        "enabled": True,
        "routing_enabled": True,
        "cloud_fallback_enabled": False,
        "timeout_sec": 30,
        "json_repair_enabled": True,
        "main_model": {
            "provider": "ollama",
            "api_base": ollama_api_base,
            "api_key": "",
            "model": selected_models["main_model"],
            "temperature": 0.2,
            "max_tokens": 2048,
            "timeout": 30,
            "headers": {},
        },
        "small_model": {
            "provider": "ollama",
            "api_base": ollama_api_base,
            "api_key": "",
            "model": selected_models["small_model"],
            "temperature": 0.1,
            "max_tokens": 1024,
            "timeout": 20,
            "headers": {},
        },
        "routing": {
            "task_profiles": {
                "default": "main",
                "contract_audit_main": "main",
                "contract_audit_memory": "main",
                "contract_clause_audit": "main",
                "tax_risk_main": "main",
                "memory_flush": "small",
                "tax_match_small": "small",
                "entity_extract_small": "small",
            },
            "tax_match_use_small_model": True,
            "entity_extract_use_small_model": True,
            "high_risk_force_cloud": False,
        },
        "execution": {
            "fallback_on_error": False,
            "fallback_on_invalid_json": False,
            "tax_match_cloud_review_labels": ["non_compliant"],
            "tax_match_min_confidence": 0.65,
            "tax_match_max_workers": 2,
            "tax_risk_max_workers": 2,
            "entity_extract_max_workers": 4,
            "memory_clause_force_cloud_for_priority": False,
            "memory_flush_force_cloud": False,
        },
    }
    config["memory_runtime_config"] = {
        "memory_module_enabled": False,
        "memory_mode_when_disabled": "classic",
        "memory_disable_fallback_on_error": True,
        "memory_token_guard_enabled": True,
        "memory_max_llm_calls_per_audit": 12,
        "memory_max_prompt_chars_per_clause": 2400,
    }
    config["memory_enable_long_memory"] = False
    config["memory_enable_short_memory"] = False
    config["memory_use_long_hits"] = False
    config["reranker_enabled"] = False
    config["reranker_model_path"] = ""
    config["reranker_profiles"] = {}
    config["ocr_engine"] = "mineru"
    config["ocr_engine_order"] = ["mineru"]
    config["ocr_engine_by_type"] = {"pdf": "mineru", "image": "mineru"}
    config["ocr_engines"] = {
        "mineru": {
            "module": "app.core.mineru_ocr",
            "function": "ocr_document",
        }
    }
    config["mineru"] = {
        "mode": "auto",
        "fallback_backends": ["pipeline"],
        "probe_cache_ttl_sec": 300,
        "method": "auto",
        "lang": "ch",
        "device": "cpu",
        "formula": False,
        "table": False,
        "model_source": "huggingface",
        "timeout": 900,
    }
    config["model_preflight_check_on_startup"] = True
    config["model_preflight_auto_download_on_startup"] = False
    config["model_preflight_include_optional"] = bool(enable_translation)
    config["model_preflight_require_all"] = True
    translation_cfg = config.get("translation_config") if isinstance(config.get("translation_config"), dict) else {}
    translation_cfg["enabled"] = bool(enable_translation)
    translation_cfg["backend"] = "hy_mt_local"
    translation_cfg["mode"] = translation_cfg.get("mode") or "dual"
    translation_cfg["cross_lang_source_query_enabled"] = bool(translation_cfg.get("cross_lang_source_query_enabled", False))
    translation_cfg["model_id"] = str(translation_cfg.get("model_id") or DEFAULT_TRANSLATION_MODEL)
    translation_cfg["model_dir"] = str(translation_cfg.get("model_dir") or translation_model_dir(translation_cfg["model_id"]))
    translation_cfg["device"] = str(translation_cfg.get("device") or "cpu")
    translation_cfg["source_langs"] = translation_cfg.get("source_langs") or ["en"]
    translation_cfg["target_lang"] = translation_cfg.get("target_lang") or "zh"
    config["translation_config"] = translation_cfg

    allow_origins = config.get("cors_allow_origins") if isinstance(config.get("cors_allow_origins"), list) else []
    defaults = [
        f"http://localhost:{frontend_port}",
        f"http://127.0.0.1:{frontend_port}",
    ]
    for origin in defaults:
        if origin not in allow_origins:
            allow_origins.append(origin)
    config["cors_allow_origins"] = allow_origins
    config["deployment_profile"] = selected_models["profile"]
    config["deployment_last_backend_port"] = int(backend_port)
    config["deployment_last_frontend_port"] = int(frontend_port)
    save_json(config_path, config)
    return config


def initialize_app(venv_python: Path, config_path: Path) -> None:
    env = os.environ.copy()
    env["APP_CONFIG"] = str(config_path)
    run_command([str(venv_python), str(REPO_ROOT / "bin" / "init.py"), "--config-path", str(config_path)], cwd=REPO_ROOT, env=env)


def ensure_aux_models(venv_python: Path, config_path: Path, include_optional: bool) -> None:
    env = os.environ.copy()
    env["APP_CONFIG"] = str(config_path)
    cmd = [str(venv_python), str(REPO_ROOT / "bin" / "ensure_local_models.py"), "--types", "all"]
    if include_optional:
        cmd.append("--include-optional")
    run_command(cmd, cwd=REPO_ROOT, env=env)


def ensure_ollama_models(venv_python: Path, selected_models: Dict[str, Any]) -> None:
    run_command(
        [
            str(venv_python),
            str(REPO_ROOT / "bin" / "start-local-llm-servers.py"),
            "--main-model",
            selected_models["main_model"],
            "--small-model",
            selected_models["small_model"],
        ],
        cwd=REPO_ROOT,
    )
    run_command(
        [
            str(venv_python),
            str(REPO_ROOT / "bin" / "download-local-llm-models.py"),
            "--main-model",
            selected_models["main_model"],
            "--small-model",
            selected_models["small_model"],
        ],
        cwd=REPO_ROOT,
    )


def http_get(url: str, timeout: int = 5) -> Dict[str, Any]:
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return {
            "status": response.status,
            "body": response.read().decode("utf-8", errors="ignore"),
        }


def wait_for_http(url: str, timeout_sec: int) -> Dict[str, Any]:
    deadline = time.time() + max(timeout_sec, 1)
    last_error = ""
    while time.time() <= deadline:
        try:
            return {"ok": True, **http_get(url, timeout=5)}
        except Exception as exc:
            last_error = str(exc)
            time.sleep(1)
    return {"ok": False, "error": last_error}


def start_service_process(
    cmd: List[str],
    *,
    cwd: Path,
    env: Dict[str, str],
    log_path: Path,
) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("a", encoding="utf-8")
    creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    return subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )


def write_pid_file(payload: Dict[str, Any]) -> None:
    save_json(RUNTIME_DIR / "services.pids.json", payload)


def stop_pid(pid: int) -> None:
    if pid <= 0:
        return
    system = platform.system().lower()
    if system == "windows":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True, check=False)
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except Exception:
        pass


def start_and_verify_services(
    venv_python: Path,
    config_path: Path,
    backend_port: int,
    frontend_port: int,
    *,
    keep_running: bool,
) -> Dict[str, Any]:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    backend_log = LOG_DIR / "backend.log"
    frontend_log = LOG_DIR / "frontend.log"
    npm_cmd = shutil.which("npm.cmd") or shutil.which("npm") or "npm"

    backend_env = os.environ.copy()
    backend_env["APP_CONFIG"] = str(config_path)
    backend_env["APP_PORT"] = str(backend_port)
    frontend_env = os.environ.copy()

    backend_proc = start_service_process(
        [str(venv_python), "-m", "app.main"],
        cwd=REPO_ROOT,
        env=backend_env,
        log_path=backend_log,
    )
    frontend_proc = start_service_process(
        [npm_cmd, "run", "dev", "--", "--host", "0.0.0.0", "--port", str(frontend_port)],
        cwd=WEB_DIR,
        env=frontend_env,
        log_path=frontend_log,
    )

    write_pid_file(
        {
            "backend_pid": backend_proc.pid,
            "frontend_pid": frontend_proc.pid,
            "backend_port": backend_port,
            "frontend_port": frontend_port,
            "backend_log": str(backend_log),
            "frontend_log": str(frontend_log),
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
    )

    backend_result = wait_for_http(f"http://127.0.0.1:{backend_port}/health", timeout_sec=60)
    frontend_result = wait_for_http(f"http://127.0.0.1:{frontend_port}", timeout_sec=60)

    if not backend_result.get("ok"):
        stop_pid(frontend_proc.pid)
        stop_pid(backend_proc.pid)
        raise DeployError(
            "后端启动失败。"
            f"\nbackend.log 末尾:\n{read_text_tail(backend_log)}"
        )
    if not frontend_result.get("ok"):
        stop_pid(frontend_proc.pid)
        stop_pid(backend_proc.pid)
        raise DeployError(
            "前端启动失败。"
            f"\nfrontend.log 末尾:\n{read_text_tail(frontend_log)}"
        )

    summary = {
        "backend": {
            "pid": backend_proc.pid,
            "url": f"http://127.0.0.1:{backend_port}",
            "health_url": f"http://127.0.0.1:{backend_port}/health",
            "log": str(backend_log),
        },
        "frontend": {
            "pid": frontend_proc.pid,
            "url": f"http://127.0.0.1:{frontend_port}",
            "log": str(frontend_log),
        },
        "keep_running": keep_running,
    }
    if not keep_running:
        stop_pid(frontend_proc.pid)
        stop_pid(backend_proc.pid)
    return summary


def build_report(
    *,
    os_info: Dict[str, str],
    memory_gb: float,
    disk_free_gb: float,
    physical_cores: int,
    gpu_info: List[Dict[str, str]],
    selected_models: Dict[str, Any],
    missing_before: List[Dict[str, Any]],
    missing_after: List[Dict[str, Any]],
    service_summary: Dict[str, Any],
    config_path: Path,
) -> Dict[str, Any]:
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "os": os_info,
        "python_version": get_python_version_text(),
        "node_version": get_node_version_text(),
        "physical_cores": physical_cores,
        "logical_cores": os.cpu_count() or 0,
        "memory_gb": memory_gb,
        "disk_free_gb": disk_free_gb,
        "gpu": gpu_info,
        "selected_models": selected_models,
        "config_path": str(config_path),
        "missing_items_before": missing_before,
        "missing_items_after": missing_after,
        "service_summary": service_summary,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AI Law Assistant 本地完整部署脚本（预检、安装、模型、配置、启动、验证）。"
    )
    parser.add_argument("--config-path", default=str(DEFAULT_CONFIG_PATH), help="app/config.json 路径。")
    parser.add_argument("--venv-dir", default=str(APP_DIR / ".venv"), help="虚拟环境目录。")
    parser.add_argument(
        "--profile",
        choices=["auto", "full", "balanced", "lite"],
        default="auto",
        help="模型规格策略：auto/full/balanced/lite。",
    )
    parser.add_argument("--main-model", default="", help="手动覆盖主模型标签。")
    parser.add_argument("--small-model", default="", help="手动覆盖轻量模型标签。")
    parser.add_argument("--backend-port", type=int, default=8000, help="后端端口。")
    parser.add_argument("--frontend-port", type=int, default=5173, help="前端端口。")
    parser.add_argument("--check-only", action="store_true", help="仅输出缺失项清单，不执行安装。")
    parser.add_argument("--skip-system-deps", action="store_true", help="跳过系统依赖安装。")
    parser.add_argument("--skip-python-deps", action="store_true", help="跳过 Python 依赖安装。")
    parser.add_argument("--skip-frontend-deps", action="store_true", help="跳过前端依赖安装。")
    parser.add_argument("--skip-models", action="store_true", help="跳过模型下载与准备。")
    parser.add_argument("--skip-start", action="store_true", help="跳过服务启动与连通性验证。")
    parser.add_argument("--disable-translation", action="store_true", help="关闭本地翻译模型准备与配置。")
    parser.add_argument("--keep-running", action="store_true", help="验证完成后保持服务运行。")
    parser.add_argument(
        "--report-json",
        default=str(REPORT_DIR / "local_deploy_report.json"),
        help="部署结果报告输出路径。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    os_info = detect_os()
    config_path = Path(args.config_path).resolve()
    venv_dir = Path(args.venv_dir).resolve()
    report_path = Path(args.report_json).resolve()

    memory_gb = bytes_to_gb(get_total_memory_bytes())
    disk_free_gb = bytes_to_gb(shutil.disk_usage(REPO_ROOT).free)
    physical_cores = get_physical_cores()
    gpu_info = get_gpu_info()

    selected_models = build_model_profile(args.profile, memory_gb, physical_cores)
    if args.main_model:
        selected_models["main_model"] = args.main_model
    if args.small_model:
        selected_models["small_model"] = args.small_model

    ensure_config_exists(config_path)
    include_optional = not args.disable_translation
    missing_before = collect_missing_items(selected_models, config_path, include_optional=include_optional)

    if args.check_only:
        report = build_report(
            os_info=os_info,
            memory_gb=memory_gb,
            disk_free_gb=disk_free_gb,
            physical_cores=physical_cores,
            gpu_info=gpu_info,
            selected_models=selected_models,
            missing_before=missing_before,
            missing_after=missing_before,
            service_summary={"skipped": True, "reason": "check_only"},
            config_path=config_path,
        )
        save_json(report_path, report)
        log(f"[OK] 仅检查模式完成，报告已输出: {report_path}")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if sys.version_info < (3, 12):
        raise DeployError(f"当前 Python 版本为 {get_python_version_text()}，需 3.12+。")
    if memory_gb < float(selected_models["required_ram_gb"]):
        raise DeployError(
            f"当前内存 {memory_gb} GB，不满足所选档位 {selected_models['profile']} 的最低要求 "
            f"{selected_models['required_ram_gb']} GB。可改用 --profile lite 或手动覆盖模型。"
        )
    if physical_cores < int(selected_models["required_cores"]):
        raise DeployError(
            f"当前物理核心数 {physical_cores}，低于档位 {selected_models['profile']} 推荐值 "
            f"{selected_models['required_cores']}。可改用 --profile lite。"
        )
    if disk_free_gb < float(selected_models["required_disk_gb"]):
        raise DeployError(
            f"当前磁盘可用空间 {disk_free_gb} GB，不满足档位 {selected_models['profile']} 的最低要求 "
            f"{selected_models['required_disk_gb']} GB。请先清理磁盘或改用更轻量模型。"
        )

    if not args.skip_system_deps:
        if not is_admin():
            log("[WARN] 当前不是管理员/root 权限；如遇系统依赖安装失败，请使用管理员权限重试。")
        install_node_if_needed(os_info)
        install_ollama_if_needed(os_info)

    venv_python = create_or_update_venv(venv_dir)

    if not args.skip_python_deps:
        install_python_dependencies(venv_python)
    if not args.skip_frontend_deps:
        install_frontend_dependencies()

    write_local_config(
        config_path,
        selected_models,
        args.backend_port,
        args.frontend_port,
        enable_translation=include_optional,
    )
    initialize_app(venv_python, config_path)

    if not args.skip_models:
        ensure_aux_models(venv_python, config_path, include_optional=include_optional)
        ensure_ollama_models(venv_python, selected_models)

    service_summary: Dict[str, Any] = {"skipped": True, "reason": "skip_start"}
    if not args.skip_start:
        service_summary = start_and_verify_services(
            venv_python,
            config_path,
            args.backend_port,
            args.frontend_port,
            keep_running=args.keep_running,
        )

    missing_after = collect_missing_items(selected_models, config_path, include_optional=include_optional)
    report = build_report(
        os_info=os_info,
        memory_gb=memory_gb,
        disk_free_gb=disk_free_gb,
        physical_cores=physical_cores,
        gpu_info=gpu_info,
        selected_models=selected_models,
        missing_before=missing_before,
        missing_after=missing_after,
        service_summary=service_summary,
        config_path=config_path,
    )
    save_json(report_path, report)

    log("[OK] 本地部署流程执行完成。")
    log(f"[OK] 报告路径: {report_path}")
    if not args.skip_start:
        log(f"[OK] 后端地址: http://127.0.0.1:{args.backend_port}")
        log(f"[OK] 前端地址: http://127.0.0.1:{args.frontend_port}")
        if args.keep_running:
            log(r"[OK] 服务保持运行中，可使用 python .\bin\stop-services.py 停止。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except DeployError as exc:
        log(f"[ERROR] {exc}")
        sys.exit(1)
