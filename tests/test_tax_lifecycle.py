import os
import uuid
import logging
from datetime import datetime, timedelta
from app.core.database import init_db, get_conn
from app.services.crud import (
    create_tax_contract_document,
    list_tax_cleanup_jobs,
    list_tax_archive_records,
)
from app.services.tax_lifecycle import run_tax_cleanup, retry_tax_cleanup


def test_tax_cleanup_archive_and_retry(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    db_path = tmp_path / "test.db"
    files_dir = tmp_path / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    cfg = {"db_path": str(db_path), "files_dir": str(files_dir)}
    init_db(cfg)

    contract_id = str(uuid.uuid4())
    create_tax_contract_document(
        cfg=cfg,
        document_id=contract_id,
        original_filename="contract.docx",
        file_path=str(tmp_path / "contract.docx"),
        file_type="docx",
        file_size=100,
        uploaded_by="u1",
        parse_status="done",
        ocr_used=0,
    )
    old_time = (datetime.utcnow() - timedelta(days=40)).isoformat()
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        "UPDATE contract_document SET created_at=?, updated_at=? WHERE id=?",
        (old_time, old_time, contract_id),
    )
    conn.commit()
    conn.close()

    report_dir = files_dir / "tax_audit_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    stale_report = report_dir / "tax_audit_report_old.json"
    stale_report.write_text("{}", encoding="utf-8")
    stale_ts = (datetime.utcnow() - timedelta(days=40)).timestamp()
    os.utime(stale_report, (stale_ts, stale_ts))

    result = run_tax_cleanup(cfg, operator_id="u1", retention_days=30)
    assert result["status"] == "success"
    assert result["archived_contracts"] == 1
    assert result["deleted_files"] == 1
    assert not stale_report.exists()

    jobs = list_tax_cleanup_jobs(cfg, limit=10)
    assert len(jobs) >= 1
    assert jobs[0]["status"] == "success"
    records = list_tax_archive_records(cfg, limit=10)
    assert len(records) == 1
    assert records[0]["contract_document_id"] == contract_id
    assert os.path.exists(records[0]["archive_path"])

    retry = retry_tax_cleanup(cfg, jobs[0]["id"], operator_id="u2")
    assert retry["status"] == "success"
