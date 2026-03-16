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
    # Fixed path structure: models/embedding/zh
    output_dir = base_dir / "models" / "embedding" / "zh"
    if not output_dir.exists():
        output_dir.mkdir(parents=True, exist_ok=True)

    # Copy tokenizer files
    print(f"Copying files from {cache_dir} to {output_dir}")
    src_path = Path(cache_dir)
    # Copy all necessary config/tokenizer files
    allow_list = [
        "config.json", "tokenizer.json", "tokenizer_config.json", 
        "vocab.txt", "special_tokens_map.json", "modules.json"
    ]
    
    for item in src_path.iterdir():
        if item.name in allow_list or item.name.endswith(".txt"):
            try:
                shutil.copy2(item, output_dir / item.name)
            except Exception as e:
                print(f"Warning: Failed to copy {item.name}: {e}")

    # Convert to ONNX
    print("Loading model for conversion...")
    tokenizer = AutoTokenizer.from_pretrained(cache_dir)
    model = AutoModel.from_pretrained(cache_dir)
    model.eval()

    # Create dummy input
    dummy_input = tokenizer("预热文本", return_tensors="pt", padding=True, truncation=True, max_length=512)
    
    input_names = ["input_ids", "attention_mask", "token_type_ids"]
    output_names = ["last_hidden_state", "pooler_output"]
    
    # Check if model expects token_type_ids
    model_inputs = (dummy_input["input_ids"], dummy_input["attention_mask"])
    dynamic_axes = {
        "input_ids": {0: "batch_size", 1: "sequence_length"},
        "attention_mask": {0: "batch_size", 1: "sequence_length"}
    }
    
    if "token_type_ids" in dummy_input:
        model_inputs = (dummy_input["input_ids"], dummy_input["attention_mask"], dummy_input["token_type_ids"])
        dynamic_axes["token_type_ids"] = {0: "batch_size", 1: "sequence_length"}
    else:
        # Remove token_type_ids from input_names if not present
        input_names = ["input_ids", "attention_mask"]

    onnx_path = output_dir / "model.onnx"
    print(f"Exporting ONNX to {onnx_path}...")

    try:
        torch.onnx.export(
            model,
            model_inputs,
            str(onnx_path),
            input_names=input_names,
            output_names=output_names,
            dynamic_axes=dynamic_axes,
            opset_version=14,
            do_constant_folding=True
        )
        print("ONNX export complete.")
    except Exception as e:
        print(f"Export failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()