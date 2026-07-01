import argparse
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCAN_TARGETS = [
    ROOT / "app",
    ROOT / "bin",
    ROOT / "tests",
]
SCAN_SUFFIXES = {".py", ".json", ".txt", ".md", ".yml", ".yaml"}
EXCLUDED_FILES = {
    ROOT / "bin" / "validate_offline_compliance.py",
}
FORBIDDEN_PATTERNS = {
    "public_cloud_domain": re.compile(
        r"api\.openai\.com|dashscope\.aliyuncs\.com|openrouter\.ai|api\.anthropic\.com",
        re.IGNORECASE,
    ),
    "public_model_hub": re.compile(
        r"\bmodelscope\b|\bhuggingface\b|snapshot_download",
        re.IGNORECASE,
    ),
    "cloud_fallback_logic": re.compile(
        r"cloud_fallback|force_cloud|high_risk_force_cloud|tax_match_cloud_review_labels|memory_clause_force_cloud|memory_flush_force_cloud",
        re.IGNORECASE,
    ),
    "public_cloud_env": re.compile(
        r"OPENAI_API_KEY|DASHSCOPE_API_KEY|DASHSCOPE_BASE_URL",
        re.IGNORECASE,
    ),
}


def iter_files():
    for target in SCAN_TARGETS:
        if not target.exists():
            continue
        if target.is_file():
            yield target
            continue
        for file_path in target.rglob("*"):
            if (
                file_path.is_file()
                and file_path.suffix.lower() in SCAN_SUFFIXES
                and file_path not in EXCLUDED_FILES
            ):
                yield file_path


def scan_repo():
    issues = []
    for file_path in iter_files():
        try:
            text = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        for rule_name, pattern in FORBIDDEN_PATTERNS.items():
            for match in pattern.finditer(text):
                line_no = text.count("\n", 0, match.start()) + 1
                issues.append(
                    {
                        "rule": rule_name,
                        "file": str(file_path.relative_to(ROOT)),
                        "line": line_no,
                        "match": match.group(0),
                    }
                )
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate that the enterprise offline branch does not contain public cloud interaction logic."
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    args = parser.parse_args()

    issues = scan_repo()
    report = {
        "root": str(ROOT),
        "scanned_targets": [str(path.relative_to(ROOT)) for path in SCAN_TARGETS if path.exists()],
        "issue_count": len(issues),
        "issues": issues,
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print()
        print("PASS" if not issues else "FAIL")
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
