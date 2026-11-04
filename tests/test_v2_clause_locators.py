import sys
from pathlib import Path

sys.path.insert(0, ".")

from app.services.contract_audit import (
    _v2_chunk_clauses,
    normalize_units,
    source_from_text,
)


def _units_and_clauses():
    text = "第一条 税率按9%执行。\n第二条 双方按法规办理。\n第三条 其他约定。"
    clauses = [
        {
            "clause_id": "c1",
            "anchor_id": "clause-c1",
            "clause_path": "第一条",
            "page_no": 2,
            "paragraph_no": "7",
            "clause_text": "第一条 税率按9%执行。",
        },
        {
            "clause_id": "c2",
            "anchor_id": "clause-c2",
            "clause_path": "第二条",
            "page_no": 2,
            "paragraph_no": "8",
            "clause_text": "第二条 双方按法规办理。",
        },
    ]
    source = source_from_text(text, document_id="doc-1", source_hash="hash-1")
    return normalize_units(source, clauses), clauses


def test_v2_chunk_clauses_keep_original_locators():
    units, clauses = _units_and_clauses()

    resolved = _v2_chunk_clauses(units, clauses)

    assert resolved
    first = resolved[0]
    assert first["clause_id"] == "c1"
    assert first["page_no"] == 2
    assert first["paragraph_no"] == "7"
    assert first["clause_path"] == "第一条"
    assert first["clause_text"] == "第一条 税率按9%执行。"
    assert first["locate_source"] == "preview_clause"


def test_v2_chunk_clauses_without_index_mark_synthetic_locator():
    units, _clauses = _units_and_clauses()

    resolved = _v2_chunk_clauses(units)

    assert resolved
    assert resolved[0]["locate_source"] == "chunk_unit"
    assert resolved[0]["page_no"] == 0
