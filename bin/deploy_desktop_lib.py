import hashlib
import json
import logging
import os
import platform
import shutil
import socket
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / ".runtime" / "deploy"
LOG_DIR = RUNTIME_DIR / "logs"


class DeployError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def setup_logger(name: str, log_file: Path) -> logging.Logger:
    ensure_dir(log_file.parent)
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    logger.propagate = False
    return logger


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as file_obj:
        return json.load(file_obj)


def save_json(path: Path, data: Dict[str, Any]) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as file_obj:
        json.dump(data, file_obj, ensure_ascii=False, indent=2)
        file_obj.write("\n")


def tail_text_file(path: Path, line_limit: int = 80) -> str:
    if not path.exists():
        return ""
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as file_obj:
            lines = file_obj.readlines()[-line_limit:]
    except OSError:
        return ""
    return "".join(lines)


def ensure_ascii_workspace(path: Path) -> None:
    raw = str(path)
    for ch in raw:
        if ord(ch) > 127:
            raise DeployError(
                f"workspace path must avoid non-ASCII characters for desktop deployment: {raw}"
            )


def parse_version(value: str) -> List[int]:
    parts: List[int] = []
    token = ""
    for ch in str(value or ""):
        if ch.isdigit():
            token += ch
            continue
        if token:
            parts.append(int(token))
            token = ""
    if token:
        parts.append(int(token))
    return parts


def version_gte(current: str, minimum: str) -> bool:
    cur = parse_version(current)
    req = parse_version(minimum)
    width = max(len(cur), len(req))
    cur += [0] * (width - len(cur))
    req += [0] * (width - len(req))
    return cur >= req


def command_exists(command: str) -> bool:
    return shutil.which(command) is not None


def run_command(
    cmd: List[str],
    *,
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
    timeout: Optional[int] = None,
    check: bool = True,
    logger: Optional[logging.Logger] = None,
) -> subprocess.CompletedProcess:
    if logger:
        logger.info("run_command cwd=%s cmd=%s",
                    str(cwd or ROOT), " ".join(cmd))
    result = subprocess.run(
        cmd,
        cwd=str(cwd or ROOT),
        env=env,
        timeout=timeout,
        check=False,
        capture_output=True,
        text=True,
    )
    if logger:
        if result.stdout.strip():
            logger.info("stdout:\n%s", result.stdout.strip())
        if result.stderr.strip():
            logger.info("stderr:\n%s", result.stderr.strip())
    if check and result.returncode != 0:
        raise DeployError(
            f"command failed with exit={result.returncode}: {' '.join(cmd)}"
        )
    return result


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        while True:
            block = file_obj.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest().lower()


def verify_sha256(path: Path, expected: str) -> bool:
    if not expected:
        return path.exists()
    if not path.exists():
        return False
    return sha256_file(path) == str(expected).strip().lower()


def is_allowed_download_url(url: str, allowed_hosts: Iterable[str]) -> bool:
    parsed = urllib.parse.urlsplit(str(url or "").strip())
    host = (parsed.hostname or "").lower()
    allowed = {str(item).strip().lower()
               for item in allowed_hosts if str(item).strip()}
    return bool(host and host in allowed)


def human_size(size_bytes: int) -> str:
    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size_bytes} B"


def file_mtime(path: Path) -> float:
    return path.stat().st_mtime if path.exists() else 0.0


def load_stamp(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return load_json(path)
    except Exception:
        return {}


def save_stamp(path: Path, payload: Dict[str, Any]) -> None:
    save_json(path, payload)


def build_backend_install_stamp(requirements_path: Path, python_exe: Path) -> Dict[str, Any]:
    return {
        "requirements_path": str(requirements_path),
        "requirements_mtime": file_mtime(requirements_path),
        "python_exe": str(python_exe),
        "saved_at": utc_now(),
    }


def build_frontend_install_stamp(lock_path: Path, npm_exe: str) -> Dict[str, Any]:
    return {
        "lock_path": str(lock_path),
        "lock_mtime": file_mtime(lock_path),
        "npm_exe": npm_exe,
        "saved_at": utc_now(),
    }


def stamp_matches(current: Dict[str, Any], expected: Dict[str, Any]) -> bool:
    if not current:
        return False
    for key, value in expected.items():
        if key == "saved_at":
            continue
        if current.get(key) != value:
            return False
    return True


def http_json(url: str, timeout: int = 10) -> Dict[str, Any]:
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def http_ok(url: str, timeout: int = 10) -> bool:
    try:
        request = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 200 <= int(response.status) < 300
    except Exception:
        return False


def wait_http_ok(url: str, timeout_seconds: int, probe_timeout: int = 10) -> bool:
    deadline = time.time() + max(1, timeout_seconds)
    while time.time() <= deadline:
        if http_ok(url, timeout=probe_timeout):
            return True
        time.sleep(2)
    return False


def is_port_open(host: str, port: int, timeout: float = 1.5) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        sock.close()


def powershell_json(script: str) -> Any:
    if platform.system().lower() != "windows":
        return None
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def detect_gpu_windows() -> List[Dict[str, Any]]:
    payload = powershell_json(
        "Get-CimInstance Win32_VideoController | "
        "Select-Object Name,DriverVersion,AdapterCompatibility | ConvertTo-Json -Depth 3"
    )
    if payload is None:
        return []
    rows = payload if isinstance(payload, list) else [payload]
    output: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        output.append(
            {
                "name": str(row.get("Name") or "").strip(),
                "driver_version": str(row.get("DriverVersion") or "").strip(),
                "vendor": str(row.get("AdapterCompatibility") or "").strip(),
            }
        )
    return output


def detect_gpu_linux() -> List[Dict[str, Any]]:
    gpus: List[Dict[str, Any]] = []
    if command_exists("nvidia-smi"):
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        for line in result.stdout.splitlines():
            parts = [item.strip() for item in line.split(",")]
            if len(parts) >= 2:
                gpus.append(
                    {
                        "name": parts[0],
                        "driver_version": parts[1],
                        "vendor": "NVIDIA",
                    }
                )
    if not gpus and command_exists("lspci"):
        result = subprocess.run(
            ["lspci"],
            capture_output=True,
            text=True,
            check=False,
        )
        for line in result.stdout.splitlines():
            lowered = line.lower()
            if "vga" in lowered or "3d controller" in lowered:
                gpus.append(
                    {
                        "name": line.strip(),
                        "driver_version": "",
                        "vendor": "",
                    }
                )
    return gpus


def detect_environment() -> Dict[str, Any]:
    system_name = platform.system().lower()
    machine = platform.machine().lower()
    gpu_info = detect_gpu_windows() if system_name == "windows" else detect_gpu_linux()

    has_nvidia = any("nvidia" in str(item.get("name", "")).lower(
    ) or "nvidia" in str(item.get("vendor", "")).lower() for item in gpu_info)
    has_amd = any("amd" in str(item.get("name", "")).lower() or "advanced micro devices" in str(
        item.get("vendor", "")).lower() for item in gpu_info)
    has_apple = system_name == "darwin" and machine in {"arm64", "aarch64"}

    return {
        "detected_at": utc_now(),
        "os": {
            "system": system_name,
            "release": platform.release(),
            "version": platform.version(),
        },
        "cpu": {
            "architecture": machine,
            "processor": platform.processor(),
            "cores": os.cpu_count() or 0,
        },
        "gpu": {
            "items": gpu_info,
            "has_nvidia": has_nvidia,
            "has_amd": has_amd,
            "has_apple_silicon": has_apple,
        },
        "tooling": {
            "python": sys.version.split()[0],
            "git": detect_tool_version("git", ["git", "--version"]),
            "cmake": detect_tool_version("cmake", ["cmake", "--version"]),
            "node": detect_tool_version("node", ["node", "--version"]),
            "npm": detect_tool_version("npm", ["npm", "--version"]),
            "nvidia_smi": detect_tool_version("nvidia-smi", ["nvidia-smi", "--version"]),
            "nvcc": detect_tool_version("nvcc", ["nvcc", "--version"]),
            "rocm_smi": detect_tool_version("rocm-smi", ["rocm-smi", "--showdriverversion"]),
        },
    }


def detect_tool_version(tool_name: str, cmd: List[str]) -> str:
    executable = shutil.which(cmd[0])
    if not executable and os.name == "nt" and "." not in cmd[0]:
        for suffix in (".cmd", ".exe", ".bat"):
            executable = shutil.which(f"{cmd[0]}{suffix}")
            if executable:
                break
    if not executable:
        return ""
    resolved_cmd = [executable, *cmd[1:]]
    try:
        result = subprocess.run(
            resolved_cmd,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return ""
    output = (result.stdout or result.stderr or "").strip().splitlines()
    return output[0].strip() if output else ""


def detect_llamacpp_build_profile(env_info: Dict[str, Any]) -> Dict[str, Any]:
    system_name = str((env_info.get("os") or {}).get("system") or "").lower()
    arch = str((env_info.get("cpu") or {}).get("architecture") or "").lower()
    gpu = env_info.get("gpu") or {}

    if bool(gpu.get("has_nvidia")):
        return {
            "backend": "cuda",
            "cmake_flags": ["-DGGML_CUDA=ON", "-DGGML_NATIVE=ON"],
            "notes": "Detected NVIDIA GPU; prefer CUDA build.",
        }
    if bool(gpu.get("has_amd")) and system_name == "linux":
        return {
            "backend": "rocm",
            "cmake_flags": ["-DGGML_HIPBLAS=ON", "-DGGML_NATIVE=ON"],
            "notes": "Detected AMD GPU on Linux; prefer ROCm/HIP build.",
        }
    if system_name == "darwin" and arch in {"arm64", "aarch64"}:
        return {
            "backend": "metal",
            "cmake_flags": ["-DGGML_METAL=ON", "-DGGML_NATIVE=ON"],
            "notes": "Detected Apple Silicon; prefer Metal build.",
        }
    if arch in {"x86_64", "amd64"}:
        return {
            "backend": "cpu_avx2",
            "cmake_flags": ["-DGGML_NATIVE=ON", "-DGGML_AVX2=ON"],
            "notes": "CPU-only desktop build with AVX2 optimization.",
        }
    return {
        "backend": "cpu_generic",
        "cmake_flags": ["-DGGML_NATIVE=ON"],
        "notes": "Fallback CPU-only desktop build profile.",
    }


class DeploymentTransaction:
    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.rollback_actions: List[tuple[str, Any]] = []

    def add_rollback(self, description: str, action) -> None:
        self.rollback_actions.append((description, action))

    def rollback(self) -> None:
        for description, action in reversed(self.rollback_actions):
            try:
                self.logger.info("rollback: %s", description)
                action()
            except Exception as exc:
                self.logger.warning(
                    "rollback failed for %s: %s", description, exc)


def request_with_resume(
    url: str,
    target_path: Path,
    *,
    retries: int,
    logger: logging.Logger,
    timeout: int = 60,
    chunk_size: int = 1024 * 1024,
) -> None:
    ensure_dir(target_path.parent)
    partial_path = target_path.with_suffix(target_path.suffix + ".part")

    for attempt in range(1, retries + 1):
        existing_size = partial_path.stat().st_size if partial_path.exists() else 0
        headers = {}
        if existing_size > 0:
            headers["Range"] = f"bytes={existing_size}-"

        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                total_size = response.headers.get("Content-Length")
                expected_total = int(
                    total_size) + existing_size if total_size and existing_size else int(total_size or 0)
                mode = "ab" if existing_size > 0 else "wb"
                downloaded = existing_size
                last_progress = -1
                with partial_path.open(mode) as file_obj:
                    while True:
                        block = response.read(chunk_size)
                        if not block:
                            break
                        file_obj.write(block)
                        downloaded += len(block)
                        if expected_total > 0:
                            progress = int(downloaded * 100 / expected_total)
                            if progress >= last_progress + 5:
                                last_progress = progress
                                logger.info(
                                    "download progress url=%s file=%s progress=%s%% (%s/%s)",
                                    url,
                                    target_path.name,
                                    progress,
                                    human_size(downloaded),
                                    human_size(expected_total),
                                )
                partial_path.replace(target_path)
                return
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and partial_path.exists():
                partial_path.replace(target_path)
                return
            logger.warning(
                "download failed attempt=%s/%s url=%s err=%s",
                attempt,
                retries,
                url,
                exc,
            )
        except Exception as exc:
            logger.warning(
                "download failed attempt=%s/%s url=%s err=%s",
                attempt,
                retries,
                url,
                exc,
            )
        time.sleep(min(5, attempt))
    raise DeployError(f"failed to download after retries: {url}")


def merge_parts(parts: List[Path], target_path: Path) -> None:
    ensure_dir(target_path.parent)
    with target_path.open("wb") as output:
        for part in parts:
            with part.open("rb") as input_file:
                shutil.copyfileobj(input_file, output)


def extract_archive(archive_path: Path, destination: Path) -> None:
    ensure_dir(destination)
    archive_path = archive_path.resolve()
    destination = destination.resolve()

    def _is_safe(member_path: Path) -> bool:
        try:
            member_path.resolve().relative_to(destination)
            return True
        except ValueError:
            return False

    if zipfile.is_zipfile(archive_path):
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                if not member.filename:
                    continue
                target = destination / member.filename
                if not _is_safe(target):
                    raise DeployError(f"unsafe zip member path: {member.filename}")
            archive.extractall(destination)
        return

    if tarfile.is_tarfile(archive_path):
        with tarfile.open(archive_path) as archive:
            for member in archive.getmembers():
                if not member.name:
                    continue
                target = destination / member.name
                if not _is_safe(target):
                    raise DeployError(f"unsafe tar member path: {member.name}")
            archive.extractall(destination)
        return

    raise DeployError(f"unsupported archive format: {archive_path}")


def pick_runtime_bundle(manifest: Dict[str, Any], env_info: Dict[str, Any]) -> Dict[str, Any]:
    bundles = (manifest.get("llama_cpp") or {}).get("runtime_bundles") or []
    system_name = str((env_info.get("os") or {}).get("system") or "").lower()
    arch = str((env_info.get("cpu") or {}).get("architecture") or "").lower()
    target_key = f"{system_name}_{arch}".replace(
        "amd64", "x64").replace("x86_64", "x64")
    for item in bundles:
        if not isinstance(item, dict):
            continue
        if str(item.get("name") or "").lower() == target_key:
            return item
    return bundles[0] if bundles else {}
