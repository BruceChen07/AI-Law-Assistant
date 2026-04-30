from app.memory_system.rerank import rerank_memory_candidates, apply_context_budget


def test_rerank_memory_candidates_prefers_regulation_match():
    query = "发票 税率 开票时点"
    candidates = [
        {
            "memory_id": "m_other_pack",
            "regulation_pack_id": "rp_other",
            "clause_text_excerpt": "发票税率约定不清",
            "risk_reasoning": "建议补充开票时点",
            "memory_quality_score": 0.95,
            "confidence": 0.95,
            "created_at": "2026-01-01T00:00:00",
            "outcome": "success",
        },
        {
            "memory_id": "m_same_pack",
            "regulation_pack_id": "rp_same",
            "clause_text_excerpt": "发票税率约定不清",
            "risk_reasoning": "建议补充开票时点",
            "memory_quality_score": 0.80,
            "confidence": 0.80,
            "created_at": "2026-01-01T00:00:00",
            "outcome": "success",
        },
    ]
    ranked = rerank_memory_candidates(candidates, query_text=query, regulation_pack_id="rp_same")
    assert ranked[0]["memory_id"] == "m_same_pack"
    assert ranked[0]["_rerank"]["regulation_match"] == 1.0


def test_apply_context_budget_limits_chars_and_items():
    candidates = [
        {"memory_id": "m1", "_context_text": "A" * 180, "score": 0.9},
        {"memory_id": "m2", "_context_text": "B" * 180, "score": 0.8},
        {"memory_id": "m3", "_context_text": "C" * 180, "score": 0.7},
    ]
    selected = apply_context_budget(candidates, max_items=3, max_chars=300)
    assert len(selected) <= 2
    assert sum(len(str(x.get("_context_text") or "")) for x in selected) <= 300
