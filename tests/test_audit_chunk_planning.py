"""分块规划：按 token 预算打包，避免小合同被切成过多调用。"""

from app.services.contract_audit import (
    _chunk_preview_clauses,
    _estimate_clause_tokens,
    _plan_chunks_by_tokens,
)

POLICY = {
    "enabled": True,
    "clause_group_target_tokens": 2800,
    "input_hard_limit_tokens": 5120,
}


def _clauses(count: int, chars_per_clause: int = 60):
    return [
        {
            "clause_id": f"c{idx}",
            "title": f"第{idx}条",
            "clause_text": "甲" * chars_per_clause,
        }
        for idx in range(1, count + 1)
    ]


def test_small_contract_stays_single_group():
    clauses = _clauses(21)

    groups = _chunk_preview_clauses(clauses, max_calls=12, policy=POLICY)

    assert len(groups) == 1
    assert len(groups[0]) == 21


def test_large_contract_splits_by_token_budget():
    clauses = _clauses(270, chars_per_clause=120)
    total = sum(_estimate_clause_tokens(item) for item in clauses)

    groups = _plan_chunks_by_tokens(clauses, POLICY, max_calls=12)

    assert len(groups) == max(1, -(-total // POLICY["clause_group_target_tokens"]))
    assert sum(len(group) for group in groups) == 270


def test_groups_respect_hard_limit():
    clauses = _clauses(200, chars_per_clause=200)

    groups = _plan_chunks_by_tokens(clauses, POLICY, max_calls=2)

    for group in groups:
        group_tokens = sum(_estimate_clause_tokens(item) for item in group)
        assert group_tokens <= POLICY["input_hard_limit_tokens"]


def test_legacy_count_split_when_policy_disabled():
    clauses = _clauses(21)

    groups = _chunk_preview_clauses(clauses, max_calls=12, policy=None)

    assert len(groups) == 11
    assert all(len(group) == 2 for group in groups[:-1])


def test_few_clauses_single_group_without_policy():
    clauses = _clauses(6)

    groups = _chunk_preview_clauses(clauses, max_calls=12)

    assert len(groups) == 1
