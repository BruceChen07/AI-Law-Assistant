"""
Clause Builder.
职责: 负责将完整的合同文本切分为结构化的条款预览列表。
输入输出: 接收合同全文，返回包含 clause_id, anchor_id, clause_text 等字段的字典列表。
异常场景: 文本为空时返回空列表。
"""
import structlog
from typing import List, Dict, Any
from app.services.tax_contract_parser import split_contract_clauses

logger = structlog.get_logger(__name__)

def build_preview_clauses(text: str) -> List[Dict[str, Any]]:
    """构建用于前端预览和审计的合同条款列表。"""
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
