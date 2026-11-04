"""启动依赖自检 CLI。

用法::

    python bin/doctor.py            # 人类可读报告，缺少关键依赖时退出码 1
    python bin/doctor.py --json     # 机器可读报告
    python bin/doctor.py --strict   # 不通过时以非零码退出（便于部署脚本 gate）

检查内容与 ``app/core/dependency_preflight.py`` 一致：Python 依赖、MinerU
命令行及其所在解释器的 pipeline 依赖、本地模型文件、向量模型、LLM 端点。
"""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check startup dependencies before running the service.")
    parser.add_argument("--json", action="store_true",
                        help="Print the raw report as JSON.")
    parser.add_argument("--strict", action="store_true",
                        help="Exit with code 1 when any critical check fails (default).")
    args = parser.parse_args()

    from app.core.config import get_config
    from app.core.dependency_preflight import (
        _ensure_console_encoding,
        collect_report,
        format_report,
    )

    _ensure_console_encoding()
    cfg = get_config()
    report = collect_report(cfg)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_report(report))
        print("")
        print("提示：修复后重新运行本命令，确认全部 OK 再启动服务。")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
