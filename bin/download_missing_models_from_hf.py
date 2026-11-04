import argparse
import json
import os
import shutil
from pathlib import Path

import torch
from huggingface_hub import snapshot_download as hf_snapshot_download
from transformers import AutoModel, AutoTokenizer


ROOT = Path(__file__).resolve().parent.parent
STAGING_DIR = ROOT / ".runtime" / "hf-staging"
MODELSCOPE_HOME = ROOT / ".runtime" / "modelscope-home"
MODELSCOPE_CACHE = ROOT / ".runtime" / "modelscope-cache"
REPORT_PATH = ROOT / ".runtime" / "model-download-report.json"


def _copy_if_exists(src_dir: Path, dst_dir: Path, name: str) -> None:
    src = src_dir / name
    if src.exists():
        shutil.copy2(src, dst_dir / name)


def _log(message: str) -> None:
    print(message, flush=True)


def _download_repo_hf(repo_id: str, local_dir: Path) -> Path:
    _log(f"[download] huggingface {repo_id}")
    local_dir.mkdir(parents=True, exist_ok=True)
    hf_snapshot_download(
        repo_id=repo_id,
        local_dir=str(local_dir),
        local_dir_use_symlinks=False,
        resume_download=True,
    )
    return local_dir


def _download_repo_modelscope(repo_id: str) -> Path:
    _log(f"[download] modelscope {repo_id}")
    os.environ["MODELSCOPE_HOME"] = str(MODELSCOPE_HOME)
    os.environ["MODELSCOPE_CACHE"] = str(MODELSCOPE_CACHE)
    MODELSCOPE_HOME.mkdir(parents=True, exist_ok=True)
    MODELSCOPE_CACHE.mkdir(parents=True, exist_ok=True)
    from modelscope import snapshot_download as ms_snapshot_download

    snapshot_path = ms_snapshot_download(repo_id, cache_dir=str(MODELSCOPE_CACHE))
    return Path(snapshot_path)


def _download_repo(repo_id: str, source: str, local_dir: Path) -> Path:
    if source == "modelscope":
        return _download_repo_modelscope(repo_id)
    return _download_repo_hf(repo_id, local_dir)


def _export_embedding(repo_id: str, source: str, target_dir: Path) -> dict:
    _log(f"[embedding] start {repo_id} -> {target_dir}")
    source_dir = STAGING_DIR / repo_id.replace("/", "__")
    source_dir = _download_repo(repo_id, source, source_dir)

    target_dir.mkdir(parents=True, exist_ok=True)
    _log(f"[embedding] load tokenizer {repo_id}")
    tokenizer = AutoTokenizer.from_pretrained(source_dir, use_fast=True)
    _log(f"[embedding] load model {repo_id}")
    model = AutoModel.from_pretrained(source_dir)
    model.eval()

    for file_name in [
        "config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "vocab.txt",
        "vocab.json",
        "merges.txt",
        "sentencepiece.bpe.model",
    ]:
        _copy_if_exists(source_dir, target_dir, file_name)

    dummy_input = tokenizer(
        "warmup text",
        return_tensors="pt",
        padding=True,
        truncation=True,
        max_length=512,
    )
    input_names = ["input_ids", "attention_mask", "token_type_ids"]
    dynamic_axes = {
        "input_ids": {0: "batch_size", 1: "sequence_length"},
        "attention_mask": {0: "batch_size", 1: "sequence_length"},
    }
    model_inputs = (dummy_input["input_ids"], dummy_input["attention_mask"])
    if "token_type_ids" in dummy_input:
        model_inputs = (
            dummy_input["input_ids"],
            dummy_input["attention_mask"],
            dummy_input["token_type_ids"],
        )
        dynamic_axes["token_type_ids"] = {0: "batch_size", 1: "sequence_length"}
    else:
        input_names = ["input_ids", "attention_mask"]

    onnx_path = target_dir / "model.onnx"
    _log(f"[embedding] export onnx {onnx_path}")
    torch.onnx.export(
        model,
        model_inputs,
        str(onnx_path),
        input_names=input_names,
        output_names=["last_hidden_state", "pooler_output"],
        dynamic_axes=dynamic_axes,
        opset_version=14,
        do_constant_folding=True,
    )
    if not onnx_path.exists():
        raise RuntimeError(f"onnx export finished but file missing: {onnx_path}")
    _log(f"[embedding] done {onnx_path}")

    return {
        "repo_id": repo_id,
        "source": source,
        "target_dir": str(target_dir),
        "onnx_path": str(onnx_path),
    }


def _download_reranker(repo_id: str, source: str, target_dir: Path) -> dict:
    _log(f"[reranker] start {repo_id} -> {target_dir}")
    target_dir.mkdir(parents=True, exist_ok=True)
    source_dir = STAGING_DIR / repo_id.replace("/", "__")
    source_dir = _download_repo(repo_id, source, source_dir)
    for item in source_dir.iterdir():
        if item.name == ".git" or item.name == ".cache":
            continue
        dst = target_dir / item.name
        if item.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(item, dst)
        else:
            shutil.copy2(item, dst)
    return {
        "repo_id": repo_id,
        "source": source,
        "target_dir": str(target_dir),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download missing embedding/reranker models from Hugging Face."
    )
    parser.add_argument(
        "--source",
        choices=["huggingface", "modelscope"],
        default="modelscope",
        help="Model download source.",
    )
    parser.add_argument(
        "--skip-zh-embedding",
        action="store_true",
        help="Skip zh embedding export.",
    )
    parser.add_argument(
        "--skip-en-embedding",
        action="store_true",
        help="Skip en embedding export.",
    )
    parser.add_argument(
        "--skip-en-reranker",
        action="store_true",
        help="Skip en reranker download.",
    )
    args = parser.parse_args()

    results = {"ok": True, "items": []}
    try:
        if not args.skip_zh_embedding:
            results["items"].append(
                _export_embedding(
                    "BAAI/bge-small-zh-v1.5",
                    args.source,
                    ROOT / "models" / "embedding" / "zh",
                )
            )
        if not args.skip_en_embedding:
            results["items"].append(
                _export_embedding(
                    "BAAI/bge-small-en-v1.5",
                    args.source,
                    ROOT / "models" / "embedding" / "en",
                )
            )
        if not args.skip_en_reranker:
            results["items"].append(
                _download_reranker(
                    "BAAI/bge-reranker-base",
                    args.source,
                    ROOT / "models" / "reranker" / "en",
                )
            )
    except Exception as exc:
        results["ok"] = False
        results["error"] = str(exc)
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 1

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
