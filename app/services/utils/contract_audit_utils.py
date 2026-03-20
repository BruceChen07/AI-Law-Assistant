"""
Contract Audit Utilities.
职责: 提供合同审计过程中的纯文本处理、正则匹配、文章编号归一化等无状态工具函数。
输入输出: 接收字符串或基础数据类型，返回处理后的字符串。
异常场景: 输入为空或类型不匹配时，通常返回空字符串或进行安全转换，不抛出异常。
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
