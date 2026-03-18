import os
import json
import logging
from datetime import datetime, timedelta
from app.services.crud import (
    create_tax_cleanup_job,
    finish_tax_cleanup_job,
    list_contract_documents_for_archive,
    list_tax_audit_issues_by_contract,
    list_audit_trace_by_contract,
    create_tax_archive_record,
    get_tax_cleanup_job,
)
from app.services.tax_report import build_tax_audit_report

logger = logging.getLogger("law_assistant")


def _safe_iso_to_dt(value: str):
    try:
        return datetime.fromisoformat(str(value or ""))
    except Exception:
        return None


def _is_older_than(dt_value, cutoff: datetime) -> bool:
    if dt_value is None:
        return False
    return dt_value <= cutoff


def _collect_old_report_files(files_dir: str, cutoff: datetime) -> list[str]:
    base = os.path.join(files_dir, "tax_audit_reports")
    if not os.path.exists(base):
        return []
    targets = []
    for name in os.listdir(base):
        full = os.path.join(base, name)
        if not os.path.isfile(full):
            continue
        mtime = datetime.fromtimestamp(os.path.getmtime(full))
        if mtime <= cutoff:
            targets.append(full)
    return targets


def run_tax_cleanup(cfg, operator_id: str = "", retention_days: int = 30, retry_job_id: str = "") -> dict:
    days = max(1, int(retention_days))
    cutoff = datetime.utcnow() - timedelta(days=days)
    job_id = create_tax_cleanup_job(cfg, days, created_by=operator_id)
    logger.info(
        "tax_cleanup_start job_id=%s operator=%s retention_days=%s retry_from_job_id=%s cutoff=%s",
        job_id,
        operator_id,
        days,
        retry_job_id,
        cutoff.isoformat(),
    )
    archived_contracts = 0
    deleted_files = 0
    archived_contract_ids = []
    deleted_file_paths = []
    try:
        contracts = list_contract_documents_for_archive(cfg)
        logger.info("tax_cleanup_candidates job_id=%s candidates=%s", job_id, len(contracts))
        archive_root = os.path.join(cfg["files_dir"], "tax_audit_archive", cutoff.strftime("%Y%m"))
        os.makedirs(archive_root, exist_ok=True)
        for contract in contracts:
            updated_at = _safe_iso_to_dt(contract.get("updated_at"))
            created_at = _safe_iso_to_dt(contract.get("created_at"))
            benchmark = updated_at or created_at
            if not _is_older_than(benchmark, cutoff):
                continue
            contract_id = str(contract.get("id") or "")
            issues = list_tax_audit_issues_by_contract(cfg, contract_id)
            traces = list_audit_trace_by_contract(cfg, contract_id, limit=2000)
            report = build_tax_audit_report(cfg, contract_id)
            archive_payload = {
                "contract_id": contract_id,
                "contract_filename": contract.get("original_filename"),
                "archived_at": datetime.utcnow().isoformat(),
                "retention_days": days,
                "issue_count": len(issues),
                "trace_count": len(traces),
                "report": report,
            }
            archive_name = f"tax_audit_archive_{contract_id}.json"
            archive_path = os.path.join(archive_root, archive_name)
            with open(archive_path, "w", encoding="utf-8") as f:
                json.dump(archive_payload, f, ensure_ascii=False, indent=2)
            create_tax_archive_record(
                cfg,
                contract_document_id=contract_id,
                archive_path=archive_path,
                archived_by=operator_id,
                source_job_id=job_id,
            )
            archived_contracts += 1
            archived_contract_ids.append(contract_id)
            logger.info(
                "tax_cleanup_archived_contract job_id=%s contract_id=%s issues=%s traces=%s archive_path=%s",
                job_id,
                contract_id,
                len(issues),
                len(traces),
                archive_path,
            )

        old_report_files = _collect_old_report_files(cfg["files_dir"], cutoff)
        for path in old_report_files:
            try:
                os.remove(path)
                deleted_files += 1
                deleted_file_paths.append(path)
            except Exception:
                logger.exception("tax_cleanup_delete_report_failed job_id=%s file=%s", job_id, path)
                continue

        details = {
            "retry_from_job_id": retry_job_id,
            "cutoff": cutoff.isoformat(),
            "archived_contract_ids": archived_contract_ids,
            "deleted_file_paths": deleted_file_paths,
        }
        finish_tax_cleanup_job(
            cfg,
            job_id=job_id,
            status="success",
            archived_contracts=archived_contracts,
            deleted_files=deleted_files,
            details_json=json.dumps(details, ensure_ascii=False),
            error="",
        )
        result = {
            "job_id": job_id,
            "status": "success",
            "retention_days": days,
            "archived_contracts": archived_contracts,
            "deleted_files": deleted_files,
            "cutoff": cutoff.isoformat(),
        }
        logger.info(
            "tax_cleanup_done job_id=%s archived_contracts=%s deleted_files=%s",
            job_id,
            archived_contracts,
            deleted_files,
        )
        return result
    except Exception as e:
        finish_tax_cleanup_job(
            cfg,
            job_id=job_id,
            status="failed",
            archived_contracts=archived_contracts,
            deleted_files=deleted_files,
            details_json=json.dumps(
                {
                    "retry_from_job_id": retry_job_id,
                    "cutoff": cutoff.isoformat(),
                    "archived_contract_ids": archived_contract_ids,
                    "deleted_file_paths": deleted_file_paths,
                },
                ensure_ascii=False,
            ),
            error=str(e),
        )
        logger.exception("tax_cleanup_failed job_id=%s error=%s", job_id, str(e))
        raise


def retry_tax_cleanup(cfg, job_id: str, operator_id: str = "") -> dict:
    source = get_tax_cleanup_job(cfg, job_id)
    if not source:
        raise ValueError("cleanup job not found")
    days = int(source.get("retention_days") or 30)
    logger.info(
        "tax_cleanup_retry_start source_job_id=%s operator=%s retention_days=%s",
        job_id,
        operator_id,
        days,
    )
    return run_tax_cleanup(
        cfg,
        operator_id=operator_id,
        retention_days=days,
        retry_job_id=job_id,
    )
