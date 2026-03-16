import argparse
import json
import os
from app.core.config import get_config
from app.core.ocr import detect_dependencies, benchmark_engines


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=str, default="")
    parser.add_argument("--output", type=str, default="")
    args = parser.parse_args()

    cfg = get_config()
    report = {"dependencies": detect_dependencies()}
    if args.pdf and os.path.exists(args.pdf):
        report["benchmark"] = benchmark_engines(cfg, args.pdf)

    if args.output:
        os.makedirs(os.path.dirname(args.output), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
