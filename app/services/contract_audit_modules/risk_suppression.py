"""
Risk Suppression.
Responsibilities: Responsible for detecting missing-style risks (e.g., not specified/mentioned mentioned) and suppressing them.
Input/Output: Accepts a single risk item and all contract clauses, and returns whether to suppress the risk and the suppression hit details.
Exception Handling: Logs error messages if the risk structure is abnormal or the context is missing, and suppresses the risk by default.
"""
import structlog
from typing import Dict, Any, List, Set, Tuple, Optional

logger = structlog.get_logger(__name__)

MISSING_RISK_MARKERS = [
    "未明确", "未约定", "未提及", "未说明", "缺失", "缺乏", "未写明", "not mention", "not specified", "not clear"
]

RISK_TOPIC_KEYWORDS = {
    "invoice": ["发票", "增值税普通发票", "专用发票", "电子发票", "invoice", "vat invoice"],
    "invoice_timing": ["开票时点", "开票时间", "发送发票", "发票发送", "invoice timing", "issue invoice"],
    "tax_rate": ["税率", "税点", "增值税税率", "vat rate", "tax rate"],
    "tax_obligation": ["纳税义务", "纳税时间", "征管", "tax obligation", "tax liability"],
    "withholding": ["代扣", "代缴", "代扣代缴", "withholding"],
}

FALLBACK_SERVICE_KEYWORDS = [
    "餐饮", "外卖", "平台服务", "补贴", "服务费", "供应", "service", "platform"
]

FALLBACK_TAX_RATE_KEYWORDS = [
    "税率", "税点", "免税", "vat", "tax rate"
]


def contains_any(text: str, keywords: List[str]) -> bool:
    """Check if the text contains any of the keywords."""
    t = str(text or "").lower()
    return any(str(k or "").lower() in t for k in keywords)


def is_missing_style_risk(risk: Dict[str, Any]) -> bool:
    """Check if the risk is a missing-style risk."""
    merged = " ".join([
        str(risk.get("issue", "") or ""),
        str(risk.get("suggestion", "") or ""),
        str(risk.get("evidence", "") or ""),
    ])
    return contains_any(merged, MISSING_RISK_MARKERS)


def detect_risk_topics(risk: Dict[str, Any]) -> Set[str]:
    """Detect tax topics associated with a missing-style risk."""
    merged = " ".join([
        str(risk.get("issue", "") or ""),
        str(risk.get("suggestion", "") or ""),
        str(risk.get("evidence", "") or ""),
    ])
    topics: Set[str] = set()
    for topic, kws in RISK_TOPIC_KEYWORDS.items():
        if contains_any(merged, kws):
            topics.add(topic)
    return topics


def find_counter_evidence_clause(
    topics: Set[str],
    clauses: List[Dict[str, Any]],
    skip_clause_id: str = "",
) -> Tuple[bool, Dict[str, Any]]:
    """Find a clause that counters a "missing-style risk" in all clauses."""
    if not topics or not clauses:
        return False, {}
    for clause in clauses:
        cid = str(clause.get("clause_id") or "")
        if skip_clause_id and cid == skip_clause_id:
            continue
        ctext = str(clause.get("clause_text") or "")
        if not ctext.strip():
            continue
        for topic in topics:
            kws = RISK_TOPIC_KEYWORDS.get(topic, [])
            if kws and contains_any(ctext, kws):
                return True, {
                    "clause_id": cid,
                    "clause_path": str(clause.get("clause_path") or ""),
                    "page_no": int(clause.get("page_no") or 0),
                    "paragraph_no": str(clause.get("paragraph_no") or ""),
                    "topic": topic,
                    "quote": ctext[:160],
                }
    return False, {}


def find_counter_evidence_in_global_context(
    topics: Set[str],
    global_tax_context: Dict[str, Any],
    skip_clause_id: str = "",
) -> Tuple[bool, Dict[str, Any]]:
    """Find counter evidence in the global tax context."""
    if not topics or not isinstance(global_tax_context, dict):
        return False, {}
    for topic in topics:
        items = global_tax_context.get(topic)
        if not isinstance(items, list):
            continue
        for it in items:
            cid = str(it.get("clause_id") or "")
            if skip_clause_id and cid == skip_clause_id:
                continue
            return True, {
                "topic": topic,
                "clause_id": cid,
                "clause_path": str(it.get("clause_path") or ""),
                "page_no": int(it.get("page_no") or 0),
                "paragraph_no": str(it.get("paragraph_no") or ""),
                "quote": str(it.get("quote") or ""),
                "source": "global_tax_context",
            }
    return False, {}


def should_suppress_missing_risk(
    risk: Dict[str, Any],
    clauses: List[Dict[str, Any]],
    current_clause_id: str = "",
    global_tax_context: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, Dict[str, Any]]:
    """Determine whether to suppress a missing-style risk."""
    if not isinstance(risk, dict) or not is_missing_style_risk(risk):
        return False, {}
    topics = detect_risk_topics(risk)
    found, hit = find_counter_evidence_in_global_context(
        topics, global_tax_context or {}, current_clause_id)
    if found:
        return True, hit
    found, hit = find_counter_evidence_clause(
        topics, clauses, current_clause_id)
    if not found:
        return False, {}
    hit["source"] = "clause_scan"
    return True, hit


def build_global_tax_context(clauses: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build a global tax context for the contract."""
    context = {
        "invoice": [],
        "invoice_timing": [],
        "tax_rate": [],
        "tax_obligation": [],
        "withholding": [],
    }
    if not clauses:
        return context
    seen: Set[str] = set()
    for clause in clauses:
        cid = str(clause.get("clause_id") or "")
        cpath = str(clause.get("clause_path") or "")
        ctext = str(clause.get("clause_text") or "")
        if not ctext.strip():
            continue
        for topic, kws in RISK_TOPIC_KEYWORDS.items():
            if not kws or not contains_any(ctext, kws):
                continue
            key = f"{topic}##{cid}"
            if key in seen:
                continue
            seen.add(key)
            context[topic].append(
                {
                    "clause_id": cid,
                    "clause_path": cpath,
                    "page_no": int(clause.get("page_no") or 0),
                    "paragraph_no": str(clause.get("paragraph_no") or ""),
                    "quote": ctext[:220],
                }
            )
    return context


def format_global_tax_context(context: Dict[str, Any], per_topic_limit: int = 3) -> str:
    """Format a global tax context as a Prompt usable text."""
    if not isinstance(context, dict):
        return ""
    lines: List[str] = []
    for topic in ["invoice", "invoice_timing", "tax_rate", "tax_obligation", "withholding"]:
        items = context.get(topic)
        if not isinstance(items, list) or not items:
            continue
        lines.append(f"{topic}:")
        for it in items[:per_topic_limit]:
            cpath = str(it.get("clause_path") or "")
            quote = str(it.get("quote") or "")
            lines.append(f"- 【{cpath}】 | {quote}")
    return "\n".join(lines)


def reconcile_cross_clause_conflicts(
    risks: List[Dict[str, Any]],
    clauses: List[Dict[str, Any]],
    global_tax_context: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Reconcile cross-clause conflicts."""
    if not risks:
        return risks, []
    kept: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []
    for risk in risks:
        if not isinstance(risk, dict):
            continue
        loc = risk.get("location") if isinstance(
            risk.get("location"), dict) else {}
        cid = str(loc.get("clause_id") or "")
        suppress, hit = should_suppress_missing_risk(
            risk,
            clauses,
            current_clause_id=cid,
            global_tax_context=global_tax_context,
        )
        if suppress:
            removed.append(
                {
                    "risk_id": str(loc.get("risk_id") or ""),
                    "clause_id": cid,
                    "topic": str(hit.get("topic") or ""),
                    "counter_clause_id": str(hit.get("clause_id") or ""),
                    "counter_source": str(hit.get("source") or ""),
                }
            )
            continue
        kept.append(risk)
    return kept, removed


def detect_zero_risk_fallback_hit(
    clauses: List[Dict[str, Any]],
    global_tax_context: Dict[str, Any],
) -> Tuple[bool, Dict[str, Any]]:
    has_tax_rate = False
    has_service = False
    best_hit: Dict[str, Any] = {}
    tax_items = global_tax_context.get("tax_rate") if isinstance(
        global_tax_context, dict) else []
    if isinstance(tax_items, list) and tax_items:
        has_tax_rate = True
        first = tax_items[0] if isinstance(tax_items[0], dict) else {}
        best_hit = {
            "topic": "tax_rate",
            "clause_id": str(first.get("clause_id") or ""),
            "clause_path": str(first.get("clause_path") or ""),
            "page_no": int(first.get("page_no") or 0),
            "paragraph_no": str(first.get("paragraph_no") or ""),
            "quote": str(first.get("quote") or ""),
            "source": "global_tax_context",
        }
    for clause in clauses or []:
        if not isinstance(clause, dict):
            continue
        ctext = str(clause.get("clause_text") or clause.get("text") or "")
        if not ctext:
            continue
        if contains_any(ctext, FALLBACK_TAX_RATE_KEYWORDS):
            has_tax_rate = True
            if not best_hit:
                best_hit = {
                    "topic": "tax_rate",
                    "clause_id": str(clause.get("clause_id") or ""),
                    "clause_path": str(clause.get("clause_path") or ""),
                    "page_no": int(clause.get("page_no") or 0),
                    "paragraph_no": str(clause.get("paragraph_no") or ""),
                    "quote": ctext[:220],
                    "source": "clause_scan",
                }
        if contains_any(ctext, FALLBACK_SERVICE_KEYWORDS):
            has_service = True
            if not best_hit:
                best_hit = {
                    "topic": "service",
                    "clause_id": str(clause.get("clause_id") or ""),
                    "clause_path": str(clause.get("clause_path") or ""),
                    "page_no": int(clause.get("page_no") or 0),
                    "paragraph_no": str(clause.get("paragraph_no") or ""),
                    "quote": ctext[:220],
                    "source": "clause_scan",
                }
    if has_tax_rate and has_service:
        best_hit["topic"] = "tax_rate_service_combo"
        return True, best_hit
    return False, {}
