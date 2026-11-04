from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def _write(name: str, content: str) -> dict[str, object]:
    path = ROOT / name
    path.write_text(content, encoding="utf-8", newline="\n")
    payload = content.encode("utf-8")
    return {
        "path": name,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
    }


def main() -> None:
    fragmented = "\n".join(
        f"第{index}项 税务约定{index}。"
        for index in range(1, 798)
    ) + "\n"
    oversized = "第一条 超长设备销售与开票约定\n" + (
        "设备销售、验收、付款及增值税专用发票义务应保持一致。\n" * 1200
    )
    tail_risk = "\n".join(
        [
            *(f"第{index}条 一般履约约定。" for index in range(1, 80)),
            "第80条 税务风险：合同约定销售设备但按6%开具服务发票。",
        ]
    ) + "\n"

    samples = [
        {
            "sample_id": "synthetic-fragmented-797",
            "type": "txt",
            "language": "zh",
            "pages": 1,
            "expected_source_spans": 797,
            "expected_risks": [],
            "authorization_ref": "synthetic-generated",
            **_write("fragmented_797.txt", fragmented),
        },
        {
            "sample_id": "synthetic-oversized-clause",
            "type": "txt",
            "language": "zh",
            "pages": 1,
            "expected_source_spans": 1,
            "expected_risks": [],
            "authorization_ref": "synthetic-generated",
            **_write("oversized_clause.txt", oversized),
        },
        {
            "sample_id": "synthetic-tail-tax-risk",
            "type": "txt",
            "language": "zh",
            "pages": 1,
            "expected_source_spans": 80,
            "expected_risks": [
                {
                    "risk_category": "vat_rate_mismatch",
                    "marker": "按6%开具服务发票",
                }
            ],
            "authorization_ref": "synthetic-generated",
            **_write("tail_tax_risk.txt", tail_risk),
        },
    ]
    manifest = {
        "schema_version": 1,
        "generator": Path(__file__).name,
        "samples": samples,
    }
    (ROOT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    main()
