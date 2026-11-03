from concurrent.futures import ThreadPoolExecutor

from app.services.contract_audit_stage_log import (
    export_contract_audit_stage_logs,
    init_contract_audit_log_db,
    query_contract_audit_stage_logs,
    register_contract_audit_log_context,
    write_contract_audit_stage_log,
)


def _cfg(tmp_path):
    data_dir = tmp_path / "data"
    return {
        "data_dir": str(data_dir),
        "contract_audit_log_db_path": str(data_dir / "audit_logs" / "contract_audit_stage_logs.db"),
        "contract_audit_log_export_dir": str(data_dir / "exports" / "contract_audit_logs"),
    }


def test_contract_audit_stage_log_write_is_idempotent(tmp_path):
    cfg = _cfg(tmp_path)
    init_contract_audit_log_db(cfg)
    register_contract_audit_log_context(
        "audit-1",
        document_id="doc-1",
        contract_name="Master Service Agreement",
        operator_id="user-1",
        ip_address="127.0.0.1",
        source_endpoint="/contracts/audit",
    )

    first = write_contract_audit_stage_log(
        cfg,
        audit_id="audit-1",
        stage_code="retrieval",
        stage_status="running",
        stage_result="running",
        stage_message="retrieving evidence",
        occurred_at_utc="2026-10-16T06:00:00.123Z",
        payload={"progress": 40},
        event_key="event-retrieval-40",
    )
    second = write_contract_audit_stage_log(
        cfg,
        audit_id="audit-1",
        stage_code="retrieval",
        stage_status="running",
        stage_result="running",
        stage_message="retrieving evidence",
        occurred_at_utc="2026-10-16T06:00:00.123Z",
        payload={"progress": 40},
        event_key="event-retrieval-40",
    )

    result = query_contract_audit_stage_logs(cfg, operator_id="user-1", audit_id="audit-1")
    assert result["total"] == 1
    assert first["id"] == second["id"]
    assert result["items"][0]["contract_name"] == "Master Service Agreement"
    assert result["items"][0]["occurred_at_utc"].endswith("Z")


def test_contract_audit_stage_log_query_and_export(tmp_path):
    cfg = _cfg(tmp_path)
    init_contract_audit_log_db(cfg)
    register_contract_audit_log_context(
        "audit-2",
        document_id="doc-2",
        contract_name="Procurement Contract",
        operator_id="user-2",
        ip_address="10.0.0.2",
        source_endpoint="/contracts/audit",
    )
    write_contract_audit_stage_log(
        cfg,
        audit_id="audit-2",
        stage_code="retrieval",
        stage_status="running",
        stage_result="running",
        stage_message="retrieving evidence",
        occurred_at_utc="2026-10-16T06:10:00.100Z",
        payload={"progress": 40},
        event_key="audit-2-retrieval",
    )
    write_contract_audit_stage_log(
        cfg,
        audit_id="audit-2",
        stage_code="done",
        stage_status="done",
        stage_result="success",
        stage_message="completed",
        audit_opinion="2 high-risk clauses found",
        occurred_at_utc="2026-10-16T06:10:03.400Z",
        payload={"progress": 100, "risk_summary": {"high": 2, "medium": 0, "low": 1}},
        event_key="audit-2-done",
    )

    filtered = query_contract_audit_stage_logs(
        cfg,
        operator_id="user-2",
        contract_name="procurement",
        started_at_utc="2026-10-16T06:09:00.000Z",
        ended_at_utc="2026-10-16T06:11:00.000Z",
        stage_filters=["done"],
    )
    assert filtered["total"] == 1
    assert filtered["items"][0]["audit_opinion"] == "2 high-risk clauses found"

    exported = export_contract_audit_stage_logs(cfg, records=filtered["items"], export_format="jsonl")
    content = open(exported["path"], "r", encoding="utf-8").read()
    assert "Procurement Contract" in content
    assert "\"stage_code\": \"done\"" in content


def test_contract_audit_stage_log_concurrent_write_stability(tmp_path):
    cfg = _cfg(tmp_path)
    init_contract_audit_log_db(cfg)
    register_contract_audit_log_context(
        "audit-3",
        document_id="doc-3",
        contract_name="Concurrent Contract",
        operator_id="user-3",
        ip_address="127.0.0.1",
        source_endpoint="/contracts/audit",
    )

    def _write(idx: int):
        return write_contract_audit_stage_log(
            cfg,
            audit_id="audit-3",
            stage_code="auditing",
            stage_status="running",
            stage_result="running",
            stage_message=f"auditing batch {idx % 8}",
            occurred_at_utc=f"2026-10-16T06:20:0{idx % 8}.000Z",
            payload={"progress": 70 + (idx % 8)},
            event_key=f"concurrent-{idx % 8}",
        )

    with ThreadPoolExecutor(max_workers=12) as executor:
        rows = list(executor.map(_write, range(64)))

    assert len(rows) == 64
    result = query_contract_audit_stage_logs(cfg, operator_id="user-3", audit_id="audit-3", limit=50)
    assert result["total"] == 8
    assert all(item["contract_name"] == "Concurrent Contract" for item in result["items"])
