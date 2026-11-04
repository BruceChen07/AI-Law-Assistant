"""MinerU 本地模型部署：路径解析、完整性校验与断点续传下载。

本地目录结构（默认 ``models/mineru``）::

    mineru/
    ├── mineru.json          # MinerU 工具链配置，models-dir 指向下面两个目录
    ├── manifest.json        # 下载清单（文件大小 + sha256）
    ├── pipeline/            # PDF-Extract-Kit-1.0 仓库内容（models/...）
    └── vlm/                 # MinerU2.5-Pro-2604-1.2B 仓库内容

运行 ``MINERU_MODEL_SOURCE=local`` 时，MinerU 只读 ``MINERU_TOOLS_CONFIG_JSON``
指向的配置文件里的 ``models-dir``，不会再访问 Hugging Face / ModelScope。
"""

import fnmatch
import hashlib
import json
import logging
import os
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

import requests

from app.core.config import get_config_path

logger = logging.getLogger("law_assistant.mineru_models")

SUPPORTED_SOURCES = ("huggingface", "modelscope")
MODEL_SOURCES = SUPPORTED_SOURCES + ("local",)
SUPPORTED_KINDS = ("pipeline", "vlm")

REPO_IDS = {
    "pipeline": {
        "huggingface": "opendatalab/PDF-Extract-Kit-1.0",
        "modelscope": "OpenDataLab/PDF-Extract-Kit-1.0",
    },
    "vlm": {
        "huggingface": "opendatalab/MinerU2.5-Pro-2604-1.2B",
        "modelscope": "OpenDataLab/MinerU2.5-Pro-2604-1.2B",
    },
}

DEFAULT_ENDPOINTS = {
    "huggingface": "https://huggingface.co",
    "modelscope": "https://www.modelscope.cn",
}

DEFAULT_REVISIONS = {"huggingface": "main", "modelscope": "master"}

DEFAULT_PATTERNS = {
    "pipeline": [
        "models/Layout/PP-DocLayoutV2/*",
        "models/MFR/unimernet_hf_small_2503/*",
        "models/OCR/paddleocr_torch/*",
        "models/TabCls/paddle_table_cls/*",
        "models/OriCls/paddle_orientation_classification/*",
        "models/TabRec/SlanetPlus/*",
        "models/TabRec/UnetStructure/*",
    ],
    "vlm": ["*"],
}

PIPELINE_REQUIRED = [
    "models/Layout/PP-DocLayoutV2/model.safetensors",
    "models/OCR/paddleocr_torch",
]
PIPELINE_FORMULA_REQUIRED = ["models/MFR/unimernet_hf_small_2503/model.safetensors"]
PIPELINE_TABLE_REQUIRED = [
    "models/TabRec/SlanetPlus/slanet-plus.onnx",
    "models/TabRec/UnetStructure/unet.onnx",
    "models/TabCls/paddle_table_cls/PP-LCNet_x1_0_table_cls.onnx",
    "models/OriCls/paddle_orientation_classification/PP-LCNet_x1_0_doc_ori.onnx",
]
VLM_REQUIRED = ["config.json"]

DEFAULT_SETTINGS = {
    "max_workers": 3,
    "retries": 3,
    "retry_backoff_sec": 3,
    "connect_timeout_sec": 15,
    "read_timeout_sec": 120,
    "chunk_size": 1024 * 1024,
    "progress_log_bytes": 256 * 1024 * 1024,
}

_STATE_LOCK = threading.Lock()
_STATE: Dict[str, Any] = {
    "running": False,
    "ok": False,
    "stage": "idle",
    "message": "",
    "error": "",
    "started_at": "",
    "finished_at": "",
}


class MineruModelError(RuntimeError):
    """MinerU 本地模型相关错误基类。"""


class MineruModelPathError(MineruModelError):
    """本地目录不可写、权限不足或磁盘空间不足。"""


class MineruModelDownloadError(MineruModelError):
    """下载过程网络异常或远端返回异常。"""


class MineruModelIntegrityError(MineruModelError):
    """下载文件大小/sha256 与远端清单不一致。"""


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_test_process() -> bool:
    return "pytest" in sys.modules or "PYTEST_CURRENT_TEST" in os.environ


def get_mineru_config(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if cfg is None:
        from app.core.config import get_config
        cfg = get_config()
    raw = cfg.get("mineru")
    return raw if isinstance(raw, dict) else {}


def _config_base_dir() -> str:
    return os.path.dirname(os.path.abspath(get_config_path()))


def _resolve_path(value: str, default: str) -> str:
    raw = _clean_text(value) or default
    raw = raw.replace("\\", os.sep).replace("/", os.sep)
    if os.path.isabs(raw):
        return os.path.normpath(raw)
    return os.path.abspath(os.path.join(_config_base_dir(), raw))


def resolve_models_root(cfg: Optional[Dict[str, Any]] = None) -> str:
    return _resolve_models_root(get_mineru_config(cfg))


def _resolve_models_root(mineru_cfg: Dict[str, Any]) -> str:
    return _resolve_path(mineru_cfg.get("local_models_dir"), "../models/mineru")


def resolve_mineru_config_path(mineru_cfg: Dict[str, Any]) -> str:
    """按 mineru 配置块解析 MinerU 工具链配置文件路径。"""
    explicit = _clean_text(mineru_cfg.get("local_config_path"))
    if explicit:
        return _resolve_path(explicit, "mineru.json")
    return os.path.join(_resolve_models_root(mineru_cfg), "mineru.json")


def resolve_config_path(cfg: Optional[Dict[str, Any]] = None,
                        root: Optional[str] = None) -> str:
    mineru_cfg = get_mineru_config(cfg)
    if root:
        explicit = _clean_text(mineru_cfg.get("local_config_path"))
        if explicit:
            return _resolve_path(explicit, "mineru.json")
        return os.path.join(root, "mineru.json")
    return resolve_mineru_config_path(mineru_cfg)


def resolve_manifest_path(root: Optional[str] = None,
                          cfg: Optional[Dict[str, Any]] = None) -> str:
    return os.path.join(root or resolve_models_root(cfg), "manifest.json")


def _normalize_source(value: Any, default: str = "modelscope") -> str:
    source = _clean_text(value).lower() or default
    return source if source in SUPPORTED_SOURCES else default


def _normalize_model_source(value: Any, default: str = "huggingface") -> str:
    source = _clean_text(value).lower() or default
    return source if source in MODEL_SOURCES else default


def required_kinds(mineru_cfg: Dict[str, Any]) -> List[str]:
    mode = _clean_text(mineru_cfg.get("mode", "auto")).lower()
    if mode in {"force_pipeline", "pipeline"}:
        return ["pipeline"]
    return ["pipeline", "vlm"]


def _required_entries(mineru_cfg: Dict[str, Any]) -> Dict[str, List[Tuple[str, bool]]]:
    """返回 {kind: [(相对路径, 是否必需), ...]}。"""
    formula = bool(mineru_cfg.get("formula", True))
    table = bool(mineru_cfg.get("table", True))
    entries: Dict[str, List[Tuple[str, bool]]] = {
        "pipeline": [(path, True) for path in PIPELINE_REQUIRED],
        "vlm": [(path, True) for path in VLM_REQUIRED],
    }
    if formula:
        entries["pipeline"].extend((path, True) for path in PIPELINE_FORMULA_REQUIRED)
    else:
        entries["pipeline"].extend((path, False) for path in PIPELINE_FORMULA_REQUIRED)
    if table:
        entries["pipeline"].extend((path, True) for path in PIPELINE_TABLE_REQUIRED)
    else:
        entries["pipeline"].extend((path, False) for path in PIPELINE_TABLE_REQUIRED)
    kinds = required_kinds(mineru_cfg)
    return {kind: entries[kind] for kind in kinds}


def _path_ready(path: str) -> bool:
    if os.path.isdir(path):
        for _root, _dirs, files in os.walk(path):
            if files:
                return True
        return False
    return os.path.isfile(path) and os.path.getsize(path) > 0


def _has_safetensors(directory: str) -> bool:
    if not os.path.isdir(directory):
        return False
    for name in os.listdir(directory):
        if name.endswith(".safetensors") and os.path.getsize(os.path.join(directory, name)) > 0:
            return True
    return False


def _load_manifest(root: str) -> Dict[str, Any]:
    path = resolve_manifest_path(root)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        logger.warning("mineru_manifest_unreadable path=%s", path)
        return {}


def _deep_check(root: str, manifest: Dict[str, Any], verify_sha256: bool) -> List[str]:
    problems: List[str] = []
    kinds = manifest.get("kinds")
    if not isinstance(kinds, dict) or not kinds:
        return ["manifest"]
    for kind, info in kinds.items():
        files = (info or {}).get("files") if isinstance(info, dict) else None
        if not isinstance(files, dict):
            problems.append(f"{kind}/manifest")
            continue
        for rel, meta in files.items():
            path = os.path.join(root, kind, rel.replace("/", os.sep))
            expected_size = int((meta or {}).get("size") or 0)
            if not os.path.exists(path):
                problems.append(f"{kind}/{rel}")
                continue
            if expected_size and os.path.getsize(path) != expected_size:
                problems.append(f"{kind}/{rel}")
                continue
            expected_sha = _clean_text((meta or {}).get("sha256"))
            if verify_sha256 and expected_sha and _sha256_file(path) != expected_sha:
                problems.append(f"{kind}/{rel}")
    return problems


def inspect_local_models(cfg: Optional[Dict[str, Any]] = None,
                         deep: bool = False,
                         verify_sha256: bool = False) -> Dict[str, Any]:
    """检查本地模型是否完整，不发起任何网络请求。"""
    mineru_cfg = get_mineru_config(cfg)
    root = resolve_models_root(cfg)
    config_path = resolve_config_path(cfg, root)
    missing: List[str] = []
    optional_missing: List[str] = []

    for kind, entries in _required_entries(mineru_cfg).items():
        if not os.path.isdir(os.path.join(root, kind)):
            missing.append(f"{kind}/")
            continue
        for rel, is_required in entries:
            path = os.path.join(root, kind, rel.replace("/", os.sep))
            if _path_ready(path):
                continue
            (missing if is_required else optional_missing).append(f"{kind}/{rel}")
        if kind == "vlm" and not _has_safetensors(os.path.join(root, "vlm")):
            missing.append("vlm/*.safetensors")

    if deep and not missing:
        missing.extend(_deep_check(root, _load_manifest(root), verify_sha256))

    ok = not missing
    message = ""
    if not ok:
        message = (
            "MinerU 本地模型不完整，缺失: "
            + ", ".join(missing[:6])
            + (" ..." if len(missing) > 6 else "")
            + "；可执行 python bin/ensure_local_models.py --types mineru --download 完成部署"
        )
    elif not os.path.exists(config_path):
        message = "MinerU 本地模型已就绪，但缺少 mineru.json，将在启动时自动生成"

    return {
        "ok": ok,
        "root": root,
        "config_path": config_path,
        "manifest_path": resolve_manifest_path(root),
        "required_kinds": required_kinds(mineru_cfg),
        "missing": missing,
        "optional_missing": optional_missing,
        "deep_checked": bool(deep),
        "message": message,
    }


def _mineru_config_payload(root: str) -> Dict[str, Any]:
    return {
        "models-dir": {
            "pipeline": os.path.join(root, "pipeline"),
            "vlm": os.path.join(root, "vlm"),
        },
        "latex-delimiter-config": {
            "display": {"left": "$$", "right": "$$"},
            "inline": {"left": "$", "right": "$"},
        },
        "llm-aided-config": {
            "title_aided": {"enable": False, "api_key": "", "base_url": "", "model": ""}
        },
        "config_version": "1.3.1",
    }


def _atomic_write_json(path: str, payload: Dict[str, Any]) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    try:
        os.makedirs(parent, exist_ok=True)
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=4)
        os.replace(tmp_path, path)
    except PermissionError as exc:
        raise MineruModelPathError(
            f"MinerU 本地模型目录无写入权限: {parent}") from exc
    except OSError as exc:
        raise MineruModelPathError(
            f"MinerU 本地模型目录写入失败: {parent} ({exc})") from exc


def write_mineru_config(cfg: Optional[Dict[str, Any]] = None,
                        root: Optional[str] = None) -> str:
    """生成 MinerU 工具链配置（models-dir 指向本地目录）。"""
    models_root = root or resolve_models_root(cfg)
    config_path = resolve_config_path(cfg, models_root)
    payload = _mineru_config_payload(models_root)
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as fh:
                existing = json.load(fh)
            if existing == payload:
                return config_path
        except (OSError, ValueError):
            pass
    _atomic_write_json(config_path, payload)
    logger.info("mineru_config_written path=%s", config_path)
    return config_path


def resolve_runtime_env(cfg: Optional[Dict[str, Any]] = None,
                        base_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """构造 MinerU 子进程环境变量（local 模式指向本地配置）。"""
    env = dict(base_env if base_env is not None else os.environ)
    mineru_cfg = get_mineru_config(cfg)
    source = _normalize_model_source(mineru_cfg.get("model_source", "huggingface"))
    env["MINERU_MODEL_SOURCE"] = source
    if source == "local":
        root = resolve_models_root(cfg)
        env["MINERU_TOOLS_CONFIG_JSON"] = resolve_config_path(cfg, root)
    return env


def get_status() -> Dict[str, Any]:
    with _STATE_LOCK:
        return dict(_STATE)


def _set_state(**kwargs: Any) -> None:
    with _STATE_LOCK:
        _STATE.update(kwargs)


def _sha256_file(path: str, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_settings(mineru_cfg: Dict[str, Any]) -> Dict[str, Any]:
    settings = dict(DEFAULT_SETTINGS)
    for key in ("max_workers", "retries", "retry_backoff_sec",
                "connect_timeout_sec", "read_timeout_sec", "chunk_size"):
        raw = mineru_cfg.get(f"download_{key}")
        if raw is not None:
            try:
                value = int(raw)
            except (TypeError, ValueError):
                continue
            settings[key] = max(0, value) if key == "retry_backoff_sec" else max(1, value)
    endpoints = dict(DEFAULT_ENDPOINTS)
    raw_endpoints = mineru_cfg.get("download_endpoints")
    if isinstance(raw_endpoints, dict):
        for key, value in raw_endpoints.items():
            if key in endpoints and _clean_text(value):
                endpoints[key] = _clean_text(value).rstrip("/")
    revisions = dict(DEFAULT_REVISIONS)
    raw_revisions = mineru_cfg.get("download_revisions")
    if isinstance(raw_revisions, dict):
        for key, value in raw_revisions.items():
            if key in revisions and _clean_text(value):
                revisions[key] = _clean_text(value)
    settings["endpoints"] = endpoints
    settings["revisions"] = revisions
    return settings


def _patterns_for(kind: str, mineru_cfg: Dict[str, Any]) -> List[str]:
    raw = mineru_cfg.get("download_patterns")
    if isinstance(raw, dict) and isinstance(raw.get(kind), list) and raw[kind]:
        return [str(item) for item in raw[kind]]
    return list(DEFAULT_PATTERNS[kind])


def _match_any(path: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def _http_json(session: requests.Session, url: str, settings: Dict[str, Any]) -> Any:
    timeout = (settings["connect_timeout_sec"], settings["read_timeout_sec"])
    try:
        resp = session.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        raise MineruModelDownloadError(f"获取模型清单失败: {url} ({exc})") from exc
    except ValueError as exc:
        raise MineruModelDownloadError(f"模型清单不是合法 JSON: {url}") from exc


def _fetch_remote_files(source: str, repo: str, revision: str,
                        endpoint: str, settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    """返回 [{"path","size","sha256"}]。sha256 缺失时按大小校验。"""
    session = requests.Session()
    try:
        if source == "huggingface":
            url = f"{endpoint}/api/models/{repo}/tree/{quote(revision)}?recursive=true"
            files: List[Dict[str, Any]] = []
            while url:
                timeout = (settings["connect_timeout_sec"], settings["read_timeout_sec"])
                try:
                    resp = session.get(url, timeout=timeout)
                    resp.raise_for_status()
                    rows = resp.json()
                except requests.RequestException as exc:
                    raise MineruModelDownloadError(
                        f"获取模型清单失败: {url} ({exc})") from exc
                for row in rows if isinstance(rows, list) else []:
                    if row.get("type") != "file":
                        continue
                    files.append({
                        "path": str(row.get("path") or ""),
                        "size": int(row.get("size") or 0),
                        "sha256": _clean_text((row.get("lfs") or {}).get("oid")),
                    })
                url = (resp.links.get("next") or {}).get("url") or ""
            return [item for item in files if item["path"]]

        url = (
            f"{endpoint}/api/v1/models/{repo}/repo/files"
            f"?Revision={quote(revision)}&Recursive=True"
        )
        payload = _http_json(session, url, settings)
        rows = ((payload or {}).get("Data") or {}).get("Files") or []
        files = []
        for row in rows:
            if _clean_text(row.get("Type")).lower() not in {"blob", "file"}:
                continue
            files.append({
                "path": _clean_text(row.get("Path")),
                "size": int(row.get("Size") or 0),
                "sha256": _clean_text(row.get("Sha256")),
            })
        return [item for item in files if item["path"]]
    finally:
        session.close()


def _file_url(source: str, repo: str, revision: str, endpoint: str, path: str) -> str:
    quoted = quote(path)
    if source == "huggingface":
        return f"{endpoint}/{repo}/resolve/{quote(revision)}/{quoted}"
    return f"{endpoint}/models/{repo}/resolve/{quote(revision)}/{quoted}"


_THREAD_LOCAL = threading.local()


def _thread_session() -> requests.Session:
    session = getattr(_THREAD_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        _THREAD_LOCAL.session = session
    return session


def _download_file(url: str, dest_path: str, *, size: int, sha256: str,
                   settings: Dict[str, Any]) -> Dict[str, Any]:
    """单文件下载：支持 HTTP Range 断点续传 + 大小/sha256 校验。"""
    dest = os.path.normpath(dest_path)
    part = f"{dest}.part"
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
    except PermissionError as exc:
        raise MineruModelPathError(
            f"MinerU 本地模型目录无写入权限: {os.path.dirname(dest)}") from exc
    except OSError as exc:
        raise MineruModelPathError(
            f"MinerU 本地模型目录创建失败: {os.path.dirname(dest)} ({exc})") from exc

    if os.path.exists(dest) and os.path.getsize(dest) == size:
        if not sha256 or _sha256_file(dest) == sha256:
            return {"path": dest, "status": "cached", "size": size}

    timeout = (settings["connect_timeout_sec"], settings["read_timeout_sec"])
    retries = max(1, settings["retries"])
    last_error: Optional[Exception] = None

    for attempt in range(1, retries + 1):
        offset = os.path.getsize(part) if os.path.exists(part) else 0
        if size and offset > size:
            os.remove(part)
            offset = 0
        downloaded = offset
        try:
            if not size or offset < size:
                headers = {"Range": f"bytes={offset}-"} if offset else {}
                with _thread_session().get(
                        url, headers=headers, stream=True, timeout=timeout) as resp:
                    if resp.status_code == 416 and offset:
                        os.remove(part)
                        raise MineruModelDownloadError("续传区间无效，重新下载")
                    if resp.status_code not in (200, 206):
                        raise MineruModelDownloadError(
                            f"下载失败 HTTP {resp.status_code}: {url}")
                    if offset and resp.status_code == 200:
                        offset = 0
                        downloaded = 0
                    mode = "ab" if offset else "wb"
                    next_log = downloaded + settings["progress_log_bytes"]
                    with open(part, mode) as fh:
                        for chunk in resp.iter_content(settings["chunk_size"]):
                            if not chunk:
                                continue
                            fh.write(chunk)
                            downloaded += len(chunk)
                            if downloaded >= next_log:
                                logger.info(
                                    "mineru_model_download_progress file=%s downloaded_mb=%.1f total_mb=%.1f",
                                    os.path.basename(dest),
                                    downloaded / 1048576,
                                    size / 1048576 if size else 0,
                                )
                                next_log = downloaded + settings["progress_log_bytes"]
        except (requests.RequestException, OSError) as exc:
            if isinstance(exc, PermissionError):
                raise MineruModelPathError(f"写入失败（权限不足）: {part}") from exc
            last_error = exc
            logger.warning(
                "mineru_model_download_retry attempt=%s/%s file=%s downloaded_mb=%.1f error=%s",
                attempt, retries, os.path.basename(dest),
                downloaded / 1048576, exc,
            )
            if attempt < retries:
                time.sleep(settings["retry_backoff_sec"] * attempt)
            continue

        actual = os.path.getsize(part) if os.path.exists(part) else 0
        if size and actual != size:
            last_error = MineruModelIntegrityError(
                f"文件大小不符 {os.path.basename(dest)}: 期望 {size} 实际 {actual}")
            logger.warning("mineru_model_size_mismatch file=%s expected=%s actual=%s",
                           dest, size, actual)
            os.remove(part)
            continue
        if sha256:
            actual_sha = _sha256_file(part)
            if actual_sha != sha256:
                last_error = MineruModelIntegrityError(
                    f"sha256 校验失败 {os.path.basename(dest)}")
                logger.warning(
                    "mineru_model_sha256_mismatch file=%s expected=%s actual=%s",
                    dest, sha256, actual_sha)
                os.remove(part)
                continue
        os.replace(part, dest)
        return {"path": dest, "status": "resumed" if offset else "downloaded",
                "size": actual}

    message = str(last_error) if last_error else "未知错误"
    if isinstance(last_error, MineruModelError):
        raise last_error
    raise MineruModelDownloadError(
        f"下载失败（已重试 {retries} 次）file={dest} error={message}")


def _stage_kind(kind: str, source: str, root: str, mineru_cfg: Dict[str, Any],
                settings: Dict[str, Any]) -> Dict[str, Any]:
    endpoint = settings["endpoints"][source]
    revision = settings["revisions"][source]
    repo = REPO_IDS[kind][source]
    entries = _fetch_remote_files(source, repo, revision, endpoint, settings)
    patterns = _patterns_for(kind, mineru_cfg)
    selected = [row for row in entries if _match_any(row["path"], patterns)]
    if not selected:
        raise MineruModelError(
            f"远端仓库未匹配到模型文件 repo={repo} patterns={patterns}")

    total_bytes = sum(int(row.get("size") or 0) for row in selected)
    logger.info(
        "mineru_model_stage_start kind=%s source=%s repo=%s files=%s total_mb=%.1f root=%s",
        kind, source, repo, len(selected), total_bytes / 1048576, root,
    )

    target_root = os.path.join(root, kind)
    try:
        os.makedirs(target_root, exist_ok=True)
    except PermissionError as exc:
        raise MineruModelPathError(
            f"MinerU 本地模型目录无写入权限: {target_root}") from exc

    free_bytes = shutil.disk_usage(root).free
    if total_bytes and free_bytes < total_bytes:
        raise MineruModelPathError(
            f"磁盘空间不足: 需要 {total_bytes / 1073741824:.1f} GB, "
            f"可用 {free_bytes / 1073741824:.1f} GB, 目录 {root}")

    files_meta: Dict[str, Dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=settings["max_workers"]) as pool:
        futures = {}
        for row in selected:
            rel = row["path"]
            futures[pool.submit(
                _download_file,
                _file_url(source, repo, revision, endpoint, rel),
                os.path.join(target_root, rel.replace("/", os.sep)),
                size=int(row.get("size") or 0),
                sha256=_clean_text(row.get("sha256")),
                settings=settings,
            )] = (rel, row)
        for future in as_completed(futures):
            rel, row = futures[future]
            result = future.result()
            files_meta[rel] = {
                "size": int(result.get("size") or row.get("size") or 0),
                "sha256": _clean_text(row.get("sha256")),
            }
            logger.info(
                "mineru_model_file_ready kind=%s file=%s status=%s size_mb=%.2f",
                kind, rel, result.get("status"), files_meta[rel]["size"] / 1048576,
            )
    return {
        "source": source,
        "repo": repo,
        "revision": revision,
        "endpoint": endpoint,
        "files": files_meta,
    }


def _merge_manifest(root: str, staged: Dict[str, Any]) -> None:
    manifest = _load_manifest(root)
    kinds = manifest.get("kinds") if isinstance(manifest.get("kinds"), dict) else {}
    kinds.update(staged)
    _atomic_write_json(resolve_manifest_path(root), {
        "version": 1,
        "generated_at": _utc_now_iso(),
        "kinds": kinds,
    })


def stage_models(cfg: Optional[Dict[str, Any]] = None,
                 kinds: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """下载并部署 MinerU 本地模型（断点续传 + 校验），返回部署结果。"""
    mineru_cfg = get_mineru_config(cfg)
    root = resolve_models_root(cfg)
    settings = _download_settings(mineru_cfg)
    primary = _normalize_source(mineru_cfg.get("download_source", "modelscope"))
    fallback = _normalize_source(
        mineru_cfg.get("download_source_fallback", "huggingface"), default="huggingface")
    sources = [primary] if fallback == primary else [primary, fallback]
    selected_kinds = [kind for kind in (kinds or required_kinds(mineru_cfg))
                      if kind in SUPPORTED_KINDS]

    _set_state(running=True, ok=False, stage="downloading", message="",
               error="", started_at=_utc_now_iso(), finished_at="")
    staged: Dict[str, Any] = {}
    errors: List[Exception] = []
    try:
        for kind in selected_kinds:
            last_error: Optional[Exception] = None
            for source in sources:
                try:
                    staged[kind] = _stage_kind(kind, source, root, mineru_cfg, settings)
                    last_error = None
                    break
                except MineruModelPathError:
                    raise
                except MineruModelError as exc:
                    last_error = exc
                    logger.warning(
                        "mineru_model_stage_source_failed kind=%s source=%s error=%s",
                        kind, source, exc)
            if last_error is not None:
                errors.append(last_error)
        if errors:
            detail = "; ".join(f"{kind}: {err}" for kind, err in zip(
                [k for k in selected_kinds if k not in staged], errors))
            if all(isinstance(err, MineruModelIntegrityError) for err in errors):
                raise MineruModelIntegrityError(detail)
            raise MineruModelError(detail)

        _merge_manifest(root, staged)
        config_path = write_mineru_config(cfg, root)
        status = inspect_local_models(cfg, deep=True)
        if not status["ok"]:
            raise MineruModelIntegrityError(status["message"])
        _set_state(running=False, ok=True, stage="done", message="",
                   finished_at=_utc_now_iso())
        logger.info(
            "mineru_model_stage_done root=%s config=%s kinds=%s",
            root, config_path, list(staged))
        return {
            "ok": True,
            "root": root,
            "config_path": config_path,
            "kinds": list(staged),
            "downloaded": True,
        }
    except Exception as exc:
        _set_state(running=False, ok=False, stage="failed",
                   error=str(exc), finished_at=_utc_now_iso())
        logger.error("mineru_model_stage_failed root=%s error=%s", root, exc)
        raise


def ensure_local_models(cfg: Optional[Dict[str, Any]] = None,
                        allow_download: bool = False,
                        deep: bool = False) -> Dict[str, Any]:
    """确保本地模型可用；allow_download=False 时只检查不下载。"""
    status = inspect_local_models(cfg, deep=deep)
    if status["ok"]:
        try:
            status["config_path"] = write_mineru_config(cfg, status["root"])
        except MineruModelPathError as exc:
            logger.error("mineru_config_write_failed error=%s", exc)
            status["ok"] = False
            status["message"] = str(exc)
        return status
    if not allow_download:
        return status
    try:
        staged = stage_models(cfg)
    except MineruModelError as exc:
        status["ok"] = False
        status["message"] = str(exc)
        return status
    result = inspect_local_models(cfg, deep=True)
    result["downloaded"] = bool(staged.get("downloaded"))
    return result


def _run_download_task(cfg: Dict[str, Any]) -> None:
    try:
        result = ensure_local_models(cfg, allow_download=True)
        if result.get("ok"):
            logger.warning(
                "mineru_local_models_download_done root=%s config=%s",
                result.get("root"), result.get("config_path"))
        else:
            logger.error(
                "mineru_local_models_download_failed message=%s", result.get("message"))
    except Exception:
        _set_state(running=False, ok=False, stage="failed",
                   error="unexpected", finished_at=_utc_now_iso())
        logger.exception("mineru_local_models_download_crashed")


def start_background_download(cfg: Optional[Dict[str, Any]] = None) -> bool:
    with _STATE_LOCK:
        if _STATE["running"]:
            return False
        _STATE.update(running=True, ok=False, stage="scheduled",
                      message="", error="", started_at=_utc_now_iso())
    resolved = cfg
    if resolved is None:
        from app.core.config import get_config
        resolved = get_config()
    thread = threading.Thread(
        target=_run_download_task, args=(resolved,),
        name="mineru-model-download", daemon=True)
    thread.start()
    return True


def ensure_models_on_startup(cfg: Optional[Dict[str, Any]] = None,
                             log: Optional[logging.Logger] = None) -> Dict[str, Any]:
    """服务启动钩子：local 模式下检查/自动下载 MinerU 本地模型。"""
    active_logger = log or logger
    mineru_cfg = get_mineru_config(cfg)
    source = _normalize_model_source(mineru_cfg.get("model_source", "huggingface"))
    if source != "local":
        active_logger.info("mineru_local_models_skipped model_source=%s", source)
        return {"ok": False, "skipped": True, "reason": "model_source != local"}

    status = inspect_local_models(cfg)
    if status["ok"]:
        try:
            config_path = write_mineru_config(cfg, status["root"])
            active_logger.info(
                "mineru_local_models_ready root=%s config=%s kinds=%s",
                status["root"], config_path, status["required_kinds"])
        except MineruModelPathError as exc:
            active_logger.error("mineru_local_models_config_failed error=%s", exc)
        return status

    active_logger.warning(
        "mineru_local_models_missing root=%s missing=%s",
        status["root"], status["missing"][:8])
    if not bool(mineru_cfg.get("auto_download_on_startup", True)):
        active_logger.warning(
            "mineru_local_models_download_disabled hint=%s",
            "python bin/ensure_local_models.py --types mineru --download")
        return status
    if _is_test_process():
        active_logger.info("mineru_local_models_download_skipped reason=test_process")
        return status

    if bool(mineru_cfg.get("auto_download_background", True)):
        if start_background_download(cfg):
            active_logger.warning(
                "mineru_local_models_download_started background=True source=%s fallback=%s",
                mineru_cfg.get("download_source", "modelscope"),
                mineru_cfg.get("download_source_fallback", "huggingface"))
        else:
            active_logger.info("mineru_local_models_download_already_running")
        return status

    active_logger.warning("mineru_local_models_download_started background=False")
    return ensure_local_models(cfg, allow_download=True)
