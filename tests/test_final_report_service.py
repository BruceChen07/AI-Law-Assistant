from app.services.audit_orchestrator import run_contract_pipeline_bundle, AuditServices
from app.services.final_report_service import build_contract_final_report


def _sample_audit():
    return {
        "summary": "审计完成",
        "risk_summary": {"high": 1, "medium": 1, "low": 0},
        "risks": [
            {
                "level": "high",
                "issue": "未明确增值税税率",
                "suggestion": "补充税率条款",
                "citation_id": "cid-1",
                "law_title": "中华人民共和国增值税法",
                "article_no": "第1条",
                "evidence": "证据A",
                "location": {
                    "risk_id": "r1",
                    "clause_id": "c1",
                    "clause_path": "第一条",
                    "page_no": 1,
                    "paragraph_no": "1",
                    "quote": "合同未明确税率",
                    "score": 0.95,
                },
            },
            {
                "level": "medium",
                "issue": "开票时点不清",
                "suggestion": "明确开票时点",
                "citation_id": "cid-2",
                "law_title": "中华人民共和国发票管理办法",
                "article_no": "第2条",
                "evidence": "证据B",
                "location": {
                    "risk_id": "r2",
                    "clause_id": "c2",
                    "clause_path": "第二条",
                    "page_no": 2,
                    "paragraph_no": "1",
                    "quote": "合同未明确开票时间",
                    "score": 0.85,
                },
            },
        ],
        "citations": [
            {"citation_id": "cid-1", "law_title": "中华人民共和国增值税法", "article_no": "第1条", "content": "法律内容A"},
            {"citation_id": "cid-2", "law_title": "中华人民共和国发票管理办法", "article_no": "第2条", "content": "法律内容B"},
        ],
        "legal_validation": {
            "ok": False,
            "issues": [
                {"reason": "parse_failed", "severity": "high", "message": "第一次解析失败", "clause_id": "c1"}
            ],
        },
    }


def _sample_meta():
    return {
        "preview_clause_total": 12,
        "memory_llm_call_count": 3,
        "execution_path": "multipass_classic_stage1",
        "audit_duration_ms": 12345,
        "retrieval_used": True,
        "audit_token_budget_requires_multi_pass": True,
        "audit_token_budget_reasons": ["clauses_omitted"],
        "multipass_round_count": 3,
        "duplicate_items_removed": 1,
        "conflict_items_removed": 1,
        "review_item_count": 2,
        "review_retry_count": 1,
        "unresolved_review_item_count": 1,
        "reliability_level": "low",
    }


def test_build_contract_final_report_uses_structured_audit_only():
    report = build_contract_final_report(
        audit=_sample_audit(),
        meta=_sample_meta(),
        file_path=r"E:\workspace\AI-Law-Assistant\data\uploads\sample.docx",
        contract_id="audit-1",
    )
    assert report["contract_id"] == "audit-1"
    assert report["overview"]["contract_filename"] == "sample.docx"
    assert report["pipeline_summary"]["execution_path"] == "multipass_classic_stage1"
    assert report["reliability_summary"]["level"] == "low"
    assert report["risk_summary"]["high"] == 1
    assert len(report["risk_items"]) == 2
    assert len(report["evidence_items"]) == 2
    assert len(report["review_conclusions"]) >= 2
    assert len(report["exception_items"]) == 1
    assert report["key_findings"][0]["level"] == "high"


def test_run_contract_pipeline_bundle_exposes_final_report(monkeypatch):
    def _fake_run_contract_audit(cfg, services, **kwargs):
        audit = _sample_audit()
        meta = _sample_meta()
        audit["final_report"] = build_contract_final_report(
            audit=audit,
            meta=meta,
            file_path="contract.docx",
            contract_id="audit-x",
        )
        audit["pipeline_summary"] = audit["final_report"]["pipeline_summary"]
        audit["reliability_summary"] = audit["final_report"]["reliability_summary"]
        return {"audit": audit, "meta": meta}

    monkeypatch.setattr(
        "app.services.audit_orchestrator.run_contract_audit",
        _fake_run_contract_audit,
    )
    out = run_contract_pipeline_bundle(
        cfg={},
        services=AuditServices(),
        file_path="contract.docx",
        lang="zh",
    )
    assert out["final_report"]["contract_id"] == "audit-x"
    assert out["pipeline_summary"]["execution_path"] == "multipass_classic_stage1"
    assert out["reliability_summary"]["level"] == "low"
