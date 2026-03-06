import os
import json
import argparse
import shutil
from pathlib import Path
from modelscope.hub.snapshot_download import snapshot_download


def resolve_path(base_dir: str, p: str) -> str:
    if not p:
        return ""
    if os.path.isabs(p):
        return p
    return os.path.abspath(os.path.join(base_dir, p))


def copy_if_exists(src_dir: Path, dst_dir: Path, name: str):
    s = src_dir / name
    if s.exists():
        shutil.copy2(s, dst_dir / name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-id", default="BAAI/bge-small-zh-v1.5")
    parser.add_argument("--language", default="zh", choices=["zh", "en"])
    parser.add_argument("--target-dir", default="..\\models\\embedding")
    parser.add_argument("--onnx-name", default="")
    parser.add_argument("--config-path", default="config.json")
    parser.add_argument("--revision", default="master")
    args = parser.parse_args()

    app_dir = os.path.dirname(os.path.abspath(__file__))
    target_dir = Path(resolve_path(app_dir, os.path.join(args.target_dir, args.language)))
    target_dir.mkdir(parents=True, exist_ok=True)

    local_dir = snapshot_download(
        model_id=args.model_id,
        revision=args.revision
    )
    src_dir = Path(local_dir)

    onnx_file = None
    if args.onnx_name:
        p = src_dir / args.onnx_name
        if p.exists():
            onnx_file = p
    if onnx_file is None:
        cands = list(src_dir.rglob("*.onnx"))
        if cands:
            cands.sort(key=lambda x: len(str(x)))
            onnx_file = cands[0]

    if onnx_file is None:
        raise RuntimeError(f"No .onnx file found in {src_dir}")

    onnx_dst = target_dir / onnx_file.name
    shutil.copy2(onnx_file, onnx_dst)

    files_to_copy = [
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "vocab.txt",
        "vocab.json",
        "merges.txt",
        "sentencepiece.bpe.model",
        "config.json"
    ]
    for f in files_to_copy:
        copy_if_exists(src_dir, target_dir, f)

    cfg_path = resolve_path(app_dir, args.config_path)
    if os.path.exists(cfg_path):
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        cfg.setdefault("default_language", "zh")
        profiles = cfg.setdefault("embedding_profiles", {})
        profile = profiles.setdefault(args.language, {})
        profile["embedding_model"] = os.path.relpath(str(onnx_dst), app_dir).replace("/", "\\")
        profile["embedding_tokenizer_dir"] = os.path.relpath(str(target_dir), app_dir).replace("/", "\\")
        profile["embedding_model_id"] = args.model_id
        profile["embedding_source"] = "modelscope"
        profile.setdefault("embedding_max_seq_len", 512)
        profile.setdefault("embedding_pooling", "cls")
        profile.setdefault("embedding_threads", 2)
        if args.language == "en":
            profile.setdefault("embedding_query_instruction", "Represent this sentence for retrieving relevant passages:")
        else:
            profile.setdefault("embedding_query_instruction", "为这个句子生成表示以用于检索相关文章：")
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=4)

    print(json.dumps({
        "language": args.language,
        "model_id": args.model_id,
        "onnx": str(onnx_dst),
        "tokenizer_dir": str(target_dir),
        "config_updated": os.path.exists(cfg_path)
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
