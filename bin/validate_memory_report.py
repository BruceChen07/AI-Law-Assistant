import argparse
import json

from app.memory_system.validator import validate_report_citations


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--catalog", required=True)
    args = parser.parse_args()

    with open(args.report, "r", encoding="utf-8") as f:
        report = json.load(f)
    with open(args.catalog, "r", encoding="utf-8") as f:
        catalog = json.load(f)

    result = validate_report_citations(report, catalog)
    print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))
    raise SystemExit(0 if result.ok else 1)


if __name__ == "__main__":
    main()
