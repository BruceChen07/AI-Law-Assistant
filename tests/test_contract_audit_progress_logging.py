import logging

from app.api.routers.contracts import _set_audit_progress


def test_set_audit_progress_logs_stage_changes_once(caplog):
    audit_id = "audit-progress-log"

    with caplog.at_level(logging.INFO, logger="law_assistant"):
        _set_audit_progress(audit_id, "running", 40, "retrieval", "retrieving evidence")
        _set_audit_progress(audit_id, "running", 40, "retrieval", "retrieving evidence")
        _set_audit_progress(audit_id, "running", 55, "retrieval_done", "evidence ready")

    progress_records = [
        record for record in caplog.records
        if "contract_audit_progress" in record.getMessage()
    ]
    assert len(progress_records) == 2
    assert "task=evidence_retrieving" in progress_records[0].getMessage()
    assert "message=retrieving evidence" in progress_records[0].getMessage()
    assert "task=evidence_ready" in progress_records[1].getMessage()
