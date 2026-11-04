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
