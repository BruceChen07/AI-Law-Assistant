from __future__ import annotations

import json
from typing import Any, Dict, Sequence


REQUIRED_RISK_FIELDS = {
    "level",
    "issue",
    "suggestion",
    "citation_id",
    "law_title",
    "article_no",
    "evidence",
    "confidence",
    "clause_id",
}


def validate_model_json(
    result_text: str,
    evidence_items: Sequence[Dict[str, Any]],
    clauses: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    try:
        payload = json.loads(str(result_text or ""))
    except (TypeError, ValueError):
        return {
            "valid": False,
            "payload": {},
            "accepted_risks": [],
            "rejected_risks": [],
            "errors": ["response_not_single_json_object"],
        }
    if not isinstance(payload, dict):
        return {
            "valid": False,
            "payload": {},
            "accepted_risks": [],
            "rejected_risks": [],
            "errors": ["response_not_json_object"],
        }
    if not isinstance(payload.get("summary"), str) or not isinstance(
        payload.get("risks"), list
    ):
        return {
            "valid": False,
            "payload": payload,
            "accepted_risks": [],
            "rejected_risks": [],
            "errors": ["response_schema_invalid"],
        }

    evidence_map = {
        str(item.get("citation_id") or "").strip(): item
        for item in evidence_items
        if isinstance(item, dict) and str(item.get("citation_id") or "").strip()
    }
    clause_ids = {
        str(item.get("clause_id") or "").strip()
        for item in clauses
        if isinstance(item, dict) and str(item.get("clause_id") or "").strip()
    }
    accepted = []
    rejected = []
    errors = []
    for index, risk in enumerate(payload["risks"]):
        if not isinstance(risk, dict) or not REQUIRED_RISK_FIELDS.issubset(risk):
            errors.append(f"risk_schema_invalid:{index}")
            continue
        if any(
            not isinstance(risk.get(name), str)
            for name in REQUIRED_RISK_FIELDS - {"confidence"}
        ) or isinstance(risk.get("confidence"), bool) or not isinstance(
            risk.get("confidence"), (int, float)
        ):
            errors.append(f"risk_type_invalid:{index}")
            continue

        reason = ""
        citation_id = str(risk.get("citation_id") or "").strip()
        clause_id = str(risk.get("clause_id") or "").strip()
        evidence = evidence_map.get(citation_id)
        if evidence is None:
            reason = "unknown_citation_id"
        elif clause_id not in clause_ids:
            reason = "unknown_clause_id"
        elif str(risk.get("law_title") or "").strip() != str(
            evidence.get("law_title") or evidence.get("title") or ""
        ).strip():
            reason = "law_title_mismatch"
        elif str(risk.get("article_no") or "").strip() != str(
            evidence.get("article_no") or ""
        ).strip():
            reason = "article_no_mismatch"

        if reason:
            rejected.append(
                {
                    "index": index,
                    "reason": reason,
                    "risk": dict(risk),
                    "verification_status": "rejected",
                }
            )
            continue
        accepted.append({**risk, "verification_status": "verified"})

    if errors:
        return {
            "valid": False,
            "payload": payload,
            "accepted_risks": [],
            "rejected_risks": rejected,
            "errors": errors,
        }
    return {
        "valid": True,
        "payload": payload,
        "accepted_risks": accepted,
        "rejected_risks": rejected,
        "errors": [],
    }
