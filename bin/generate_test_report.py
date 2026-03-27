import json
import os
import subprocess
import sys
import time


def main():
    output = sys.argv[1] if len(sys.argv) > 1 else "reports/test_report.json"
    os.makedirs(os.path.dirname(output), exist_ok=True)
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        elapsed = int((time.perf_counter() - start) * 1000)
        report = {
            "exit_code": proc.returncode,
            "elapsed_ms": elapsed,
            "stdout": proc.stdout,
            "stderr": proc.stderr
        }
    except Exception as e:
        elapsed = int((time.perf_counter() - start) * 1000)
        report = {
            "exit_code": 1,
            "elapsed_ms": elapsed,
            "stdout": "",
            "stderr": str(e)
        }
    with open(output, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
