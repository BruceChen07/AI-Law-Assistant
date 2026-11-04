"""合同审计分块并发数的解析测试。"""

from app.services.contract_audit import _resolve_classic_parallel_workers


def test_parallel_workers_default_is_serial():
    assert _resolve_classic_parallel_workers({}, 12) == 1


def test_parallel_workers_from_contract_audit_block():
    cfg = {"contract_audit": {"parallel_workers": 2}}

    assert _resolve_classic_parallel_workers(cfg, 12) == 2


def test_parallel_workers_falls_back_to_runtime_config():
    cfg = {"memory_runtime_config": {"audit_parallel_workers": 3}}

    assert _resolve_classic_parallel_workers(cfg, 12) == 3


def test_parallel_workers_capped_by_chunk_count():
    cfg = {"contract_audit": {"parallel_workers": 4}}

    assert _resolve_classic_parallel_workers(cfg, 2) == 2
    assert _resolve_classic_parallel_workers(cfg, 0) == 1
