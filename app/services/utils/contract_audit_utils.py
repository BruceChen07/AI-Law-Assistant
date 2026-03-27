"""
Contract Audit Utilities.
Responsibilities: Provides stateless utility functions for plain text processing, regex matching, and article number normalization during the contract audit process.
Input/Output: Accepts strings or basic data types, and returns processed strings.
Exception Handling: When input is empty or type mismatches occur, typically returns an empty string or performs a safe conversion without throwing exceptions.
"""
import re
import structlog
from typing import Any

logger = structlog.get_logger(__name__)

def norm_text(v: Any) -> str:
    """去除文本中的所有空白字符。"""
    s = re.sub(r"\s+", "", str(v or ""))
    return s.strip()

def normalize_article_no(article: Any) -> str:
    """归一化法条编号，如将 '19' 转换为 '第19条'。"""
    s = str(article or "").strip()
    if not s:
        return ""
    if "条" in s:
        return s
    if s.startswith("第"):
        return f"{s}条"
    return f"第{s}条"

def citation_match_key(law_title: Any, article_no: Any) -> str:
    """生成法规和条款的唯一匹配键。"""
    law = str(law_title or "").strip().lower()
    article = normalize_article_no(article_no).lower()
    return f"{law}##{article}"
