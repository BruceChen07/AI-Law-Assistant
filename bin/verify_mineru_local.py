"""MinerU 本地模型离线验证脚本。

用途：在不依赖 Hugging Face / ModelScope 的前提下，用真实文档验证
``MINERU_MODEL_SOURCE=local`` 的完整加载链路（配置解析 → 子进程 → 产物解析）。

示例::

    python bin/verify_mineru_local.py --pdf data/contracts/sample.pdf
    python bin/verify_mineru_local.py --pdf data/contracts/sample.pdf --block-network

退出码：0 表示本地模型就绪且 OCR 产出文本；1 表示校验或 OCR 失败。
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _default_pdf() -> str:
    for pattern in ("data/contracts/*.pdf", "data/uploads/*.pdf", "data/uploads/**/*.pdf"):
        matches = sorted(REPO_ROOT.glob(pattern))
        if matches:
            return str(matches[0])
    return ""


def _block_network() -> None:
    """把出站代理指向不可用端口，证明全流程不触网。

    MinerU 会在 127.0.0.1 起本地 API 并探测，因此仍需要 NO_PROXY 放行环回地址。
    """
    dead = "http://127.0.0.1:9"
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        os.environ[key] = dead
    no_proxy = ["127.0.0.1", "localhost"]
    existing = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    for token in existing.split(","):
        token = token.strip()
        if token and token not in no_proxy:
            no_proxy.append(token)
    value = ",".join(no_proxy)
    os.environ["NO_PROXY"] = value
    os.environ["no_proxy"] = value


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify MinerU local model OCR offline.")
    parser.add_argument("--pdf", default="", help="待验证的 PDF/图片路径（默认取 data/contracts 下首个 PDF）")
    parser.add_argument("--output-dir", default="", help="产物输出目录（默认使用临时目录）")
    parser.add_argument("--lang", default="")
    parser.add_argument("--dpi", type=int, default=0)
    parser.add_argument("--block-network", action="store_true",
                        help="把出站代理指向不可用端口，证明流程不访问外网")
    parser.add_argument("--deep", action="store_true",
                        help="按 manifest 逐文件校验 sha256（较慢）")
    args = parser.parse_args()

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    if args.block_network:
        _block_network()

    from app.core.config import get_config
    from app.core.mineru_models import inspect_local_models
    from app.core.mineru_ocr import ocr_document, run_mineru_extract

    cfg = get_config()
    mineru_cfg = cfg.get("mineru") or {}
    status = inspect_local_models(cfg, deep=bool(args.deep), verify_sha256=bool(args.deep))
    print(json.dumps({"stage": "inspect", "status": status}, ensure_ascii=False, indent=2))
    if not status.get("ok"):
        print("RESULT=FAIL reason=local_models_incomplete", file=sys.stderr)
        return 1

    pdf = args.pdf or _default_pdf()
    if not pdf:
        print("RESULT=FAIL reason=no_input_document", file=sys.stderr)
        return 1
    if not os.path.exists(pdf):
        print(f"RESULT=FAIL reason=input_missing path={pdf}", file=sys.stderr)
        return 1

    lang = args.lang or str(mineru_cfg.get("lang") or "ch")
    dpi = args.dpi or int(cfg.get("ocr_dpi") or 220)
    started = time.perf_counter()
    artifacts = {}
    if args.output_dir:
        result = run_mineru_extract(pdf, output_dir=args.output_dir)
        text = str(result.get("text") or "")
        pages = int(result.get("pages") or 0)
        artifacts = {
            key: str(result.get(key) or "")
            for key in ("md_path", "middle_path", "content_list_path", "model_path", "output_dir")
        }
    else:
        text, pages = ocr_document(pdf, lang, dpi, doc_type="pdf")
    elapsed = round(time.perf_counter() - started, 2)
    summary = {
        "stage": "ocr",
        "pdf": pdf,
        "pages": pages,
        "text_len": len(text),
        "elapsed_sec": elapsed,
        "model_source": str(mineru_cfg.get("model_source") or ""),
        "hf_offline": os.environ.get("HF_HUB_OFFLINE", ""),
        "blocked_network": bool(args.block_network),
        "output_dir": args.output_dir,
        "artifacts": artifacts,
        "sample": text[:200],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if pages <= 0 or not text.strip():
        print("RESULT=FAIL reason=empty_ocr_output", file=sys.stderr)
        return 1
    print("RESULT=OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
