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


def normalize_law_title(law_title: Any) -> str:
    s = str(law_title or "").strip().lower()
    if not s:
        return ""
    s = s.replace("（", "(").replace("）", ")").replace("《", "").replace("》", "")
    s = re.sub(r"[\"'“”‘’`·•,.;:!?\(\)\[\]\{\}]", "", s)
    s = re.sub(r"\s+", "", s)
    alias = {
        "civilcodeofthepeoplesrepublicofchina": "中华人民共和国民法典",
        "prccivilcode": "中华人民共和国民法典",
        "thecivilcodeofthepeoplesrepublicofchina": "中华人民共和国民法典",
        "valueaddedtaxlawofthepeoplesrepublicofchina": "中华人民共和国增值税法",
        "vatlawofthepeoplesrepublicofchina": "中华人民共和国增值税法",
        "enterpriseincometaxlawofthepeoplesrepublicofchina": "中华人民共和国企业所得税法",
        "individualincometaxlawofthepeoplesrepublicofchina": "中华人民共和国个人所得税法",
        "lawofthepeoplesrepublicofchinaontaxcollectionandadministration": "中华人民共和国税收征收管理法",
        "invoicemanagementmeasuresofthepeoplesrepublicofchina": "中华人民共和国发票管理办法",
        "invoiceadministrationlawofthepeoplesrepublicofchina": "中华人民共和国发票管理办法",
    }
    return alias.get(s, s)


def normalize_article_no(article: Any) -> str:
    """归一化法条编号，如将 '19' 转换为 '第19条'。"""
    s = str(article or "").strip().replace("（", "(").replace("）", ")")
    if not s:
        return ""
    s = re.sub(r"\s+", "", s)
    m_article = re.search(r"(?i)article\.?([0-9]{1,5})", s)
    if m_article:
        return f"第{m_article.group(1)}条"
    m_cn = re.search(r"第([一二三四五六七八九十百千零〇两]+)条?", s)
    if m_cn:
        return f"第{m_cn.group(1)}条"
    m_digit = re.search(r"([0-9]{1,5})", s)
    if m_digit:
        return f"第{m_digit.group(1)}条"
    if "条" in s:
        return s
    if s.startswith("第"):
        return f"{s}条"
    return f"第{s}条"


def citation_match_key(law_title: Any, article_no: Any) -> str:
    """生成法规和条款的唯一匹配键。"""
    law = normalize_law_title(law_title)
    article = normalize_article_no(article_no).lower()
    return f"{law}##{article}"
