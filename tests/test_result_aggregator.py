import json

from app.services.result_aggregator import aggregate_chunk_audit_results


def _risk(level, issue, clause_id, *, law_title="中华人民共和国增值税法", article_no="第1条", citation_id="cid-1", score=0.9):
    return {
        "level": level,
        "issue": issue,
        "suggestion": "建议修订",
        "citation_id": citation_id,
        "law_title": law_title,
        "article_no": article_no,
        "law_reference": f"{law_title} {article_no}",
        "location": {
            "risk_id": f"r-{clause_id}-{issue[:2]}",
            "clause_id": clause_id,
            "page_no": 1,
            "paragraph_no": "1",
            "clause_path": clause_id,
            "score": score,
        },
    }


def test_aggregate_chunk_audit_results_dedupes_similar_risks():
    chunk_results = [
        {
            "chunk_id": "chunk-001",
            "risk_items": [_risk("medium", "未明确税率约定", "c1", score=0.8)],
            "evidence_links": [{"citation_id": "cid-1", "law_title": "中华人民共和国增值税法", "article_no": "第1条"}],
            "parse_failed_flag": False,
            "truncated_flag": False,
        },
        {
            "chunk_id": "chunk-002",
            "risk_items": [_risk("medium", "未明确税率约定。", "c1", score=0.95)],
            "evidence_links": [{"citation_id": "cid-1", "law_title": "中华人民共和国增值税法", "article_no": "第1条"}],
            "parse_failed_flag": False,
            "truncated_flag": False,
        },
    ]
    clauses = [{"clause_id": "c1", "clause_text": "合同未约定税率", "clause_path": "第一条", "page_no": 1, "paragraph_no": "1"}]
    out = aggregate_chunk_audit_results(
        cfg={},
        chunk_audit_results=chunk_results,
        preview_clauses=clauses,
        lang="zh",
    )
    assert len(out["audit"]["risks"]) == 1
    assert out["meta"]["duplicate_items_removed"] == 1
    assert out["audit"]["risks"][0]["location"]["score"] == 0.95


def test_aggregate_chunk_audit_results_suppresses_missing_conflicts():
    chunk_results = [
        {
            "chunk_id": "chunk-001",
            "risk_items": [
                _risk("low", "未明确发票类型", "c1", citation_id="", law_title="", article_no="", score=0.7)
            ],
            "evidence_links": [],
            "parse_failed_flag": False,
            "truncated_flag": False,
        }
    ]
    clauses = [
        {"clause_id": "c1", "clause_text": "本条未明确发票类型", "clause_path": "第一条", "page_no": 1, "paragraph_no": "1"},
        {"clause_id": "c2", "clause_text": "乙方开具增值税普通发票", "clause_path": "第二条", "page_no": 2, "paragraph_no": "1"},
    ]
    out = aggregate_chunk_audit_results(
        cfg={},
        chunk_audit_results=chunk_results,
        preview_clauses=clauses,
        lang="zh",
    )
    assert out["meta"]["conflict_items_removed"] == 1
    assert len(out["audit"]["risks"]) == 0


def test_aggregate_chunk_audit_results_builds_export_payloads_and_review_items():
    chunk_results = [
        {
            "chunk_id": "chunk-001",
            "risk_items": [_risk("high", "高风险但无依据", "c1", citation_id="", law_title="", article_no="", score=0.88)],
            "evidence_links": [],
            "parse_failed_flag": True,
            "truncated_flag": True,
        }
    ]
    clauses = [{"clause_id": "c1", "clause_text": "高风险条款", "clause_path": "第一条", "page_no": 1, "paragraph_no": "1"}]
    out = aggregate_chunk_audit_results(
        cfg={},
        chunk_audit_results=chunk_results,
        preview_clauses=clauses,
        lang="zh",
    )
    exports = out["exports"]
    parsed = json.loads(exports["json"])
    assert "summary" in parsed
    assert "risk_summary" in parsed
    assert "level,issue,law_title" in exports["csv"]
    assert out["meta"]["review_item_count"] >= 3

def test_aggregate_assigns_unique_traceable_risk_ids_across_chunks():
    chunk_results = [
        {
            "chunk_id": "chunk-001",
            "clause_range": {"clause_ids": ["c1"]},
            "risk_items": [_risk("medium", "未明确税率约定", "c1")],
            "evidence_links": [],
            "parse_failed_flag": False,
            "truncated_flag": False,
        },
        {
            "chunk_id": "chunk-002",
            "clause_range": {"clause_ids": ["c2"]},
            # same local id as the risk above, different clause and wording
            "risk_items": [_risk("high", "未约定开票时限", "c2", citation_id="cid-2")],
            "evidence_links": [],
            "parse_failed_flag": False,
            "truncated_flag": False,
        },
    ]

    aggregated = aggregate_chunk_audit_results(
        cfg={},
        chunk_audit_results=chunk_results,
        preview_clauses=[
            {"clause_id": "c1", "clause_path": "第一条", "clause_text": "税率按9%执行"},
            {"clause_id": "c2", "clause_path": "第二条", "clause_text": "开票时限未约定"},
        ],
        lang="zh",
    )

    risks = aggregated["audit"]["risks"]
    assert len(risks) == 2
    ids = [risk["risk_id"] for risk in risks]
    assert len(ids) == len(set(ids)) == 2
    assert ids == ["risk-001", "risk-002"]

    by_clause = {risk["location"]["clause_id"]: risk for risk in risks}
    assert by_clause["c1"]["location"]["chunk_id"] == "chunk-001"
    assert by_clause["c2"]["location"]["chunk_id"] == "chunk-002"
    # the chunk-local id is preserved for correlation with chunk-level reports
    assert by_clause["c1"]["location"]["risk_id"] == "risk-001"
    assert by_clause["c1"]["location"]["source_risk_id"].startswith("r-")
