import json
import os
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Tuple, Dict, Any, Optional
from app.core.config import get_config
from app.core.model_hub import get_model_source_order

logger = logging.getLogger("law_assistant")


def _pick_lang(default_lang: str, ocr_langs: str) -> str:
    if default_lang:
        return default_lang
    langs = (ocr_langs or "").lower()
    if "chi" in langs or "zh" in langs:
        return "ch"
    if "eng" in langs or "en" in langs:
        return "en"
    return "ch"


def _read_first_file(dir_path: str, suffix: str, stem: Optional[str] = None) -> str:
    if not os.path.exists(dir_path):
        return ""
    root = Path(dir_path)
    preferred = []
    if stem:
        preferred = sorted([p for p in root.rglob(f"{stem}{suffix}") if p.is_file()])
        if preferred:
            return str(preferred[0])
    files = sorted([p for p in root.rglob(f"*{suffix}") if p.is_file()])
    if not files:
        return ""
    return str(files[0])


def _read_pages_from_middle_json(path: str) -> int:
    if not path or not os.path.exists(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        pages = data.get("pdf_info")
        if isinstance(pages, list):
            return len(pages)
    except Exception:
        return 0
    return 0


def _run_mineru(path: str, out_dir: str) -> Dict[str, Any]:
    cfg = get_config()
    mineru_cfg: Dict[str, Any] = cfg.get("mineru") or {}
    backend = str(mineru_cfg.get("backend", "hybrid-auto-engine"))
    method = str(mineru_cfg.get("method", "auto"))
    device = str(mineru_cfg.get("device", "cpu"))
    formula = bool(mineru_cfg.get("formula", True))
    table = bool(mineru_cfg.get("table", True))
    model_source = str(mineru_cfg.get("model_source", "auto")).strip().lower()
    timeout = int(mineru_cfg.get("timeout", 900))
    ocr_langs = str(cfg.get("ocr_languages", "chi_sim+eng"))
    mineru_lang = str(mineru_cfg.get("lang") or _pick_lang("", ocr_langs))
    sources = get_model_source_order(cfg) if model_source == "auto" else [model_source]
    sources = sources or ["huggingface", "modelscope"]
    last_error = None
    for source in sources:
        cmd = [
            "mineru",
            "-p", path,
            "-o", out_dir,
            "-m", method,
            "-b", backend,
            "-l", mineru_lang,
            "-d", device,
            "--source", source,
        ]
        if not formula:
            cmd += ["-f", "false"]
        if not table:
            cmd += ["-t", "false"]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=timeout)
            logger.info("mineru_extract_ready source=%s file=%s", source, path)
            last_error = None
            break
        except Exception as e:
            last_error = e
            logger.warning("mineru_extract_failed source=%s file=%s err=%s", source, path, str(e))
            continue
    if last_error is not None:
        raise last_error
    stem = os.path.splitext(os.path.basename(path))[0]
    md_path = _read_first_file(out_dir, ".md", stem=stem)
    middle_path = _read_first_file(out_dir, "_middle.json", stem=stem)
    content_list_path = _read_first_file(out_dir, "_content_list.json", stem=stem)
    model_path = _read_first_file(out_dir, "_model.json", stem=stem)
    text = ""
    if md_path and os.path.exists(md_path):
        with open(md_path, "r", encoding="utf-8") as f:
            text = f.read()
    pages = _read_pages_from_middle_json(middle_path)
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
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        return _run_mineru(path, output_dir)
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
                dst = tempfile.mktemp(prefix="mineru_artifact_", suffix=os.path.splitext(src)[1])
                with open(src, "rb") as rf:
                    blob = rf.read()
                with open(dst, "wb") as wf:
                    wf.write(blob)
                copied[key] = dst
        result.update(copied)
        return result


def ocr_pdf(path: str, lang: str, dpi: int) -> Tuple[str, int]:
    cfg = get_config()
    mineru_cfg: Dict[str, Any] = cfg.get("mineru") or {}
    output_dir = str(mineru_cfg.get("output_dir", "")).strip()
    result = run_mineru_extract(path, output_dir=output_dir)
    return str(result.get("text") or ""), int(result.get("pages") or 0)
