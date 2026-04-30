import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Tuple, Dict, Any, Optional, List
from app.core.config import get_config


logger = logging.getLogger(__name__)

_CAPABILITIES_CACHE: Dict[str, Any] = {"ts": 0.0, "data": None}
_KNOWN_BACKENDS = ["hybrid-auto-engine", "pipeline", "vlm-transformers", "vlm-sglang-engine"]


def _run_cmd_text(cmd: List[str], timeout: int = 8) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()


def _detect_gpu() -> bool:
    if not shutil.which("nvidia-smi"):
        return False
    try:
        out = _run_cmd_text(["nvidia-smi", "-L"], timeout=5)
        return bool(out.strip()) and "gpu" in out.lower()
    except Exception:
        return False


def _probe_supported_backends() -> List[str]:
    if not shutil.which("mineru"):
        return []
    txt = ""
    for arg in ("--help", "-h"):
        try:
            txt = _run_cmd_text(["mineru", arg], timeout=8)
            if txt:
                break
        except Exception:
            continue
    if not txt:
        return []
    low = txt.lower()
    return [x for x in _KNOWN_BACKENDS if x in low]


def probe_mineru_capabilities(force_refresh: bool = False, cache_ttl_sec: int = 300) -> Dict[str, Any]:
    now = time.time()
    cached = _CAPABILITIES_CACHE.get("data")
    ts = float(_CAPABILITIES_CACHE.get("ts") or 0)
    if (not force_refresh) and cached and (now - ts) < max(1, int(cache_ttl_sec)):
        return dict(cached)
    data = {
        "mineru_cli_found": bool(shutil.which("mineru")),
        "has_gpu": _detect_gpu(),
        "supported_backends": _probe_supported_backends(),
        "probed_at": int(now),
    }
    _CAPABILITIES_CACHE["ts"] = now
    _CAPABILITIES_CACHE["data"] = dict(data)
    logger.info(
        "mineru_capability_probe cli_found=%s has_gpu=%s supported_backends=%s",
        data["mineru_cli_found"], data["has_gpu"], data["supported_backends"],
    )
    return data


def resolve_mineru_backend(mineru_cfg: Dict[str, Any], capabilities: Dict[str, Any]) -> str:
    cfg = mineru_cfg or {}
    mode = str(cfg.get("mode", "auto") or "auto").strip().lower()
    supported = [str(x) for x in (capabilities.get("supported_backends") or []) if str(x).strip()]
    has_gpu = bool(capabilities.get("has_gpu", False))

    if mode == "force_hybrid":
        candidate = "hybrid-auto-engine"
    elif mode == "force_pipeline":
        candidate = "pipeline"
    else:
        candidate = "hybrid-auto-engine" if has_gpu else "pipeline"

    chosen = candidate
    if supported and chosen not in supported:
        if "pipeline" in supported:
            chosen = "pipeline"
        else:
            chosen = supported[0]
    logger.info(
        "mineru_backend_resolved mode=%s has_gpu=%s candidate=%s chosen=%s supported=%s",
        mode, has_gpu, candidate, chosen, supported
    )
    return chosen


def build_backend_attempts(mineru_cfg: Dict[str, Any], capabilities: Dict[str, Any], primary_backend: str) -> List[str]:
    cfg = mineru_cfg or {}
    supported = [str(x).strip() for x in (capabilities.get("supported_backends") or []) if str(x).strip()]
    raw_fallback = cfg.get("fallback_backends")
    if not isinstance(raw_fallback, list):
        raw_fallback = ["pipeline"]
    candidates = [primary_backend] + [str(x).strip() for x in raw_fallback if str(x).strip()]
    seen = set()
    ordered = []
    for b in candidates:
        if b in seen:
            continue
        if supported and b not in supported:
            continue
        seen.add(b)
        ordered.append(b)
    if not ordered:
        ordered = [primary_backend]
    return ordered


def _pick_lang(default_lang: str, ocr_langs: str) -> str:
    if default_lang:
        logger.debug("mineru_pick_lang default_lang_used=%s ocr_langs=%s", default_lang, ocr_langs)
        return default_lang
    langs = (ocr_langs or "").lower()
    if "chi" in langs or "zh" in langs:
        logger.debug("mineru_pick_lang detected=ch ocr_langs=%s", ocr_langs)
        return "ch"
    if "eng" in langs or "en" in langs:
        logger.debug("mineru_pick_lang detected=en ocr_langs=%s", ocr_langs)
        return "en"
    logger.debug("mineru_pick_lang fallback=ch ocr_langs=%s", ocr_langs)
    return "ch"


def _read_first_file(dir_path: str, suffix: str, stem: Optional[str] = None) -> str:
    if not os.path.exists(dir_path):
        logger.debug("mineru_read_first_file dir_missing dir=%s suffix=%s stem=%s", dir_path, suffix, stem)
        return ""
    root = Path(dir_path)
    preferred = []
    if stem:
        preferred = sorted(
            [p for p in root.rglob(f"{stem}{suffix}") if p.is_file()])
        if preferred:
            hit = str(preferred[0])
            logger.debug("mineru_read_first_file preferred_hit suffix=%s stem=%s file=%s", suffix, stem, hit)
            return hit
    files = sorted([p for p in root.rglob(f"*{suffix}") if p.is_file()])
    if not files:
        logger.debug("mineru_read_first_file no_match suffix=%s stem=%s dir=%s", suffix, stem, dir_path)
        return ""
    hit = str(files[0])
    logger.debug("mineru_read_first_file fallback_hit suffix=%s stem=%s file=%s", suffix, stem, hit)
    return hit


def _read_pages_from_middle_json(path: str) -> int:
    if not path or not os.path.exists(path):
        logger.debug("mineru_read_pages missing_middle_json path=%s", path)
        return 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        pages = data.get("pdf_info")
        if isinstance(pages, list):
            page_count = len(pages)
            logger.debug("mineru_read_pages success path=%s pages=%s", path, page_count)
            return page_count
        logger.warning("mineru_read_pages invalid_pdf_info path=%s type=%s", path, type(pages).__name__)
    except Exception:
        logger.exception("mineru_read_pages parse_failed path=%s", path)
        return 0
    return 0


def _run_mineru(path: str, out_dir: str) -> Dict[str, Any]:
    cfg = get_config()
    mineru_cfg: Dict[str, Any] = cfg.get("mineru") or {}
    cap = probe_mineru_capabilities(cache_ttl_sec=int(mineru_cfg.get("probe_cache_ttl_sec", 300)))
    backend = resolve_mineru_backend(mineru_cfg, cap)
    backends = build_backend_attempts(mineru_cfg, cap, backend)
    method = str(mineru_cfg.get("method", "auto"))
    device = str(mineru_cfg.get("device", "cpu"))
    formula = bool(mineru_cfg.get("formula", True))
    table = bool(mineru_cfg.get("table", True))
    model_source = str(mineru_cfg.get("model_source", "huggingface"))
    timeout = int(mineru_cfg.get("timeout", 900))
    ocr_langs = str(cfg.get("ocr_languages", "chi_sim+eng"))
    mineru_lang = str(mineru_cfg.get("lang") or _pick_lang("", ocr_langs))

    logger.info("mineru_backend_attempts selected=%s attempts=%s", backend, backends)

    last_error = None
    for idx, backend_try in enumerate(backends, start=1):
        cmd = [
            "mineru", "-p", path, "-o", out_dir, "-m", method, "-b", backend_try,
            "-l", mineru_lang, "-d", device, "--source", model_source,
        ]
        if not formula:
            cmd += ["-f", "false"]
        if not table:
            cmd += ["-t", "false"]

        cmd_text = subprocess.list2cmdline(cmd)
        logger.info(
            "mineru_ocr_start attempt=%s/%s path=%s out_dir=%s timeout=%s method=%s backend=%s device=%s lang=%s source=%s formula=%s table=%s cmd=%s",
            idx, len(backends), path, out_dir, timeout, method, backend_try, device, mineru_lang, model_source, formula, table, cmd_text
        )
        started = time.perf_counter()
        try:
            proc = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=timeout)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            logger.info(
                "mineru_ocr_success attempt=%s/%s backend=%s elapsed_ms=%s returncode=%s stdout=%s stderr=%s",
                idx, len(backends), backend_try, elapsed_ms, proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip(),
            )
            break
        except subprocess.CalledProcessError as e:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            last_error = e
            logger.error(
                "mineru_ocr_failed attempt=%s/%s backend=%s elapsed_ms=%s returncode=%s cmd=%s stdout=%s stderr=%s",
                idx, len(backends), backend_try, elapsed_ms, e.returncode, cmd_text, (e.stdout or "").strip(), (e.stderr or "").strip(),
            )
            if idx < len(backends):
                logger.warning("mineru_ocr_fallback next_backend=%s", backends[idx])
                continue
            raise
        except subprocess.TimeoutExpired as e:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            last_error = e
            logger.error(
                "mineru_ocr_timeout attempt=%s/%s backend=%s elapsed_ms=%s timeout=%s cmd=%s stdout=%s stderr=%s",
                idx, len(backends), backend_try, elapsed_ms, timeout, cmd_text,
                (e.stdout or "").strip() if e.stdout else "", (e.stderr or "").strip() if e.stderr else "",
            )
            if idx < len(backends):
                logger.warning("mineru_ocr_fallback next_backend=%s", backends[idx])
                continue
            raise
    if last_error and len(backends) == 0:
        raise last_error

    stem = os.path.splitext(os.path.basename(path))[0]
    md_path = _read_first_file(out_dir, ".md", stem=stem)
    middle_path = _read_first_file(out_dir, "_middle.json", stem=stem)
    content_list_path = _read_first_file(
        out_dir, "_content_list.json", stem=stem)
    model_path = _read_first_file(out_dir, "_model.json", stem=stem)
    text = ""
    if md_path and os.path.exists(md_path):
        with open(md_path, "r", encoding="utf-8") as f:
            text = f.read()
    pages = _read_pages_from_middle_json(middle_path)
    logger.info(
        "mineru_ocr_artifacts output_dir=%s md_path=%s middle_path=%s content_list_path=%s model_path=%s text_len=%s pages=%s",
        out_dir,
        md_path,
        middle_path,
        content_list_path,
        model_path,
        len(text),
        pages,
    )
    return {
        "text": text,
        "pages": pages,
        "md_path": md_path,
        "middle_path": middle_path,
        "content_list_path": content_list_path,
        "model_path": model_path,
        "output_dir": out_dir,
    }


def run_mineru_extract(path: str, output_dir: str = "") -> Dict[str, Any]:
    logger.info("mineru_extract_start path=%s output_dir=%s", path, output_dir or "<temp>")
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        result = _run_mineru(path, output_dir)
        logger.info("mineru_extract_done mode=fixed_output output_dir=%s text_len=%s pages=%s", output_dir, len(str(result.get("text") or "")), int(result.get("pages") or 0))
        return result
    with tempfile.TemporaryDirectory(prefix="mineru_extract_") as tmp_dir:
        result = _run_mineru(path, tmp_dir)
        middle_path = str(result.get("middle_path") or "")
        content_list_path = str(result.get("content_list_path") or "")
        md_path = str(result.get("md_path") or "")
        model_path = str(result.get("model_path") or "")
        copied = {
            "middle_path": "",
            "content_list_path": "",
            "md_path": "",
            "model_path": "",
        }
        for key, src in [("middle_path", middle_path), ("content_list_path", content_list_path), ("md_path", md_path), ("model_path", model_path)]:
            if src and os.path.exists(src):
                dst = tempfile.mktemp(
                    prefix="mineru_artifact_", suffix=os.path.splitext(src)[1])
                with open(src, "rb") as rf:
                    blob = rf.read()
                with open(dst, "wb") as wf:
                    wf.write(blob)
                copied[key] = dst
        result.update(copied)
        logger.info(
            "mineru_extract_done mode=temp_output temp_dir=%s copied_md=%s copied_middle=%s copied_content_list=%s copied_model=%s text_len=%s pages=%s",
            tmp_dir,
            copied.get("md_path") or "",
            copied.get("middle_path") or "",
            copied.get("content_list_path") or "",
            copied.get("model_path") or "",
            len(str(result.get("text") or "")),
            int(result.get("pages") or 0),
        )
        return result


def ocr_pdf(path: str, lang: str, dpi: int) -> Tuple[str, int]:
    logger.info("mineru_ocr_pdf_start path=%s lang=%s dpi=%s", path, lang, dpi)
    cfg = get_config()
    mineru_cfg: Dict[str, Any] = cfg.get("mineru") or {}
    output_dir = str(mineru_cfg.get("output_dir", "")).strip()
    result = run_mineru_extract(path, output_dir=output_dir)
    text = str(result.get("text") or "")
    pages = int(result.get("pages") or 0)
    logger.info("mineru_ocr_pdf_done path=%s output_dir=%s text_len=%s pages=%s", path, output_dir or "<temp>", len(text), pages)
    return text, pages
