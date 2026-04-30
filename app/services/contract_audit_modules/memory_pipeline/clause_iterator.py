"""Clause iteration and priority helpers for memory audit loop."""

from typing import Dict, Any, List, Set


def clause_priority_score(clause_id: str, title: str, body: str, clause_path: str) -> int:
    text = " ".join([str(clause_id or ""), str(title or ""),
                    str(body or ""), str(clause_path or "")]).lower()
    keywords = [
        "税", "tax", "vat", "invoice", "发票", "税率", "计税", "纳税",
        "付款", "payment", "结算", "liability", "违约", "赔偿",
        "termination", "解除", "jurisdiction", "管辖",
    ]
    return sum(1 for k in keywords if k in text)


def iter_clause_candidates(preview_clauses: List[Dict[str, Any]]):
    """Yield normalized clause records for isolated clause-level testing."""
    for idx, pc in enumerate(list(preview_clauses or []), start=1):
        clause_id = str(pc.get("clause_id") or "").strip()
        title = str(pc.get("title") or "")
        body = str(pc.get("clause_text") or pc.get("text") or "")
        clause_path = str(pc.get("clause_path") or "")
        yield {
            "order": idx,
            "clause_id": clause_id,
            "title": title,
            "body": body,
            "clause_path": clause_path,
            "priority_score": clause_priority_score(
                clause_id, title, body, clause_path),
        }


def build_clause_priority_index(preview_clauses: List[Dict[str, Any]]) -> Dict[str, Any]:
    preview_order_map: Dict[str, int] = {}
    preview_priority_orders: List[int] = []
    preview_priority_clause_ids: Set[str] = set()
    for item in iter_clause_candidates(preview_clauses):
        idx = int(item["order"])
        pcid = str(item["clause_id"])
        if pcid:
            preview_order_map[pcid] = idx
        pscore = int(item["priority_score"])
        if pscore > 0:
            preview_priority_orders.append(idx)
            if pcid:
                preview_priority_clause_ids.add(pcid)
    return {
        "preview_order_map": preview_order_map,
        "preview_priority_orders": preview_priority_orders,
        "preview_priority_clause_ids": preview_priority_clause_ids,
    }
