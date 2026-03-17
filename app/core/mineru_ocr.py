import json
import os
import subprocess
import tempfile
from typing import Tuple, Dict, Any, Optional
from app.core.config import get_config


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
    if stem:
        cand = os.path.join(dir_path, f"{stem}{suffix}")
        if os.path.exists(cand):
            return cand
    files = [f for f in os.listdir(dir_path) if f.endswith(suffix)]
    if not files:
        return ""
    files.sort()
    return os.path.join(dir_path, files[0])


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


def ocr_pdf(path: str, lang: str, dpi: int) -> Tuple[str, int]:
    cfg = get_config()
    mineru_cfg: Dict[str, Any] = cfg.get("mineru") or {}
    backend = str(mineru_cfg.get("backend", "hybrid-auto-engine"))
    method = str(mineru_cfg.get("method", "auto"))
    device = str(mineru_cfg.get("device", "cpu"))
    formula = bool(mineru_cfg.get("formula", True))
    table = bool(mineru_cfg.get("table", True))
    model_source = str(mineru_cfg.get("model_source", "huggingface"))
    timeout = int(mineru_cfg.get("timeout", 900))
    output_dir = str(mineru_cfg.get("output_dir", "")).strip()

    ocr_langs = str(cfg.get("ocr_languages", "chi_sim+eng"))
    mineru_lang = str(mineru_cfg.get("lang") or _pick_lang("", ocr_langs))

    def run_with_output(out_dir: str) -> Tuple[str, int]:
        cmd = [
            "mineru",
            "-p", path,
            "-o", out_dir,
            "-m", method,
            "-b", backend,
            "-l", mineru_lang,
            "-d", device,
            "--source", model_source
        ]
        if not formula:
            cmd += ["-f", "false"]
        if not table:
            cmd += ["-t", "false"]
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=timeout)

        stem = os.path.splitext(os.path.basename(path))[0]
        md_path = _read_first_file(out_dir, ".md", stem=stem)
        middle_path = _read_first_file(out_dir, "_middle.json", stem=stem)
        text = ""
        if md_path and os.path.exists(md_path):
            with open(md_path, "r", encoding="utf-8") as f:
                text = f.read()
        pages = _read_pages_from_middle_json(middle_path)
        return text, pages

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        return run_with_output(output_dir)

    with tempfile.TemporaryDirectory(prefix="mineru_ocr_") as tmp_dir:
        return run_with_output(tmp_dir)