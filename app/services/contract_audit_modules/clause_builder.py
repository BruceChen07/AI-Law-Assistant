"""
Clause Builder.
Responsibilities: Responsible for splitting the complete contract text into a structured list of preview clauses.
Input/Output: Accepts the full contract text and returns a list of dictionaries containing fields such as clause_id, anchor_id, and clause_text.
Exception Handling: Returns an empty list when the text is empty.
"""
import structlog
from typing import List, Dict, Any
from app.services.tax_contract_parser import split_contract_clauses

logger = structlog.get_logger(__name__)

def build_preview_clauses(text: str) -> List[Dict[str, Any]]:
    """Build a list of contract clauses for preview and audit."""
    clauses = split_contract_clauses(text)
    out: List[Dict[str, Any]] = []
    for idx, clause in enumerate(clauses, 1):
        cid = f"c{idx}"
        out.append(
            {
                "clause_id": cid,
                "anchor_id": f"clause-{cid}",
                "clause_path": clause.get("clause_path", ""),
                "page_no": int(clause.get("page_no") or 0),
                "paragraph_no": str(clause.get("paragraph_no") or ""),
                "clause_text": clause.get("clause_text", ""),
            }
        )
    return out
