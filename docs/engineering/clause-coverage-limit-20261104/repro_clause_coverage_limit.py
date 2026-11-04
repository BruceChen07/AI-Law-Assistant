"""复现「条款数超过 160 条时 classic 审计直接失败」的排查脚本。

用途：
1. 读取运行态 app/config.json，打印实际生效的 audit_token_policy；
2. 用 159/160/161/200/571 条合成条款调用 plan_clause_groups，定位容量边界；
3. 用 FakeLLM 走完整 _build_multipass_classic_audit 流程，记录业务流程中断点；
4. 用临时 SQLite 库验证 200 条审计结果的落库与回读完整性；
5. 校验失败异常在 API 层的用户提示映射结果。

运行：app\\.venv\\Scripts\\python.exe .runtime\\validation-temp\\clause-coverage-20261104\\repro_clause_coverage_limit.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from app.core.config import load_config  # noqa: E402
from app.core.token_utils import estimate_text_tokens  # noqa: E402
from app.services.audit_token_policy import (  # noqa: E402
    LegacyAuditCoverageError,
    get_audit_token_policy,
    plan_clause_groups,
)
from app.services.contract_audit import _build_multipass_classic_audit  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent
RESULTS: dict = {}


def make_clause(index: int) -> dict:
    """生成一条结构合规（字段与 build_preview_clauses 一致）的中文合同条款。"""
    return {
        "clause_id": f"c{index}",
        "anchor_id": f"clause-c{index}",
        "clause_path": f"第{index}条",
        "page_no": (index - 1) // 35 + 1,
        "paragraph_no": str(index),
        "clause_text": (
            f"第{index}条 甲方应在收到乙方开具的增值税专用发票后三十（30）个工作日内"
            "支付服务费；逾期支付的，每逾期一日按未付金额的万分之五向乙方支付违约金。"
            "乙方应保证发票税率与合同约定一致，否则应赔偿甲方因此产生的全部损失。"
        ),
    }


class FakeLLM:
    """不发起真实推理的 LLM 桩，用于验证业务编排而非模型质量。"""

    def __init__(self, fail_rounds: tuple | None = None) -> None:
        self.calls = 0
        self.fail_rounds = set(fail_rounds or ())

    def chat_with_profile(self, messages, task_profile, overrides=None):
        self.calls += 1
        if self.calls in self.fail_rounds:
            return "not-json", {"usage": {"completion_tokens": 12}}
        body = {
            "summary": f"round-{self.calls}",
            "risks": [
                {
                    "level": "medium",
                    "issue": f"第{self.calls}轮风险",
                    "suggestion": "补充税务条款",
                    "citation_id": "cid-1",
                    "law_title": "中华人民共和国增值税法",
                    "article_no": "第1条",
                    "evidence": "证据",
                    "confidence": 0.88,
                    "clause_id": f"c{self.calls}",
                }
            ],
        }
        return json.dumps(body, ensure_ascii=False), {
            "usage": {"completion_tokens": 256}
        }


def resolve_policy(cfg: dict) -> dict:
    policy = get_audit_token_policy(cfg)
    capacity = int(policy["max_rounds"]) * int(policy["max_clauses_per_group"])
    return {**policy, "theoretical_clause_capacity": capacity}


def scenario_plan(clause_count: int, policy: dict) -> dict:
    clauses = [make_clause(i) for i in range(1, clause_count + 1)]
    started = time.perf_counter()
    try:
        plan = plan_clause_groups(clauses, policy)
        return {
            "clause_count": clause_count,
            "status": "ok",
            "round_count": plan["round_count"],
            "round_sizes": [len(group) for group in plan["groups"]][:30],
            "first_round_clause_ids": plan["rounds"][0]["clause_ids"] if plan["rounds"] else [],
            "last_round_clause_ids": plan["rounds"][-1]["clause_ids"] if plan["rounds"] else [],
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        }
    except LegacyAuditCoverageError as exc:
        return {
            "clause_count": clause_count,
            "status": "error",
            "error_type": type(exc).__name__,
            "error_code": getattr(exc, "code", ""),
            "error": str(exc),
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        }


def scenario_pipeline(
    clause_count: int,
    base_cfg: dict,
    policy_override: dict | None = None,
    fail_rounds: tuple | None = None,
) -> dict:
    cfg = json.loads(json.dumps(base_cfg))
    cfg["audit_token_policy"] = dict(cfg.get("audit_token_policy") or {})
    if policy_override:
        cfg["audit_token_policy"].update(policy_override)
    cfg["llm_trace_enabled"] = False
    cfg["data_dir"] = str(OUT_DIR / "pipeline-data")
    cfg["memory_root"] = str(OUT_DIR / "pipeline-data" / "memory")
    policy = get_audit_token_policy(cfg)
    clauses = [make_clause(i) for i in range(1, clause_count + 1)]
    text = "\n".join(item["clause_text"] for item in clauses)
    llm = FakeLLM(fail_rounds)
    progress: list = []
    audit_id = f"audit_repro_{clause_count}_{policy['max_rounds']}"

    def _progress(stage, percent, message="", detail=None):
        progress.append(
            {
                "stage": stage,
                "percent": percent,
                "message": message,
                "detail": detail or {},
            }
        )

    started = time.perf_counter()
    try:
        result = _build_multipass_classic_audit(
            cfg=cfg,
            llm=llm,
            text=text,
            lang="zh",
            preview_clauses=clauses,
            evidence_items=[
                {
                    "citation_id": "cid-1",
                    "law_title": "中华人民共和国增值税法",
                    "article_no": "第1条",
                    "content": "证据内容",
                    "final_score": 0.9,
                }
            ],
            retrieval_opts={"audit_mode": "tax"},
            audit_id=audit_id,
            trace_id=f"trace_{audit_id}",
            token_budget={"policy": policy, "reasons": ["clauses_omitted"]},
            progress_cb=_progress,
        )
        meta = result.get("meta") or {}
        return {
            "clause_count": clause_count,
            "policy_override": policy_override or {},
            "status": "ok",
            "llm_calls": llm.calls,
            "execution_path": meta.get("execution_path"),
            "multipass_round_count": meta.get("multipass_round_count"),
            "classic_parse_failed": meta.get("classic_parse_failed"),
            "parse_failed_rounds": meta.get("multipass_parse_failed_rounds"),
            "truncated_rounds": meta.get("multipass_truncated_rounds"),
            "risk_count": len((result.get("audit") or {}).get("risks") or []),
            "progress_events": len(progress),
            "last_progress": progress[-1] if progress else {},
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        }
    except LegacyAuditCoverageError as exc:
        return {
            "clause_count": clause_count,
            "policy_override": policy_override or {},
            "status": "error",
            "error_type": type(exc).__name__,
            "error_code": getattr(exc, "code", ""),
            "error": str(exc),
            "llm_calls": llm.calls,
            "progress_events": len(progress),
            "last_progress": progress[-1] if progress else {},
            "traceback_tail": traceback.format_exc().strip().splitlines()[-3:],
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "clause_count": clause_count,
            "policy_override": policy_override or {},
            "status": "unexpected_error",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "llm_calls": llm.calls,
            "traceback_tail": traceback.format_exc().strip().splitlines()[-5:],
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        }


class _ExactCounter:
    """v2 计划器使用的精确 token 计数器桩（按字符/4 估算）。"""

    exact = True

    def count_messages(self, messages):
        chars = sum(len(str(item.get("content") or "")) for item in messages)
        return 16 + (chars + 3) // 4


def scenario_v2_pipeline(clause_count: int, base_cfg: dict) -> dict:
    cfg = json.loads(json.dumps(base_cfg))
    cfg["audit_optimization"] = {
        "enabled": True,
        "context_tokens": 16384,
        "safety_tokens": 1024,
        "output_tokens": 1536,
        "input_target_tokens": 7000,
        "max_chunks": 64,
        "max_attempts_per_chunk": 2,
        "max_retries_per_job": 3,
    }
    cfg["llm_trace_enabled"] = False
    cfg["data_dir"] = str(OUT_DIR / "pipeline-data")
    clauses = [make_clause(i) for i in range(1, clause_count + 1)]
    text = "\n".join(item["clause_text"] for item in clauses)
    llm = FakeLLM()
    started = time.perf_counter()
    try:
        from app.services.contract_audit import _build_v2_classic_audit

        result = _build_v2_classic_audit(
            cfg=cfg,
            llm=llm,
            text=text,
            lang="zh",
            preview_clauses=clauses,
            evidence_items=[
                {
                    "citation_id": "cid-1",
                    "law_title": "中华人民共和国增值税法",
                    "article_no": "第1条",
                    "content": "证据内容",
                    "final_score": 0.9,
                }
            ],
            retrieval_opts={"audit_mode": "tax"},
            audit_id=f"audit_v2_{clause_count}",
            trace_id=f"trace_v2_{clause_count}",
            token_counter=_ExactCounter(),
        )
        meta = result.get("meta") or {}
        return {
            "clause_count": clause_count,
            "status": "ok",
            "llm_calls": llm.calls,
            "execution_path": meta.get("execution_path"),
            "chunks_total": meta.get("chunks_total"),
            "chunks_completed": meta.get("chunks_completed"),
            "coverage_audited": meta.get("coverage_audited"),
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "clause_count": clause_count,
            "status": "error",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "llm_calls": llm.calls,
            "traceback_tail": traceback.format_exc().strip().splitlines()[-5:],
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        }


def build_audit_payload(clause_count: int) -> dict:
    clauses = [make_clause(i) for i in range(1, clause_count + 1)]
    risks = []
    for item in clauses:
        risks.append(
            {
                "level": "medium",
                "issue": f"{item['clause_path']} 未明确发票类型与税率适用口径",
                "suggestion": "补充增值税专用发票、税率及价税合计口径约定",
                "clause_id": item["clause_id"],
                "clause_path": item["clause_path"],
                "location": {
                    "clause_path": item["clause_path"],
                    "quote": item["clause_text"][:80],
                    "score": 0.86,
                },
            }
        )
    return {
        "summary": f"共 {clause_count} 条条款的模拟审计结果",
        "risks": risks,
        "preview_clauses": clauses,
    }


def scenario_storage(clause_count: int) -> dict:
    from app.core.database import init_db
    from app.services.crud import (
        get_latest_contract_audit_by_document,
        insert_contract_audit,
        insert_document,
    )

    workdir = tempfile.mkdtemp(prefix=f"clause-storage-{clause_count}-", dir=str(OUT_DIR))
    cfg = {"db_path": os.path.join(workdir, "app.db"), "data_dir": workdir}
    init_db(cfg)
    doc_path = os.path.join(workdir, f"contract_{clause_count}.docx")
    with open(doc_path, "wb") as handle:
        handle.write(b"0" * 1024)

    insert_document(
        cfg,
        doc_id=f"doc_{clause_count}",
        filename=os.path.basename(doc_path),
        original_filename=f"{clause_count}_clauses.docx",
        file_path=doc_path,
        file_size=1024,
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        user_id="u_repro",
        title="复现合同",
        status="active",
        category="contract",
    )

    payload = build_audit_payload(clause_count)
    result_json = json.dumps(payload, ensure_ascii=False)
    digest = hashlib.sha256(result_json.encode("utf-8")).hexdigest()
    started = time.perf_counter()
    insert_contract_audit(
        cfg,
        audit_id=f"audit_{clause_count}",
        document_id=f"doc_{clause_count}",
        status="done",
        result_json=result_json,
        model_provider="repro",
        model_name="fake",
        created_at="2026-11-04T00:00:00",
    )
    # 失败态也按生产代码落一条 status=failed 记录
    insert_contract_audit(
        cfg,
        audit_id=f"audit_failed_{clause_count}",
        document_id=f"doc_{clause_count}",
        status="failed",
        result_json=json.dumps(
            {
                "error": "COVERAGE_INCOMPLETE: legacy audit plan exceeds max_rounds; "
                "refusing to append unchecked clauses to the final round",
                "detail": "合同审计失败。请检查【Admin -> 模型配置】中的 LLM 参数后重试。",
            },
            ensure_ascii=False,
        ),
        model_provider="repro",
        model_name="fake",
        created_at="2026-11-04T00:00:01",
    )
    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    row = get_latest_contract_audit_by_document(cfg, f"doc_{clause_count}", status="done")
    stored = str(row.get("result_json") or "")
    conn = sqlite3.connect(cfg["db_path"])
    sqlite_version = conn.execute("select sqlite_version()").fetchone()[0]
    try:
        length_limit = conn.getlimit(sqlite3.SQLITE_LIMIT_LENGTH)
    except Exception:  # noqa: BLE001
        length_limit = None
    conn.close()
    return {
        "clause_count": clause_count,
        "status": row.get("status"),
        "payload_bytes": len(result_json.encode("utf-8")),
        "roundtrip_sha256_match": hashlib.sha256(stored.encode("utf-8")).hexdigest() == digest,
        "stored_bytes": len(stored.encode("utf-8")),
        "stored_risk_count": len(json.loads(stored).get("risks") or []),
        "sqlite_version": sqlite_version,
        "sqlite_length_limit": length_limit,
        "db_file_bytes": os.path.getsize(cfg["db_path"]),
        "write_duration_ms": duration_ms,
        "db_path": cfg["db_path"],
    }


def scenario_error_message() -> dict:
    try:
        from app.api.routers.contracts import _build_audit_error_detail
    except Exception as exc:  # noqa: BLE001
        return {"status": "import_failed", "error": str(exc)}
    exc = LegacyAuditCoverageError(
        "COVERAGE_INCOMPLETE: legacy audit plan exceeds max_rounds; "
        "refusing to append unchecked clauses to the final round"
    )
    return {
        "status": "ok",
        "zh_message": _build_audit_error_detail(exc, "zh"),
        "en_message": _build_audit_error_detail(exc, "en"),
        "error_code_matched_by_mapping": "COVERAGE_INCOMPLETE" in _build_audit_error_detail(exc, "zh"),
    }


def scenario_real_document() -> dict:
    """尽力复算线上失败文档的条款数与 token 体量（文件不存在则跳过）。"""
    path = r"D:\Workspace\AI-Law-Assistant\data\uploads\cache\73ec9496-1084-42d6-8dea-8e3bd1c9cbd3.docx"
    if not os.path.exists(path):
        return {"status": "skipped", "reason": "document not found", "path": path}
    from app.core.utils import extract_text_with_config
    from app.services.contract_audit import build_preview_clauses

    started = time.perf_counter()
    cfg = json.loads(json.dumps(RUNTIME_CFG))
    text, meta = extract_text_with_config(cfg, path)
    clauses = build_preview_clauses(text)
    total_tokens = sum(
        estimate_text_tokens(f"{item.get('clause_path') or ''}\n{item.get('clause_text') or ''}")
        for item in clauses
    )
    return {
        "status": "ok",
        "text_chars": len(text),
        "clause_count": len(clauses),
        "total_clause_tokens": total_tokens,
        "avg_clause_tokens": round(total_tokens / max(1, len(clauses)), 1),
        "extract_duration_ms": round((time.perf_counter() - started) * 1000, 2),
        "meta_keys": sorted(meta.keys()),
    }


def main() -> None:
    global RUNTIME_CFG
    RUNTIME_CFG = load_config()
    policy = resolve_policy(RUNTIME_CFG)
    RESULTS["runtime_config_path"] = os.path.join(str(ROOT), "app", "config.json")
    RESULTS["runtime_policy"] = policy
    RESULTS["runtime_has_audit_token_policy"] = isinstance(
        RUNTIME_CFG.get("audit_token_policy"), dict
    )
    RESULTS["runtime_has_audit_optimization"] = isinstance(
        RUNTIME_CFG.get("audit_optimization"), dict
    )
    RESULTS["runtime_v2_enabled"] = bool(
        (RUNTIME_CFG.get("audit_optimization") or {}).get("enabled", False)
    )
    RESULTS["avg_synthetic_clause_tokens"] = round(
        sum(
            estimate_text_tokens(
                f"{item['clause_path']}\n{item['clause_text']}"
            )
            for item in (make_clause(i) for i in range(1, 201))
        )
        / 200,
        1,
    )
    RESULTS["plan_boundaries"] = [
        scenario_plan(count, policy) for count in (159, 160, 161, 200, 571)
    ]
    RESULTS["plan_with_25_rounds"] = scenario_plan(
        200, {**policy, "max_rounds": 25}
    )
    RESULTS["pipeline_160_runtime_policy"] = scenario_pipeline(160, RUNTIME_CFG)
    RESULTS["pipeline_200_runtime_policy"] = scenario_pipeline(200, RUNTIME_CFG)
    RESULTS["pipeline_200_max_rounds_25"] = scenario_pipeline(
        200, RUNTIME_CFG, {"max_rounds": 25}
    )
    RESULTS["pipeline_571_runtime_policy"] = scenario_pipeline(571, RUNTIME_CFG)
    RESULTS["pipeline_160_parse_failed_round"] = scenario_pipeline(
        160, RUNTIME_CFG, None, (3, 4)
    )
    no_retry_cfg = json.loads(json.dumps(RUNTIME_CFG))
    no_retry_cfg["audit_reliability_policy"] = {"enabled": False}
    RESULTS["pipeline_160_parse_failed_no_retry"] = scenario_pipeline(
        160, no_retry_cfg, None, (3, 4)
    )
    RESULTS["storage_200"] = scenario_storage(200)
    RESULTS["storage_571"] = scenario_storage(571)
    RESULTS["v2_pipeline_200"] = scenario_v2_pipeline(200, RUNTIME_CFG)
    RESULTS["v2_pipeline_571"] = scenario_v2_pipeline(571, RUNTIME_CFG)
    RESULTS["api_error_message"] = scenario_error_message()
    try:
        RESULTS["real_document"] = scenario_real_document()
    except Exception as exc:  # noqa: BLE001
        RESULTS["real_document"] = {
            "status": "error",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback_tail": traceback.format_exc().strip().splitlines()[-6:],
        }

    output = json.dumps(RESULTS, ensure_ascii=False, indent=2)
    print(output)
    with open(OUT_DIR / "repro_results.json", "w", encoding="utf-8") as handle:
        handle.write(output)


if __name__ == "__main__":
    main()
