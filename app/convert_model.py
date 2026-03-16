import os
import shutil
from pathlib import Path
from transformers import AutoTokenizer, AutoModel
import torch
from modelscope.hub.snapshot_download import snapshot_download

def main():
    # Ensure model is downloaded
    model_id = "BAAI/bge-small-zh-v1.5"
    try:
        cache_dir = snapshot_download(model_id)
        print(f"Model downloaded to: {cache_dir}")
    except Exception as e:
        print(f"Download failed: {e}")
        return

    # Output directory
    # Use absolute path to avoid confusion
    base_dir = Path(os.getcwd())
    output_dir = base_dir / "models" / "embedding" / "zh"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Copy tokenizer files
    print(f"Copying files from {cache_dir} to {output_dir}")
    for file in Path(cache_dir).glob("*"):
        if file.is_file() and (file.name.startswith("tokenizer") or file.name.endswith(".txt") or file.name.endswith(".json")):
            try:
                shutil.copy2(file, output_dir / file.name)
            except Exception as e:
                print(f"Failed to copy {file.name}: {e}")
    print("Copied tokenizer files")

    # Convert to ONNX
    print("Loading model for conversion...")
    tokenizer = AutoTokenizer.from_pretrained(cache_dir)
    model = AutoModel.from_pretrained(cache_dir)
    model.eval()

    dummy_input = tokenizer("预热文本", return_tensors="pt")
    input_names = ["input_ids", "attention_mask", "token_type_ids"]
    output_names = ["last_hidden_state", "pooler_output"]

    onnx_path = output_dir / "model.onnx"
    print(f"Exporting ONNX to {onnx_path}...")

    try:
        torch.onnx.export(
            model,
            (dummy_input["input_ids"], dummy_input["attention_mask"], dummy_input["token_type_ids"]),
            str(onnx_path),
            input_names=input_names,
            output_names=output_names,
            dynamic_axes={
                "input_ids": {0: "batch_size", 1: "sequence_length"},
                "attention_mask": {0: "batch_size", 1: "sequence_length"},
                "token_type_ids": {0: "batch_size", 1: "sequence_length"}
            },
            opset_version=14
        )
        print("ONNX export complete.")
    except Exception as e:
        print(f"Export failed: {e}")

if __name__ == "__main__":
    main()
