import uuid
import logging
from datetime import datetime
from app.core.database import get_conn

logger = logging.getLogger("law_assistant")


def upsert_job(cfg, job_id, status, error=None, finished_at=None):
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("UPDATE ingest_job SET status=?, error=?, finished_at=? WHERE id=?",
                (status, error, finished_at, job_id))
    conn.commit()
    conn.close()


def insert_job(cfg, job_id):
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("INSERT INTO ingest_job(id,status,created_at) VALUES(?,?,?)",
                (job_id, "running", datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()


def create_regulation(cfg, title, doc_no, issuer, reg_type, status):
    rid = str(uuid.uuid4())
    version_group_id = str(uuid.uuid4())
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO regulation(id,title,doc_no,issuer,reg_type,status,version_group_id,created_at)
        VALUES(?,?,?,?,?,?,?,?)
    """, (rid, title, doc_no, issuer, reg_type, status, version_group_id, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()
    return rid


def create_version(cfg, regulation_id, effective_date, expiry_date, region, industry, source_file):
    vid = str(uuid.uuid4())
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO regulation_version(id,regulation_id,effective_date,expiry_date,is_current,region,industry,source_file,created_at)
        VALUES(?,?,?,?,?,?,?,?,?)
    """, (vid, regulation_id, effective_date, expiry_date, 1, region, industry, source_file, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()
    return vid


def insert_articles(cfg, version_id, items, job_id=None, language: str = "zh", embedder=None):
    conn = get_conn(cfg)
    cur = conn.cursor()
    total = len(items)
    for idx, (article_no, content) in enumerate(items, 1):
        aid = str(uuid.uuid4())
        cur.execute("""
            INSERT INTO article(id,regulation_version_id,article_no,level_path,content,keywords)
            VALUES(?,?,?,?,?,?)
        """, (aid, version_id, article_no, article_no, content, ""))
        cur.execute("""
            INSERT INTO article_fts(content,article_id,regulation_version_id)
            VALUES(?,?,?)
        """, (content, aid, version_id))
        prof = embedder.get_embed_profile(language) if embedder else None
        v = embedder.compute_embedding(
            content, lang=language) if embedder else None
        if v is not None:
            cur.execute("""
                INSERT OR REPLACE INTO article_embedding(article_id, lang, model_id, dim, vec)
                VALUES(?,?,?,?,?)
            """, (aid, (prof or {}).get("lang", language), (prof or {}).get("model_id", "unknown"), int(v.shape[0]), v.tobytes()))
        if job_id and total > 0 and (idx % max(1, total // 10) == 0 or idx == total):
            upsert_job(cfg, job_id, "running")
    conn.commit()
    conn.close()


def insert_contract_audit(cfg, audit_id, document_id, status, result_json, model_provider, model_name, created_at):
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO contract_audit(id, document_id, status, result_json, model_provider, model_name, created_at)
        VALUES(?,?,?,?,?,?,?)
    """, (audit_id, document_id, status, result_json, model_provider, model_name, created_at))
    conn.commit()
    conn.close()


def insert_document(cfg, doc_id, filename, original_filename, file_path, file_size, mime_type, user_id,
                    title=None, category=None, status="active"):
    conn = get_conn(cfg)
    cur = conn.cursor()

    # Check if document with same original_filename already exists
    cur.execute(
        "SELECT id FROM documents WHERE original_filename = ? AND status = 'active'", (original_filename,))
    row = cur.fetchone()

    if row:
        # Update existing document
        existing_id = row[0]
        cur.execute("""
            UPDATE documents 
            SET filename=?, file_path=?, file_size=?, mime_type=?, user_id=?, 
                title=?, category=?, created_at=?
            WHERE id=?
        """, (filename, file_path, int(file_size), mime_type, user_id,
              title, category, datetime.utcnow().isoformat(), existing_id))
        doc_id = existing_id
    else:
        cur.execute(
            """
            INSERT INTO documents (id, filename, original_filename, file_path, file_size, mime_type, user_id, title, category, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc_id,
                filename,
                original_filename,
                file_path,
                int(file_size),
                mime_type,
                user_id,
                title,
                category,
                status,
                datetime.utcnow().isoformat(),
            ),
        )
    conn.commit()
    conn.close()
    return doc_id


def get_document_by_id_for_user(cfg, document_id, user_id):
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, filename, original_filename, file_path, file_size, mime_type, user_id, title, category, status, created_at, updated_at
        FROM documents
        WHERE id=? AND user_id=? AND status='active'
        """,
        (document_id, user_id),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def backfill_legal_document_categories(cfg):
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE documents
        SET category = 'legal'
        WHERE status = 'active'
          AND (category IS NULL OR TRIM(category) = '')
          AND EXISTS (
              SELECT 1
              FROM regulation_version v
              WHERE v.source_file = documents.file_path
          )
        """
    )
    updated = cur.rowcount
    conn.commit()
    conn.close()
    return updated


def create_tax_regulation_document(
    cfg,
    document_id,
    original_filename,
    file_path,
    file_type,
    file_size,
    uploaded_by,
    checksum=None,
    parse_status="pending",
):
    now = datetime.utcnow().isoformat()
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO regulation_document(
            id, original_filename, file_path, file_type, file_size, checksum,
            parse_status, uploaded_by, created_at, updated_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        (
            document_id,
            original_filename,
            file_path,
            file_type,
            int(file_size),
            checksum,
            parse_status,
            uploaded_by,
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()


def create_tax_contract_document(
    cfg,
    document_id,
    original_filename,
    file_path,
    file_type,
    file_size,
    uploaded_by,
    parse_status="pending",
    ocr_used=0,
):
    now = datetime.utcnow().isoformat()
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO contract_document(
            id, original_filename, file_path, file_type, file_size,
            parse_status, ocr_used, uploaded_by, created_at, updated_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        (
            document_id,
            original_filename,
            file_path,
            file_type,
            int(file_size),
            parse_status,
            int(ocr_used),
            uploaded_by,
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()


def list_tax_audit_issues_by_contract(cfg, contract_document_id):
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            i.id,
            i.contract_document_id,
            i.clause_id,
            i.rule_id,
            i.risk_level,
            i.issue_text,
            i.suggestion,
            i.reviewer_status,
            i.reviewer_note,
            i.created_at,
            i.updated_at
        FROM audit_issue i
        WHERE i.contract_document_id=?
        ORDER BY
            CASE i.risk_level
                WHEN 'high' THEN 1
                WHEN 'medium' THEN 2
                WHEN 'low' THEN 3
                ELSE 4
            END,
            i.created_at DESC
        """,
        (contract_document_id,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_tax_regulation_document(cfg, document_id):
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("SELECT * FROM regulation_document WHERE id=?", (document_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def update_tax_regulation_document_status(cfg, document_id, parse_status):
    now = datetime.utcnow().isoformat()
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        "UPDATE regulation_document SET parse_status=?, updated_at=? WHERE id=?",
        (parse_status, now, document_id),
    )
    conn.commit()
    conn.close()


def replace_tax_rules_for_document(cfg, document_id, rules, created_by):
    now = datetime.utcnow().isoformat()
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM tax_rule WHERE regulation_document_id=?", (document_id,))
    for rule in rules:
        cur.execute(
            """
            INSERT INTO tax_rule(
                id, regulation_document_id, law_title, article_no, rule_type,
                trigger_condition, required_action, prohibited_action, numeric_constraints,
                deadline_constraints, region, industry, effective_date, expiry_date,
                source_page, source_paragraph, source_text, created_by, created_at, updated_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                str(uuid.uuid4()),
                document_id,
                rule.get("law_title", ""),
                rule.get("article_no", ""),
                rule.get("rule_type", ""),
                rule.get("trigger_condition", ""),
                rule.get("required_action", ""),
                rule.get("prohibited_action", ""),
                rule.get("numeric_constraints", ""),
                rule.get("deadline_constraints", ""),
                rule.get("region", ""),
                rule.get("industry", ""),
                rule.get("effective_date", ""),
                rule.get("expiry_date", ""),
                int(rule.get("source_page") or 0),
                str(rule.get("source_paragraph", "")),
                rule.get("source_text", ""),
                created_by,
                now,
                now,
            ),
        )
    conn.commit()
    conn.close()


def count_tax_rules_by_document(cfg, document_id):
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(1) AS c FROM tax_rule WHERE regulation_document_id=?", (document_id,))
    row = cur.fetchone()
    conn.close()
    return int(row["c"] if row else 0)


def get_tax_contract_document(cfg, contract_id):
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("SELECT * FROM contract_document WHERE id=?", (contract_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def update_tax_contract_document_status(cfg, contract_id, parse_status, ocr_used=None):
    now = datetime.utcnow().isoformat()
    conn = get_conn(cfg)
    cur = conn.cursor()
    if ocr_used is None:
        cur.execute(
            "UPDATE contract_document SET parse_status=?, updated_at=? WHERE id=?",
            (parse_status, now, contract_id),
        )
    else:
        cur.execute(
            "UPDATE contract_document SET parse_status=?, ocr_used=?, updated_at=? WHERE id=?",
            (parse_status, int(ocr_used), now, contract_id),
        )
    conn.commit()
    conn.close()


def replace_contract_clauses(cfg, contract_id, clauses, created_by):
    now = datetime.utcnow().isoformat()
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM contract_clause WHERE contract_document_id=?", (contract_id,))
    for clause in clauses:
        cur.execute(
            """
            INSERT INTO contract_clause(
                id, contract_document_id, clause_path, page_no, paragraph_no,
                clause_text, entities_json, created_by, created_at, updated_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                str(uuid.uuid4()),
                contract_id,
                clause.get("clause_path", ""),
                int(clause.get("page_no") or 0),
                str(clause.get("paragraph_no", "")),
                clause.get("clause_text", ""),
                clause.get("entities_json", "{}"),
                created_by,
                now,
                now,
            ),
        )
    conn.commit()
    conn.close()


def count_contract_clauses(cfg, contract_id):
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(1) AS c FROM contract_clause WHERE contract_document_id=?", (contract_id,))
    row = cur.fetchone()
    conn.close()
    return int(row["c"] if row else 0)


def list_contract_clauses(cfg, contract_id, limit=200):
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, contract_document_id, clause_path, page_no, paragraph_no, clause_text, entities_json, created_at, updated_at
        FROM contract_clause
        WHERE contract_document_id=?
        ORDER BY page_no ASC, paragraph_no ASC
        LIMIT ?
        """,
        (contract_id, int(limit)),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def list_tax_rules(cfg, regulation_document_id=None, limit=2000):
    conn = get_conn(cfg)
    cur = conn.cursor()
    if regulation_document_id:
        cur.execute(
            """
            SELECT id, regulation_document_id, law_title, article_no, rule_type,
                   trigger_condition, required_action, prohibited_action, numeric_constraints,
                   deadline_constraints, region, industry, effective_date, expiry_date,
                   source_page, source_paragraph, source_text, created_at, updated_at
            FROM tax_rule
            WHERE regulation_document_id=?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (regulation_document_id, int(limit)),
        )
    else:
        cur.execute(
            """
            SELECT id, regulation_document_id, law_title, article_no, rule_type,
                   trigger_condition, required_action, prohibited_action, numeric_constraints,
                   deadline_constraints, region, industry, effective_date, expiry_date,
                   source_page, source_paragraph, source_text, created_at, updated_at
            FROM tax_rule
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (int(limit),),
        )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def clear_clause_rule_matches_by_contract(cfg, contract_id):
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        """
        DELETE FROM clause_rule_match
        WHERE clause_id IN (
            SELECT id FROM contract_clause WHERE contract_document_id=?
        )
        """,
        (contract_id,),
    )
    conn.commit()
    conn.close()


def create_clause_rule_matches(cfg, matches, created_by):
    now = datetime.utcnow().isoformat()
    conn = get_conn(cfg)
    cur = conn.cursor()
    for m in matches:
        cur.execute(
            """
            INSERT INTO clause_rule_match(
                id, clause_id, rule_id, match_score, match_label,
                evidence_json, created_by, created_at, updated_at
            )
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                str(uuid.uuid4()),
                m.get("clause_id", ""),
                m.get("rule_id", ""),
                float(m.get("match_score") or 0),
                m.get("match_label", "not_mentioned"),
                m.get("evidence_json", "{}"),
                created_by,
                now,
                now,
            ),
        )
    conn.commit()
    conn.close()


def list_clause_rule_matches_by_contract(cfg, contract_id, limit=500):
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT m.id, m.clause_id, m.rule_id, m.match_score, m.match_label, m.evidence_json, m.created_at, m.updated_at
        FROM clause_rule_match m
        JOIN contract_clause c ON c.id = m.clause_id
        WHERE c.contract_document_id=?
        ORDER BY
            CASE m.match_label
                WHEN 'non_compliant' THEN 1
                WHEN 'not_mentioned' THEN 2
                WHEN 'compliant' THEN 3
                ELSE 4
            END,
            m.match_score DESC,
            m.created_at DESC
        LIMIT ?
        """,
        (contract_id, int(limit)),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def count_clause_rule_matches_by_contract(cfg, contract_id):
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COUNT(1) AS c
        FROM clause_rule_match m
        JOIN contract_clause c ON c.id=m.clause_id
        WHERE c.contract_document_id=?
        """,
        (contract_id,),
    )
    row = cur.fetchone()
    conn.close()
    return int(row["c"] if row else 0)


def clear_audit_issues_by_contract(cfg, contract_id):
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        """
        DELETE FROM audit_trace
        WHERE issue_id IN (
            SELECT id FROM audit_issue WHERE contract_document_id=?
        )
        """,
        (contract_id,),
    )
    cur.execute(
        "DELETE FROM audit_issue WHERE contract_document_id=?", (contract_id,))
    conn.commit()
    conn.close()


def create_audit_issues(cfg, issues, created_by):
    now = datetime.utcnow().isoformat()
    conn = get_conn(cfg)
    cur = conn.cursor()
    for x in issues:
        cur.execute(
            """
            INSERT INTO audit_issue(
                id, contract_document_id, clause_id, rule_id, risk_level, issue_text,
                suggestion, reviewer_status, reviewer_note, created_by, created_at, updated_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                str(uuid.uuid4()),
                x.get("contract_document_id", ""),
                x.get("clause_id", ""),
                x.get("rule_id", ""),
                x.get("risk_level", "medium"),
                x.get("issue_text", ""),
                x.get("suggestion", ""),
                x.get("reviewer_status", "pending"),
                x.get("reviewer_note", ""),
                created_by,
                now,
                now,
            ),
        )
    conn.commit()
    conn.close()


def get_audit_issue(cfg, issue_id):
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("SELECT * FROM audit_issue WHERE id=?", (issue_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def update_audit_issue_review(cfg, issue_id, reviewer_status, reviewer_note, risk_level=None):
    now = datetime.utcnow().isoformat()
    conn = get_conn(cfg)
    cur = conn.cursor()
    if risk_level:
        cur.execute(
            """
            UPDATE audit_issue
            SET reviewer_status=?, reviewer_note=?, risk_level=?, updated_at=?
            WHERE id=?
            """,
            (reviewer_status, reviewer_note, risk_level, now, issue_id),
        )
    else:
        cur.execute(
            """
            UPDATE audit_issue
            SET reviewer_status=?, reviewer_note=?, updated_at=?
            WHERE id=?
            """,
            (reviewer_status, reviewer_note, now, issue_id),
        )
    conn.commit()
    conn.close()


def insert_audit_trace(cfg, issue_id, action_type, operator, payload_json, created_by):
    now = datetime.utcnow().isoformat()
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO audit_trace(
            id, issue_id, action_type, operator, payload_json, created_by, created_at, updated_at
        )
        VALUES(?,?,?,?,?,?,?,?)
        """,
        (
            str(uuid.uuid4()),
            issue_id,
            action_type,
            operator,
            payload_json,
            created_by,
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()


def list_audit_trace_by_issue(cfg, issue_id, limit=200):
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, issue_id, action_type, operator, payload_json, created_at, updated_at
        FROM audit_trace
        WHERE issue_id=?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (issue_id, int(limit)),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def list_audit_trace_by_contract(cfg, contract_id, limit=500):
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT t.id, t.issue_id, t.action_type, t.operator, t.payload_json, t.created_at, t.updated_at
        FROM audit_trace t
        JOIN audit_issue i ON i.id = t.issue_id
        WHERE i.contract_document_id=?
        ORDER BY t.created_at DESC
        LIMIT ?
        """,
        (contract_id, int(limit)),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def create_tax_cleanup_job(cfg, retention_days, created_by):
    now = datetime.utcnow().isoformat()
    job_id = str(uuid.uuid4())
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO tax_audit_cleanup_job(
            id, status, retention_days, started_at, archived_contracts,
            deleted_files, details_json, error, created_by, created_at, updated_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            job_id,
            "running",
            int(retention_days),
            now,
            0,
            0,
            "{}",
            "",
            created_by,
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()
    return job_id


def finish_tax_cleanup_job(cfg, job_id, status, archived_contracts, deleted_files, details_json="", error=""):
    now = datetime.utcnow().isoformat()
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE tax_audit_cleanup_job
        SET status=?, archived_contracts=?, deleted_files=?, details_json=?, error=?, finished_at=?, updated_at=?
        WHERE id=?
        """,
        (
            status,
            int(archived_contracts),
            int(deleted_files),
            details_json or "{}",
            error or "",
            now,
            now,
            job_id,
        ),
    )
    conn.commit()
    conn.close()


def get_tax_cleanup_job(cfg, job_id):
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("SELECT * FROM tax_audit_cleanup_job WHERE id=?", (job_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def list_tax_cleanup_jobs(cfg, limit=50):
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, status, retention_days, started_at, finished_at, archived_contracts,
               deleted_files, details_json, error, created_by, created_at, updated_at
        FROM tax_audit_cleanup_job
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (int(limit),),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def list_contract_documents_for_archive(cfg):
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT d.id, d.original_filename, d.parse_status, d.ocr_used, d.created_at, d.updated_at
        FROM contract_document d
        LEFT JOIN tax_audit_archive_record a ON a.contract_document_id = d.id
        WHERE a.id IS NULL
        ORDER BY d.created_at ASC
        """
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def create_tax_archive_record(cfg, contract_document_id, archive_path, archived_by="", source_job_id=""):
    now = datetime.utcnow().isoformat()
    record_id = str(uuid.uuid4())
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO tax_audit_archive_record(
            id, contract_document_id, archive_path, archived_at, archived_by, source_job_id, created_at, updated_at
        )
        VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(contract_document_id) DO UPDATE SET
            archive_path=excluded.archive_path,
            archived_at=excluded.archived_at,
            archived_by=excluded.archived_by,
            source_job_id=excluded.source_job_id,
            updated_at=excluded.updated_at
        """,
        (
            record_id,
            contract_document_id,
            archive_path,
            now,
            archived_by,
            source_job_id,
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()


def list_tax_archive_records(cfg, limit=200):
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, contract_document_id, archive_path, archived_at, archived_by, source_job_id, created_at, updated_at
        FROM tax_audit_archive_record
        ORDER BY archived_at DESC
        LIMIT ?
        """,
        (int(limit),),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows
