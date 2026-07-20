import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.database import get_conn
from app.services.audit_capabilities import (
    get_agent_profile,
    get_rule_pack_detail,
    get_template_detail,
    list_rule_pack_versions,
)
from app.services.audit_orchestrator import (
    tax_analyze_contract,
    tax_build_report,
    tax_generate_issues,
    tax_match_contract,
)
from app.services.crud import (
    clear_evidence_anchors_by_contract,
    create_evidence_anchor,
    get_tax_contract_document,
    insert_audit_trace,
    list_audit_trace_by_contract,
    list_clause_rule_matches_by_contract,
    list_contract_clauses,
    list_evidence_anchors_by_contract,
    list_tax_audit_issues_by_contract,
)

DEFAULT_TEMPLATE_ID = "tax_contract_audit_default"
SANDBOX_MODE = "builtin_only"

SKILL_EXECUTION_ORDER = [
    {"skill_id": "entity_extract_skill", "stage": "analyze", "llm_cost": 0},
    {"skill_id": "rule_precheck_skill", "stage": "match", "llm_cost": 0},
    {"skill_id": "clause_match_skill", "stage": "match", "llm_cost": 0},
    {"skill_id": "evidence_pack_skill", "stage": "evidence_pack", "llm_cost": 0},
    {"skill_id": "final_review_skill", "stage": "issues", "llm_cost": 1},
    {"skill_id": "compliance_summary_skill", "stage": "report", "llm_cost": 1},
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _safe_json_loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(str(value))
    except Exception:
        return default


def _zero_issue_summary(contract_id: str) -> Dict[str, Any]:
    return {
        "contract_id": contract_id,
        "total": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
    }


def _summarize_value(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        keys = list(value.keys())[:8]
        return {"type": "dict", "keys": keys, "size": len(value)}
    if isinstance(value, list):
        return {"type": "list", "size": len(value)}
    return {"type": type(value).__name__, "value": value}


def _row_to_runtime_session(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    item["request"] = _safe_json_loads(item.pop("request_json", "{}"), {})
    item["runtime"] = _safe_json_loads(item.pop("runtime_json", "{}"), {})
    item["result"] = _safe_json_loads(item.pop("result_json", "{}"), {})
    return item


def _row_to_skill_run(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    item["input_summary"] = _safe_json_loads(item.pop("input_summary_json", "{}"), {})
    item["output_summary"] = _safe_json_loads(item.pop("output_summary_json", "{}"), {})
    item["llm_cost"] = int(item.get("llm_cost") or 0)
    item["position_no"] = int(item.get("position_no") or 0)
    return item


def _resolve_runtime_profile(
    cfg: Dict[str, Any],
    owner_id: str,
    profile_id: str = "",
) -> Dict[str, Any]:
    if profile_id:
        profile = get_agent_profile(cfg, profile_id, owner_id)
        if not profile:
            raise ValueError("agent profile not found")
        template = get_template_detail(
            cfg, str(profile.get("template_id") or ""), user_id=owner_id
        )
    else:
        template = get_template_detail(cfg, DEFAULT_TEMPLATE_ID, user_id=owner_id)
        profile = None

    if not template:
        raise ValueError("runtime template not found")

    if profile is None:
        profile = {
            "id": "",
            "owner_id": owner_id,
            "display_name": "系统税审默认 Agent",
            "description": "Fallback runtime profile from default template",
            "scene": str(template.get("scene") or "tax_contract_audit"),
            "template_id": template["id"],
            "enabled_skill_ids": list(template.get("skill_ids") or []),
            "enabled_rule_pack_ids": list(template.get("rule_pack_ids") or []),
            "system_prompt": "",
            "max_llm_steps": int(template.get("max_llm_steps") or 2),
            "max_skills_per_run": int(template.get("max_skills_per_run") or 6),
            "status": "active",
        }

    return {"profile": profile, "template": template}


def _build_skill_plan(profile: Dict[str, Any], template: Dict[str, Any]) -> Dict[str, Any]:
    enabled_skill_ids = set(profile.get("enabled_skill_ids") or [])
    preferred_order = list(template.get("skill_ids") or [])
    ordered_ids: List[str] = [
        skill_id for skill_id in preferred_order if skill_id in enabled_skill_ids
    ]
    for skill_id in profile.get("enabled_skill_ids") or []:
        if skill_id not in ordered_ids:
            ordered_ids.append(skill_id)

    max_skills_per_run = max(
        1, int(profile.get("max_skills_per_run") or len(ordered_ids) or 1)
    )
    max_llm_steps = max(0, int(profile.get("max_llm_steps") or 0))
    llm_steps_used = 0
    items: List[Dict[str, Any]] = []
    planned_skill_ids: List[str] = []
    skill_meta_map = {item["skill_id"]: item for item in SKILL_EXECUTION_ORDER}

    for skill_id in ordered_ids:
        meta = skill_meta_map.get(
            skill_id, {"skill_id": skill_id, "stage": "custom", "llm_cost": 0}
        )
        llm_cost = int(meta.get("llm_cost") or 0)
        if len(planned_skill_ids) >= max_skills_per_run:
            status = "skipped"
            reason = "max_skills_per_run_reached"
        elif llm_cost > 0 and llm_steps_used + llm_cost > max_llm_steps:
            status = "skipped"
            reason = "max_llm_steps_reached"
        else:
            status = "planned"
            reason = ""
            planned_skill_ids.append(skill_id)
            llm_steps_used += llm_cost

        items.append(
            {
                "skill_id": skill_id,
                "stage": meta.get("stage", "custom"),
                "llm_cost": llm_cost,
                "status": status,
                "reason": reason,
            }
        )

    return {
        "items": items,
        "planned_skill_ids": planned_skill_ids,
        "llm_steps_used": llm_steps_used,
        "max_llm_steps": max_llm_steps,
        "max_skills_per_run": max_skills_per_run,
    }


def _resolve_rule_pack_pins(
    cfg: Dict[str, Any],
    owner_id: str,
    rule_pack_ids: List[str],
    pinned_rule_pack_pins: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    if pinned_rule_pack_pins:
        return [
            {
                "pack_id": str(item.get("pack_id") or ""),
                "display_name": str(item.get("display_name") or ""),
                "version_id": str(item.get("version_id") or ""),
                "version_no": int(item.get("version_no") or 0),
                "selector": dict(item.get("selector") or {}),
                "source_note": str(item.get("source_note") or ""),
            }
            for item in pinned_rule_pack_pins
        ]

    pins: List[Dict[str, Any]] = []
    for pack_id in rule_pack_ids:
        pack = get_rule_pack_detail(cfg, pack_id, user_id=owner_id)
        if not pack:
            continue
        versions = list_rule_pack_versions(cfg, pack_id=pack_id, user_id=owner_id)
        latest = versions[0] if versions else None
        selector = (
            dict(latest.get("selector") or {})
            if latest
            else dict(pack.get("selector") or {})
        )
        pins.append(
            {
                "pack_id": pack_id,
                "display_name": str(pack.get("display_name") or ""),
                "version_id": str(latest.get("id") or "") if latest else "",
                "version_no": int(latest.get("version_no") or 0) if latest else 0,
                "selector": selector,
                "source_note": str((latest or pack).get("source_note") or ""),
            }
        )
    return pins


def _build_evidence_pack(
    cfg: Dict[str, Any],
    contract_id: str,
    runtime_profile: Dict[str, Any],
    rule_pack_pins: List[Dict[str, Any]],
    operator_id: str,
) -> Dict[str, Any]:
    contract = get_tax_contract_document(cfg, contract_id) or {}
    matches = list_clause_rule_matches_by_contract(cfg, contract_id, limit=5000)
    clauses = list_contract_clauses(cfg, contract_id, limit=5000)
    issues = list_tax_audit_issues_by_contract(cfg, contract_id)

    clause_map = {str(item.get("id") or ""): item for item in clauses}
    issue_ids = [str(item.get("id") or "") for item in issues if item.get("id")]
    snapshot_basis = "|".join(
        [
            contract_id,
            str(runtime_profile.get("id") or ""),
            json.dumps(rule_pack_pins, ensure_ascii=False, sort_keys=True),
            ",".join(sorted(issue_ids)),
        ]
    )
    snapshot_hash = hashlib.sha256(snapshot_basis.encode("utf-8")).hexdigest()

    top_matches = []
    for item in matches[: min(20, len(matches))]:
        clause = clause_map.get(str(item.get("clause_id") or ""), {})
        evidence = _safe_json_loads(item.get("evidence_json"), {})
        top_matches.append(
            {
                "clause_id": str(item.get("clause_id") or ""),
                "rule_id": str(item.get("rule_id") or ""),
                "match_label": str(item.get("match_label") or ""),
                "match_score": float(item.get("match_score") or 0),
                "clause_path": str(clause.get("clause_path") or ""),
                "clause_text": str(clause.get("clause_text") or "")[:240],
                "reason": str(evidence.get("reason") or ""),
            }
        )

    return {
        "contract_id": contract_id,
        "contract_filename": str(contract.get("original_filename") or ""),
        "agent_profile_id": str(runtime_profile.get("id") or ""),
        "template_id": str(runtime_profile.get("template_id") or ""),
        "snapshot_hash": snapshot_hash,
        "generated_at": _utc_now_iso(),
        "rule_pack_pins": rule_pack_pins,
        "summary": {
            "clause_count": len(clauses),
            "match_count": len(matches),
            "issue_count": len(issues),
            "selected_match_count": len(top_matches),
        },
        "top_matches": top_matches,
        "operator_id": operator_id,
    }


def _persist_runtime_artifacts(
    cfg: Dict[str, Any],
    contract_id: str,
    operator_id: str,
    runtime_meta: Dict[str, Any],
    evidence_pack: Dict[str, Any],
) -> Dict[str, int]:
    issues = list_tax_audit_issues_by_contract(cfg, contract_id)
    matches = list_clause_rule_matches_by_contract(cfg, contract_id, limit=5000)
    clauses = list_contract_clauses(cfg, contract_id, limit=5000)
    clause_map = {str(item.get("id") or ""): item for item in clauses}
    match_map = {str(item.get("clause_id") or ""): item for item in matches}

    clear_evidence_anchors_by_contract(cfg, contract_id)
    trace_count = 0
    anchor_count = 0

    for issue in issues:
        clause_id = str(issue.get("clause_id") or "")
        match_item = match_map.get(clause_id, {})
        clause = clause_map.get(clause_id, {})
        evidence = _safe_json_loads(match_item.get("evidence_json"), {})

        payload = {
            "agent_profile_id": runtime_meta.get("agent_profile_id", ""),
            "template_id": runtime_meta.get("template_id", ""),
            "planned_skill_ids": runtime_meta.get("planned_skill_ids", []),
            "rule_pack_pins": runtime_meta.get("rule_pack_pins", []),
            "session_id": runtime_meta.get("session_id", ""),
            "evidence_pack_snapshot_hash": evidence_pack.get("snapshot_hash", ""),
            "evidence_pack_summary": evidence_pack.get("summary", {}),
            "issue_id": str(issue.get("id") or ""),
        }
        insert_audit_trace(
            cfg,
            issue_id=str(issue.get("id") or ""),
            action_type="agent_runtime_profile",
            operator=operator_id,
            payload_json=_json_text(payload),
            created_by=operator_id,
        )
        trace_count += 1

        quote_text = str(
            evidence.get("clause_excerpt") or clause.get("clause_text") or ""
        )[:500]
        if quote_text:
            create_evidence_anchor(
                cfg,
                contract_document_id=contract_id,
                issue_id=str(issue.get("id") or ""),
                snapshot_hash=str(evidence_pack.get("snapshot_hash") or ""),
                locator_type="clause_excerpt",
                quote_text=quote_text,
                created_by=operator_id,
                page_no=clause.get("page_no"),
                paragraph_no=clause.get("paragraph_no"),
                clause_id=clause_id,
                clause_path=clause.get("clause_path"),
                context_before=str(evidence.get("reason") or "")[:300],
                context_after=str(evidence.get("rule_excerpt") or "")[:300],
                confidence=float(match_item.get("match_score") or 0),
            )
            anchor_count += 1

    return {"trace_count": trace_count, "evidence_anchor_count": anchor_count}


def _create_runtime_session(
    cfg: Dict[str, Any],
    *,
    contract_id: str,
    owner_id: str,
    operator_id: str,
    profile_id: str,
    template_id: str,
    request_payload: Dict[str, Any],
    replay_of_session_id: str = "",
) -> str:
    session_id = str(uuid.uuid4())
    now = _utc_now_iso()
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO audit_runtime_session(
            id, contract_document_id, owner_id, operator_id, agent_profile_id,
            template_id, replay_of_session_id, sandbox_mode, status, request_json,
            runtime_json, result_json, error_message, started_at, finished_at,
            created_at, updated_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            session_id,
            contract_id,
            owner_id,
            operator_id,
            profile_id,
            template_id,
            replay_of_session_id or None,
            SANDBOX_MODE,
            "running",
            _json_text(request_payload),
            None,
            None,
            None,
            now,
            None,
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()
    return session_id


def _update_runtime_session(
    cfg: Dict[str, Any],
    session_id: str,
    *,
    status: Optional[str] = None,
    runtime: Optional[Dict[str, Any]] = None,
    result: Optional[Dict[str, Any]] = None,
    error_message: Optional[str] = None,
    finished: bool = False,
) -> None:
    updates: Dict[str, Any] = {"updated_at": _utc_now_iso()}
    if status is not None:
        updates["status"] = status
    if runtime is not None:
        updates["runtime_json"] = _json_text(runtime)
    if result is not None:
        updates["result_json"] = _json_text(result)
    if error_message is not None:
        updates["error_message"] = error_message
    if finished:
        updates["finished_at"] = _utc_now_iso()

    set_clause = ", ".join(f"{column}=?" for column in updates.keys())
    params = list(updates.values()) + [session_id]
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        f"UPDATE audit_runtime_session SET {set_clause} WHERE id=?",
        params,
    )
    conn.commit()
    conn.close()


def _create_skill_runs(cfg: Dict[str, Any], session_id: str, skill_items: List[Dict[str, Any]]) -> None:
    now = _utc_now_iso()
    conn = get_conn(cfg)
    cur = conn.cursor()
    for index, item in enumerate(skill_items, start=1):
        initial_status = "skipped" if item.get("status") == "skipped" else "pending"
        cur.execute(
            """
            INSERT INTO audit_runtime_skill_run(
                id, session_id, skill_id, stage, sandbox_mode, status, llm_cost,
                position_no, reason, input_summary_json, output_summary_json,
                error_message, started_at, finished_at, created_at, updated_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                str(uuid.uuid4()),
                session_id,
                str(item.get("skill_id") or ""),
                str(item.get("stage") or "custom"),
                SANDBOX_MODE,
                initial_status,
                int(item.get("llm_cost") or 0),
                index,
                str(item.get("reason") or ""),
                None,
                None,
                None,
                now,
                now if initial_status == "skipped" else None,
                now,
                now,
            ),
        )
    conn.commit()
    conn.close()


def _update_skill_run(
    cfg: Dict[str, Any],
    session_id: str,
    position_no: int,
    *,
    status: Optional[str] = None,
    input_summary: Optional[Dict[str, Any]] = None,
    output_summary: Optional[Dict[str, Any]] = None,
    error_message: Optional[str] = None,
    reason: Optional[str] = None,
    finished: bool = False,
) -> None:
    updates: Dict[str, Any] = {"updated_at": _utc_now_iso()}
    if status is not None:
        updates["status"] = status
    if input_summary is not None:
        updates["input_summary_json"] = _json_text(input_summary)
    if output_summary is not None:
        updates["output_summary_json"] = _json_text(output_summary)
    if error_message is not None:
        updates["error_message"] = error_message
    if reason is not None:
        updates["reason"] = reason
    if finished:
        updates["finished_at"] = _utc_now_iso()

    set_clause = ", ".join(f"{column}=?" for column in updates.keys())
    params = list(updates.values()) + [session_id, int(position_no)]
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        f"""
        UPDATE audit_runtime_skill_run
        SET {set_clause}
        WHERE session_id=? AND position_no=?
        """,
        params,
    )
    conn.commit()
    conn.close()


def list_runtime_sessions(
    cfg: Dict[str, Any],
    *,
    owner_id: str,
    contract_id: str = "",
    limit: int = 50,
) -> List[Dict[str, Any]]:
    conn = get_conn(cfg)
    cur = conn.cursor()
    clauses = ["owner_id=?"]
    params: List[Any] = [owner_id]
    if contract_id:
        clauses.append("contract_document_id=?")
        params.append(contract_id)
    params.append(max(1, int(limit)))
    cur.execute(
        f"""
        SELECT *
        FROM audit_runtime_session
        WHERE {' AND '.join(clauses)}
        ORDER BY started_at DESC
        LIMIT ?
        """,
        params,
    )
    rows = cur.fetchall()
    conn.close()
    return [_row_to_runtime_session(dict(row)) for row in rows]


def get_runtime_session(
    cfg: Dict[str, Any],
    *,
    session_id: str,
    owner_id: str,
) -> Optional[Dict[str, Any]]:
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM audit_runtime_session
        WHERE id=? AND owner_id=?
        """,
        (session_id, owner_id),
    )
    row = cur.fetchone()
    conn.close()
    return _row_to_runtime_session(dict(row)) if row else None


def list_runtime_skill_runs(
    cfg: Dict[str, Any],
    *,
    session_id: str,
    owner_id: str,
) -> List[Dict[str, Any]]:
    session = get_runtime_session(cfg, session_id=session_id, owner_id=owner_id)
    if not session:
        return []
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM audit_runtime_skill_run
        WHERE session_id=?
        ORDER BY position_no ASC
        """,
        (session_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return [_row_to_skill_run(dict(row)) for row in rows]


def _execute_controlled_skill_runs(
    cfg: Dict[str, Any],
    services: Any,
    *,
    session_id: str,
    contract_id: str,
    operator_id: str,
    profile: Dict[str, Any],
    template: Dict[str, Any],
    skill_items: List[Dict[str, Any]],
    rule_pack_pins: List[Dict[str, Any]],
    top_k_per_clause: int,
    include_report: bool,
) -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "analyze": None,
        "match": None,
        "issues": None,
        "evidence_pack": None,
        "report": None,
    }
    rule_pack_selectors = [dict(item.get("selector") or {}) for item in rule_pack_pins]

    for index, item in enumerate(skill_items, start=1):
        if item.get("status") == "skipped":
            continue

        stage = str(item.get("stage") or "custom")
        skill_id = str(item.get("skill_id") or "")
        input_summary = {
            "contract_id": contract_id,
            "stage": stage,
            "selector_count": len(rule_pack_selectors),
            "top_k_per_clause": int(top_k_per_clause),
            "include_report": bool(include_report),
        }
        _update_skill_run(
            cfg,
            session_id,
            index,
            status="running",
            input_summary=input_summary,
        )

        try:
            if stage == "analyze":
                if state["analyze"] is None:
                    state["analyze"] = tax_analyze_contract(
                        cfg,
                        services,
                        contract_id=contract_id,
                        operator_id=operator_id,
                    )
                output = state["analyze"]
            elif stage == "match":
                if state["match"] is None:
                    state["match"] = tax_match_contract(
                        cfg,
                        services,
                        contract_id=contract_id,
                        operator_id=operator_id,
                        top_k_per_clause=top_k_per_clause,
                        rule_pack_selectors=rule_pack_selectors,
                    )
                output = state["match"]
            elif stage == "issues":
                if state["issues"] is None:
                    state["issues"] = tax_generate_issues(
                        cfg,
                        services,
                        contract_id=contract_id,
                        operator_id=operator_id,
                    )
                output = state["issues"]
            elif stage == "evidence_pack":
                if state["issues"] is None:
                    state["issues"] = tax_generate_issues(
                        cfg,
                        services,
                        contract_id=contract_id,
                        operator_id=operator_id,
                    )
                if state["evidence_pack"] is None:
                    state["evidence_pack"] = _build_evidence_pack(
                        cfg,
                        contract_id=contract_id,
                        runtime_profile=profile,
                        rule_pack_pins=rule_pack_pins,
                        operator_id=operator_id,
                    )
                output = state["evidence_pack"]
            elif stage == "report":
                if include_report:
                    if state["report"] is None:
                        state["report"] = tax_build_report(cfg, contract_id=contract_id)
                    output = state["report"]
                else:
                    output = {"contract_id": contract_id, "skipped": True}
            else:
                _update_skill_run(
                    cfg,
                    session_id,
                    index,
                    status="blocked",
                    reason="sandbox_executor_not_registered",
                    output_summary={"stage": stage, "blocked": True},
                    finished=True,
                )
                continue

            _update_skill_run(
                cfg,
                session_id,
                index,
                status="completed",
                output_summary=_summarize_value(output),
                finished=True,
            )
        except Exception as exc:
            _update_skill_run(
                cfg,
                session_id,
                index,
                status="failed",
                error_message=str(exc),
                finished=True,
            )
            raise

    if state["issues"] is None:
        state["issues"] = _zero_issue_summary(contract_id)
    return state


def _build_runtime_payload(
    cfg: Dict[str, Any],
    *,
    session_id: str,
    profile: Dict[str, Any],
    template: Dict[str, Any],
    skill_plan: Dict[str, Any],
    rule_pack_pins: List[Dict[str, Any]],
    evidence_pack: Optional[Dict[str, Any]],
    artifact_summary: Dict[str, int],
) -> Dict[str, Any]:
    contract_id = str((evidence_pack or {}).get("contract_id") or "")
    if not contract_id:
        contract_traces = []
        evidence_anchors = []
    else:
        contract_traces = list_audit_trace_by_contract(cfg, contract_id, limit=5000)
        evidence_anchors = list_evidence_anchors_by_contract(cfg, contract_id, limit=5000)
    return {
        "session_id": session_id,
        "sandbox_mode": SANDBOX_MODE,
        "agent_profile_id": str(profile.get("id") or ""),
        "agent_profile_name": str(profile.get("display_name") or ""),
        "template_id": str(profile.get("template_id") or template.get("id") or ""),
        "system_prompt": str(profile.get("system_prompt") or ""),
        "planned_skill_ids": list(skill_plan.get("planned_skill_ids") or []),
        "skill_plan": list(skill_plan.get("items") or []),
        "rule_pack_pins": rule_pack_pins,
        "evidence_pack": evidence_pack or None,
        "llm_steps_used": int(skill_plan.get("llm_steps_used") or 0),
        "max_llm_steps": int(skill_plan.get("max_llm_steps") or 0),
        "trace_count": len(contract_traces),
        "evidence_anchor_count": len(evidence_anchors),
        "artifact_delta": artifact_summary,
    }


def run_tax_pipeline_with_runtime(
    cfg: Dict[str, Any],
    services: Any,
    *,
    contract_id: str,
    owner_id: str,
    operator_id: str = "",
    profile_id: str = "",
    top_k_per_clause: int = 5,
    include_report: bool = True,
    replay_of_session_id: str = "",
    pinned_rule_pack_pins: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    runtime_ctx = _resolve_runtime_profile(cfg, owner_id=owner_id, profile_id=profile_id)
    profile = runtime_ctx["profile"]
    template = runtime_ctx["template"]
    skill_plan = _build_skill_plan(profile, template)
    rule_pack_pins = _resolve_rule_pack_pins(
        cfg,
        owner_id=owner_id,
        rule_pack_ids=list(profile.get("enabled_rule_pack_ids") or []),
        pinned_rule_pack_pins=pinned_rule_pack_pins,
    )
    request_payload = {
        "contract_id": contract_id,
        "profile_id": str(profile.get("id") or ""),
        "top_k_per_clause": int(top_k_per_clause),
        "include_report": bool(include_report),
    }
    session_id = _create_runtime_session(
        cfg,
        contract_id=contract_id,
        owner_id=owner_id,
        operator_id=operator_id,
        profile_id=str(profile.get("id") or ""),
        template_id=str(profile.get("template_id") or template.get("id") or ""),
        request_payload=request_payload,
        replay_of_session_id=replay_of_session_id,
    )
    _create_skill_runs(cfg, session_id, list(skill_plan.get("items") or []))

    try:
        state = _execute_controlled_skill_runs(
            cfg,
            services,
            session_id=session_id,
            contract_id=contract_id,
            operator_id=operator_id,
            profile=profile,
            template=template,
            skill_items=list(skill_plan.get("items") or []),
            rule_pack_pins=rule_pack_pins,
            top_k_per_clause=top_k_per_clause,
            include_report=include_report,
        )
        evidence_pack = state.get("evidence_pack") or None
        artifact_summary = {"trace_count": 0, "evidence_anchor_count": 0}
        if evidence_pack and (state.get("issues") or {}).get("total", 0) > 0:
            artifact_summary = _persist_runtime_artifacts(
                cfg,
                contract_id=contract_id,
                operator_id=operator_id,
                runtime_meta={
                    "session_id": session_id,
                    "agent_profile_id": str(profile.get("id") or ""),
                    "template_id": str(profile.get("template_id") or template.get("id") or ""),
                    "planned_skill_ids": list(skill_plan.get("planned_skill_ids") or []),
                    "rule_pack_pins": rule_pack_pins,
                },
                evidence_pack=evidence_pack,
            )

        runtime = _build_runtime_payload(
            cfg,
            session_id=session_id,
            profile=profile,
            template=template,
            skill_plan=skill_plan,
            rule_pack_pins=rule_pack_pins,
            evidence_pack=evidence_pack,
            artifact_summary=artifact_summary,
        )
        result = {
            "contract_id": contract_id,
            "analyze": state.get("analyze"),
            "match": state.get("match"),
            "issues": state.get("issues"),
            "report": state.get("report"),
            "runtime": runtime,
        }
        _update_runtime_session(
            cfg,
            session_id,
            status="completed",
            runtime=runtime,
            result=result,
            finished=True,
        )
        return result
    except Exception as exc:
        _update_runtime_session(
            cfg,
            session_id,
            status="failed",
            error_message=str(exc),
            finished=True,
        )
        raise


def replay_runtime_session(
    cfg: Dict[str, Any],
    services: Any,
    *,
    session_id: str,
    owner_id: str,
    operator_id: str = "",
) -> Dict[str, Any]:
    session = get_runtime_session(cfg, session_id=session_id, owner_id=owner_id)
    if not session:
        raise ValueError("runtime session not found")
    request_payload = dict(session.get("request") or {})
    runtime_payload = dict(session.get("runtime") or {})
    return run_tax_pipeline_with_runtime(
        cfg,
        services,
        contract_id=str(session.get("contract_document_id") or request_payload.get("contract_id") or ""),
        owner_id=owner_id,
        operator_id=operator_id,
        profile_id=str(request_payload.get("profile_id") or ""),
        top_k_per_clause=int(request_payload.get("top_k_per_clause") or 5),
        include_report=bool(request_payload.get("include_report", True)),
        replay_of_session_id=session_id,
        pinned_rule_pack_pins=list(runtime_payload.get("rule_pack_pins") or []),
    )
