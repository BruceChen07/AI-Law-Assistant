import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.database import get_conn

logger = logging.getLogger("law_assistant")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _safe_json_loads(value: Any, default: Any):
    if value in {None, ""}:
        return default
    try:
        return json.loads(str(value))
    except Exception:
        return default


def _normalize_string_list(value: Any, field_name: str) -> Optional[List[str]]:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    items: List[str] = []
    seen = set()
    for raw_item in value:
        item = str(raw_item or "").strip()
        if not item:
            raise ValueError(f"{field_name} contains empty item")
        if item in seen:
            continue
        seen.add(item)
        items.append(item)
    return items


def _validate_agent_limits(max_llm_steps: Optional[int], max_skills_per_run: Optional[int]) -> None:
    if max_llm_steps is not None:
        steps = int(max_llm_steps)
        if steps < 1 or steps > 10:
            raise ValueError("max_llm_steps must be between 1 and 10")
    if max_skills_per_run is not None:
        skills = int(max_skills_per_run)
        if skills < 1 or skills > 20:
            raise ValueError("max_skills_per_run must be between 1 and 20")


def _validate_agent_bindings(
    cfg: Dict[str, Any],
    owner_id: str,
    scene: str,
    enabled_skill_ids: Optional[List[str]],
    enabled_rule_pack_ids: Optional[List[str]],
) -> None:
    if enabled_skill_ids is not None:
        if not enabled_skill_ids:
            raise ValueError("enabled_skill_ids must not be empty")
        valid_skill_ids = {
            item["id"] for item in list_visible_skills(cfg, user_id=owner_id, scene=scene)
        }
        invalid_skills = [
            item for item in enabled_skill_ids if item not in valid_skill_ids]
        if invalid_skills:
            raise ValueError(f"invalid skill ids: {', '.join(invalid_skills)}")

    if enabled_rule_pack_ids is not None:
        valid_rule_pack_ids = {
            item["id"] for item in list_rule_packs(cfg, user_id=owner_id, scene=scene)
        }
        invalid_rule_packs = [
            item for item in enabled_rule_pack_ids if item not in valid_rule_pack_ids
        ]
        if invalid_rule_packs:
            raise ValueError(
                f"invalid rule pack ids: {', '.join(invalid_rule_packs)}")


def _validate_agent_runtime_combination(
    enabled_skill_ids: List[str],
    max_skills_per_run: Optional[int],
) -> None:
    if not enabled_skill_ids:
        raise ValueError("enabled_skill_ids must not be empty")
    if max_skills_per_run is not None and int(max_skills_per_run) > len(enabled_skill_ids):
        raise ValueError(
            "max_skills_per_run cannot exceed enabled skill count")


def _normalize_rule_selector(selector: Any) -> Dict[str, Any]:
    if selector in (None, ""):
        return {}
    if not isinstance(selector, dict):
        raise ValueError("selector must be an object")
    normalized = dict(selector)
    if "rule_types" in normalized:
        normalized["rule_types"] = _normalize_string_list(
            normalized.get("rule_types") or [], "selector.rule_types"
        ) or []
    return normalized


BUILTIN_SKILL_SEEDS: List[Dict[str, Any]] = [
    {
        "id": "china_tax_law_knowledge",
        "display_name": "中国财税法律知识",
        "category": "knowledge",
        "scene": "tax_contract_audit",
        "description": "基于中国财税法律知识的审计辅助 skill，用于税法咨询、税收政策解释、税务合规审查。",
        "source_url": "https://clawhub.ai/codefarmerman/skills/china-tax-law",
        "reference_summary": "参考外部 skill 的法规优先、风险提示、税种分类和税务争议处理流程。",
        "tags_json": _json_text(["tax", "law", "compliance", "knowledge"]),
        "input_schema_json": _json_text(
            {"type": "object", "properties": {"question": {
                "type": "string"}, "jurisdiction": {"type": "string"}}}
        ),
        "output_schema_json": _json_text(
            {"type": "object", "properties": {"answer": {
                "type": "string"}, "citations": {"type": "array"}}}
        ),
        "config_schema_json": _json_text({"type": "object", "properties": {"citation_required": {"type": "boolean"}}}),
        "sort_order": 10,
    },
    {
        "id": "receipt_assistant",
        "display_name": "票据识别与报销助手",
        "category": "document_processing",
        "scene": "receipt_audit",
        "description": "用于发票、行程单、火车票等票据识别、信息提取、命名和汇总。",
        "source_url": "https://clawhub.ai/yy-c8/skills/receipt-assistant",
        "reference_summary": "参考外部 skill 的票据识别、字段提取、重命名规则和报表输出流程。",
        "tags_json": _json_text(["receipt", "invoice", "ocr", "expense"]),
        "input_schema_json": _json_text(
            {"type": "object", "properties": {"file_path": {
                "type": "string"}, "doc_type": {"type": "string"}}}
        ),
        "output_schema_json": _json_text(
            {"type": "object", "properties": {"ticket_type": {
                "type": "string"}, "fields": {"type": "object"}}}
        ),
        "config_schema_json": _json_text({"type": "object", "properties": {"output_dir": {"type": "string"}}}),
        "sort_order": 20,
    },
    {
        "id": "tax_digital_localization",
        "display_name": "财税数字化本地化",
        "category": "localization",
        "scene": "tax_digital_localization",
        "description": "用于财税系统术语、发票与申报相关界面文案的本地化翻译与校准。",
        "source_url": "https://clawhub.ai/munich949/skills/tax-digital-localization",
        "reference_summary": "参考外部 skill 的高风险术语校准、国家 locale 映射和税务术语保真策略。",
        "tags_json": _json_text(["i18n", "tax", "invoice", "localization"]),
        "input_schema_json": _json_text(
            {"type": "object", "properties": {"target_country": {
                "type": "string"}, "texts": {"type": "array"}}}
        ),
        "output_schema_json": _json_text(
            {"type": "object", "properties": {"translations": {
                "type": "array"}, "risk_level": {"type": "string"}}}
        ),
        "config_schema_json": _json_text({"type": "object", "properties": {"preserve_placeholders": {"type": "boolean"}}}),
        "sort_order": 30,
    },
    {
        "id": "entity_extract_skill",
        "display_name": "审计实体抽取",
        "category": "audit_pipeline",
        "scene": "tax_contract_audit",
        "description": "从合同条款中提取税种、主体、金额、税率、时限等审计实体。",
        "reference_summary": "对应当前 tax_contract_parser 的实体抽取能力。",
        "tags_json": _json_text(["audit", "entity", "extraction"]),
        "input_schema_json": _json_text({"type": "object", "properties": {"contract_text": {"type": "string"}}}),
        "output_schema_json": _json_text({"type": "object", "properties": {"entities": {"type": "array"}}}),
        "config_schema_json": _json_text({"type": "object", "properties": {"language": {"type": "string"}}}),
        "sort_order": 100,
    },
    {
        "id": "rule_precheck_skill",
        "display_name": "规则预检查",
        "category": "audit_pipeline",
        "scene": "tax_contract_audit",
        "description": "在进入大模型前，使用规则引擎进行确定性预判和规则筛选。",
        "reference_summary": "对应当前 rule_engine 和 tax_matcher 的硬规则预判思路。",
        "tags_json": _json_text(["rule", "precheck", "audit"]),
        "input_schema_json": _json_text(
            {"type": "object", "properties": {"entities": {
                "type": "object"}, "rule_pack_id": {"type": "string"}}}
        ),
        "output_schema_json": _json_text(
            {"type": "object", "properties": {"matched_rules": {
                "type": "array"}, "violations": {"type": "array"}}}
        ),
        "config_schema_json": _json_text({"type": "object", "properties": {"strict_mode": {"type": "boolean"}}}),
        "sort_order": 110,
    },
    {
        "id": "clause_match_skill",
        "display_name": "条款规则匹配",
        "category": "audit_pipeline",
        "scene": "tax_contract_audit",
        "description": "将合同条款与法规规则进行匹配，并输出命中标签、分数和证据。",
        "reference_summary": "对应当前 tax_matcher 的混合匹配能力。",
        "tags_json": _json_text(["match", "clause", "rule", "audit"]),
        "input_schema_json": _json_text(
            {"type": "object", "properties": {"clause_text": {
                "type": "string"}, "rule_id": {"type": "string"}}}
        ),
        "output_schema_json": _json_text(
            {
                "type": "object",
                "properties": {
                    "match_label": {"type": "string"},
                    "match_score": {"type": "number"},
                    "evidence": {"type": "array"},
                },
            }
        ),
        "config_schema_json": _json_text({"type": "object", "properties": {"min_confidence": {"type": "number"}}}),
        "sort_order": 120,
    },
    {
        "id": "evidence_pack_skill",
        "display_name": "证据包构建",
        "category": "audit_pipeline",
        "scene": "tax_contract_audit",
        "description": "将规则命中、条款摘录、法规依据和上下文压缩成给 35B 使用的证据包。",
        "reference_summary": "用于替代重型 memory，把上下文控制在本地模型可接受范围内。",
        "tags_json": _json_text(["evidence", "context", "audit"]),
        "input_schema_json": _json_text({"type": "object", "properties": {"matches": {"type": "array"}}}),
        "output_schema_json": _json_text({"type": "object", "properties": {"evidence_pack": {"type": "object"}}}),
        "config_schema_json": _json_text({"type": "object", "properties": {"max_chars": {"type": "integer"}}}),
        "sort_order": 130,
    },
    {
        "id": "final_review_skill",
        "display_name": "最终复核",
        "category": "audit_pipeline",
        "scene": "tax_contract_audit",
        "description": "由主模型对证据包和规则命中结果做最终裁决与解释。",
        "reference_summary": "对应当前 tax_risk / contract_audit 中的高复杂度推理部分。",
        "tags_json": _json_text(["review", "judge", "llm"]),
        "input_schema_json": _json_text({"type": "object", "properties": {"evidence_pack": {"type": "object"}}}),
        "output_schema_json": _json_text({"type": "object", "properties": {"findings": {"type": "array"}}}),
        "config_schema_json": _json_text({"type": "object", "properties": {"max_tokens": {"type": "integer"}}}),
        "sort_order": 140,
    },
    {
        "id": "compliance_summary_skill",
        "display_name": "合规摘要输出",
        "category": "audit_pipeline",
        "scene": "tax_contract_audit",
        "description": "生成结构化审计摘要、风险等级、修改建议与法规引用。",
        "reference_summary": "用于形成最终报告结构，便于前端展示与导出。",
        "tags_json": _json_text(["summary", "report", "compliance"]),
        "input_schema_json": _json_text({"type": "object", "properties": {"findings": {"type": "array"}}}),
        "output_schema_json": _json_text({"type": "object", "properties": {"report": {"type": "object"}}}),
        "config_schema_json": _json_text({"type": "object", "properties": {"locale": {"type": "string"}}}),
        "sort_order": 150,
    },
]

BUILTIN_RULE_PACK_SEEDS: List[Dict[str, Any]] = [
    {
        "id": "tax_core_default",
        "display_name": "默认财税合同审计规则包",
        "scene": "tax_contract_audit",
        "description": "映射当前 tax_rule 表中的全量规则，作为第一阶段的默认规则集合。",
        "selector_json": _json_text({}),
        "source_note": "当前版本直接映射 tax_rule 全量记录，后续阶段再细分为多规则包。",
        "sort_order": 10,
    },
    {
        "id": "invoice_receipt_default",
        "display_name": "票据与发票规则包",
        "scene": "receipt_audit",
        "description": "面向票据识别、发票字段检查和报销合规性检查的规则包占位。",
        "selector_json": _json_text({"rule_types": ["invoice", "vat", "receipt"]}),
        "source_note": "第一阶段先提供可见化元数据，后续绑定更细颗粒度规则。",
        "sort_order": 20,
    },
]

BUILTIN_TEMPLATE_SEEDS: List[Dict[str, Any]] = [
    {
        "id": "tax_contract_audit_default",
        "display_name": "税务合同审计默认模板",
        "scene": "tax_contract_audit",
        "description": "第一阶段默认审计模板，聚焦条款抽取、规则预判、证据包与最终复核。",
        "skill_ids_json": _json_text(
            [
                "entity_extract_skill",
                "rule_precheck_skill",
                "clause_match_skill",
                "evidence_pack_skill",
                "final_review_skill",
                "compliance_summary_skill",
            ]
        ),
        "rule_pack_ids_json": _json_text(["tax_core_default"]),
        "max_llm_steps": 2,
        "max_skills_per_run": 6,
        "sort_order": 10,
    }
]


def ensure_audit_capabilities_seeded(cfg: Dict[str, Any]) -> None:
    now = _utc_now_iso()
    conn = get_conn(cfg)
    cur = conn.cursor()
    try:
        for item in BUILTIN_SKILL_SEEDS:
            cur.execute(
                """
                INSERT OR IGNORE INTO audit_skill(
                    id, display_name, category, scene, description, owner_type, owner_id,
                    visibility, status, source_url, reference_summary,
                    input_schema_json, output_schema_json, config_schema_json, tags_json,
                    sort_order, created_at, updated_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    item["id"],
                    item["display_name"],
                    item["category"],
                    item["scene"],
                    item.get("description", ""),
                    "system",
                    None,
                    "public",
                    "active",
                    item.get("source_url", ""),
                    item.get("reference_summary", ""),
                    item.get("input_schema_json", ""),
                    item.get("output_schema_json", ""),
                    item.get("config_schema_json", ""),
                    item.get("tags_json", "[]"),
                    int(item.get("sort_order", 100)),
                    now,
                    now,
                ),
            )

        for item in BUILTIN_RULE_PACK_SEEDS:
            cur.execute(
                """
                INSERT OR IGNORE INTO audit_rule_pack(
                    id, display_name, scene, description, owner_type, owner_id,
                    visibility, status, selector_json, source_note, sort_order,
                    created_at, updated_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    item["id"],
                    item["display_name"],
                    item["scene"],
                    item.get("description", ""),
                    "system",
                    None,
                    "public",
                    "active",
                    item.get("selector_json", "{}"),
                    item.get("source_note", ""),
                    int(item.get("sort_order", 100)),
                    now,
                    now,
                ),
            )

        for item in BUILTIN_TEMPLATE_SEEDS:
            cur.execute(
                """
                INSERT OR IGNORE INTO audit_template(
                    id, display_name, scene, description, owner_type, owner_id,
                    visibility, status, skill_ids_json, rule_pack_ids_json,
                    max_llm_steps, max_skills_per_run, sort_order, created_at, updated_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    item["id"],
                    item["display_name"],
                    item["scene"],
                    item.get("description", ""),
                    "system",
                    None,
                    "public",
                    "active",
                    item.get("skill_ids_json", "[]"),
                    item.get("rule_pack_ids_json", "[]"),
                    int(item.get("max_llm_steps", 2)),
                    int(item.get("max_skills_per_run", 6)),
                    int(item.get("sort_order", 100)),
                    now,
                    now,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _row_to_skill(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    item["tags"] = _safe_json_loads(item.pop("tags_json", "[]"), [])
    item["input_schema"] = _safe_json_loads(
        item.pop("input_schema_json", "{}"), {})
    item["output_schema"] = _safe_json_loads(
        item.pop("output_schema_json", "{}"), {})
    item["config_schema"] = _safe_json_loads(
        item.pop("config_schema_json", "{}"), {})
    return item


def _row_to_rule_pack(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    item["selector"] = _safe_json_loads(item.pop("selector_json", "{}"), {})
    item["rule_count"] = int(item.get("rule_count") or 0)
    return item


def _row_to_rule_pack_draft(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    item["selector"] = _safe_json_loads(item.pop("selector_json", "{}"), {})
    if item.get("published_version_no") is not None:
        item["published_version_no"] = int(
            item.get("published_version_no") or 0)
    return item


def _row_to_rule_pack_version(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    item["selector"] = _safe_json_loads(item.pop("selector_json", "{}"), {})
    item["version_no"] = int(item.get("version_no") or 0)
    return item


def _row_to_template(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    item["skill_ids"] = _safe_json_loads(item.pop("skill_ids_json", "[]"), [])
    item["rule_pack_ids"] = _safe_json_loads(
        item.pop("rule_pack_ids_json", "[]"), [])
    return item


def list_visible_skills(
    cfg: Dict[str, Any],
    user_id: str = "",
    scene: str = "",
    category: str = "",
) -> List[Dict[str, Any]]:
    ensure_audit_capabilities_seeded(cfg)
    conn = get_conn(cfg)
    cur = conn.cursor()
    clauses = ["status='active'"]
    params: List[Any] = []
    if scene:
        clauses.append("scene=?")
        params.append(scene)
    if category:
        clauses.append("category=?")
        params.append(category)
    clauses.append("(visibility='public' OR owner_id=?)")
    params.append(user_id)
    sql = f"""
        SELECT *
        FROM audit_skill
        WHERE {' AND '.join(clauses)}
        ORDER BY sort_order ASC, display_name ASC
    """
    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()
    return [_row_to_skill(dict(row)) for row in rows]


def get_skill_detail(cfg: Dict[str, Any], skill_id: str, user_id: str = "") -> Optional[Dict[str, Any]]:
    ensure_audit_capabilities_seeded(cfg)
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM audit_skill
        WHERE id=? AND status='active' AND (visibility='public' OR owner_id=?)
        """,
        (skill_id, user_id),
    )
    row = cur.fetchone()
    conn.close()
    return _row_to_skill(dict(row)) if row else None


_SKILL_STATUS_VALUES = {"active", "disabled", "deleted"}
_SKILL_VISIBILITY_VALUES = {"public", "private"}
_SKILL_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")


def _slugify_skill_id(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "").strip().lower())
    text = re.sub(r"-{2,}", "-", text).strip("-_")
    return text[:64]


def _normalize_skill_id(raw_id: str, display_name: str) -> str:
    candidate = str(raw_id or "").strip().lower()
    if not candidate:
        candidate = _slugify_skill_id(display_name)
    if not candidate:
        raise ValueError("skill id is required")
    if not _SKILL_ID_PATTERN.match(candidate):
        raise ValueError(
            "skill id must be 3-64 chars and contain only lowercase letters, numbers, underscores, or hyphens"
        )
    return candidate


def _normalize_json_schema(value: Any, field_name: str) -> Dict[str, Any]:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object")
    return value


def _normalize_skill_payload(
    payload: Dict[str, Any],
    existing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    current = existing or {}
    display_name = str(
        payload.get("display_name", current.get("display_name", "")) or ""
    ).strip()
    category = str(payload.get(
        "category", current.get("category", "")) or "").strip()
    scene = str(payload.get("scene", current.get("scene", "")) or "").strip()
    description = str(
        payload.get("description", current.get("description", "")) or ""
    ).strip()
    source_url = str(payload.get(
        "source_url", current.get("source_url", "")) or "").strip()
    reference_summary = str(
        payload.get("reference_summary", current.get(
            "reference_summary", "")) or ""
    ).strip()
    visibility = str(
        payload.get("visibility", current.get(
            "visibility", "public")) or "public"
    ).strip().lower()
    status = str(payload.get("status", current.get(
        "status", "active")) or "active").strip().lower()
    sort_order_raw = payload.get("sort_order", current.get("sort_order", 100))
    tags = _normalize_string_list(
        payload.get("tags", current.get("tags", [])),
        "tags",
    ) or []
    input_schema = _normalize_json_schema(
        payload.get("input_schema", current.get("input_schema", {})),
        "input_schema",
    )
    output_schema = _normalize_json_schema(
        payload.get("output_schema", current.get("output_schema", {})),
        "output_schema",
    )
    config_schema = _normalize_json_schema(
        payload.get("config_schema", current.get("config_schema", {})),
        "config_schema",
    )

    if not display_name:
        raise ValueError("display_name is required")
    if len(display_name) > 120:
        raise ValueError("display_name must be <= 120 chars")
    if not category:
        raise ValueError("category is required")
    if len(category) > 60:
        raise ValueError("category must be <= 60 chars")
    if not scene:
        raise ValueError("scene is required")
    if len(scene) > 80:
        raise ValueError("scene must be <= 80 chars")
    if visibility not in _SKILL_VISIBILITY_VALUES:
        raise ValueError("visibility must be public or private")
    if status not in _SKILL_STATUS_VALUES:
        raise ValueError("status must be active, disabled, or deleted")

    try:
        sort_order = int(sort_order_raw)
    except Exception as exc:
        raise ValueError("sort_order must be an integer") from exc
    if sort_order < 0 or sort_order > 100000:
        raise ValueError("sort_order must be between 0 and 100000")

    return {
        "display_name": display_name,
        "category": category,
        "scene": scene,
        "description": description,
        "source_url": source_url,
        "reference_summary": reference_summary,
        "visibility": visibility,
        "status": status,
        "sort_order": sort_order,
        "tags": tags,
        "input_schema": input_schema,
        "output_schema": output_schema,
        "config_schema": config_schema,
    }


def _get_skill_reference_counts(cur, skill_id: str) -> Dict[str, int]:
    token = f'"{skill_id}"'
    cur.execute(
        """
        SELECT COUNT(1)
        FROM audit_template
        WHERE status <> 'deleted' AND skill_ids_json LIKE ?
        """,
        (f"%{token}%",),
    )
    template_ref_count = int((cur.fetchone() or [0])[0] or 0)
    cur.execute(
        """
        SELECT COUNT(1)
        FROM agent_profile
        WHERE status <> 'deleted' AND enabled_skill_ids_json LIKE ?
        """,
        (f"%{token}%",),
    )
    agent_ref_count = int((cur.fetchone() or [0])[0] or 0)
    return {
        "template_ref_count": template_ref_count,
        "agent_ref_count": agent_ref_count,
        "in_use": template_ref_count > 0 or agent_ref_count > 0,
    }


def _attach_skill_reference_meta(cur, item: Dict[str, Any]) -> Dict[str, Any]:
    refs = _get_skill_reference_counts(cur, str(item.get("id") or ""))
    out = dict(item)
    out.update(refs)
    return out


def list_admin_skills(
    cfg: Dict[str, Any],
    page: int = 1,
    page_size: int = 10,
    search: str = "",
    category: str = "",
    scene: str = "",
    status: str = "",
) -> Dict[str, Any]:
    ensure_audit_capabilities_seeded(cfg)
    conn = get_conn(cfg)
    cur = conn.cursor()
    clauses = ["1=1"]
    params: List[Any] = []
    search_text = str(search or "").strip()
    if search_text:
        clauses.append(
            "(id LIKE ? OR display_name LIKE ? OR description LIKE ? OR reference_summary LIKE ?)"
        )
        token = f"%{search_text}%"
        params.extend([token, token, token, token])
    if category:
        clauses.append("category = ?")
        params.append(str(category).strip())
    if scene:
        clauses.append("scene = ?")
        params.append(str(scene).strip())
    if status:
        clauses.append("status = ?")
        params.append(str(status).strip().lower())

    where_sql = " AND ".join(clauses)
    cur.execute(f"SELECT COUNT(1) FROM audit_skill WHERE {where_sql}", params)
    total = int((cur.fetchone() or [0])[0] or 0)
    offset = max(page - 1, 0) * page_size
    cur.execute(
        f"""
        SELECT *
        FROM audit_skill
        WHERE {where_sql}
        ORDER BY sort_order ASC, updated_at DESC, created_at DESC, display_name ASC
        LIMIT ? OFFSET ?
        """,
        params + [page_size, offset],
    )
    rows = cur.fetchall()
    items = [
        _attach_skill_reference_meta(cur, _row_to_skill(dict(row)))
        for row in rows
    ]
    conn.close()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def get_admin_skill_detail(cfg: Dict[str, Any], skill_id: str) -> Optional[Dict[str, Any]]:
    ensure_audit_capabilities_seeded(cfg)
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("SELECT * FROM audit_skill WHERE id = ?", (skill_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return None
    item = _attach_skill_reference_meta(cur, _row_to_skill(dict(row)))
    conn.close()
    return item


def create_skill(
    cfg: Dict[str, Any],
    owner_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    ensure_audit_capabilities_seeded(cfg)
    now = _utc_now_iso()
    normalized = _normalize_skill_payload(payload)
    skill_id = _normalize_skill_id(
        payload.get("id"), normalized["display_name"])
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM audit_skill WHERE id = ?", (skill_id,))
    if cur.fetchone():
        conn.close()
        raise ValueError("skill id already exists")
    cur.execute(
        """
        INSERT INTO audit_skill(
            id, display_name, category, scene, description, owner_type, owner_id,
            visibility, status, source_url, reference_summary,
            input_schema_json, output_schema_json, config_schema_json, tags_json,
            sort_order, created_at, updated_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            skill_id,
            normalized["display_name"],
            normalized["category"],
            normalized["scene"],
            normalized["description"],
            "admin",
            owner_id,
            normalized["visibility"],
            normalized["status"],
            normalized["source_url"],
            normalized["reference_summary"],
            _json_text(normalized["input_schema"]),
            _json_text(normalized["output_schema"]),
            _json_text(normalized["config_schema"]),
            _json_text(normalized["tags"]),
            normalized["sort_order"],
            now,
            now,
        ),
    )
    conn.commit()
    cur.execute("SELECT * FROM audit_skill WHERE id = ?", (skill_id,))
    row = cur.fetchone()
    item = _attach_skill_reference_meta(cur, _row_to_skill(dict(row)))
    conn.close()
    return item


def update_skill(
    cfg: Dict[str, Any],
    skill_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    ensure_audit_capabilities_seeded(cfg)
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("SELECT * FROM audit_skill WHERE id = ?", (skill_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise ValueError("skill not found")
    existing = _row_to_skill(dict(row))
    normalized = _normalize_skill_payload(payload, existing=existing)
    refs = _get_skill_reference_counts(cur, skill_id)
    if normalized["status"] == "disabled" and refs["in_use"]:
        conn.close()
        raise ValueError(
            "cannot disable a skill that is referenced by templates or agents")
    now = _utc_now_iso()
    cur.execute(
        """
        UPDATE audit_skill
        SET display_name = ?, category = ?, scene = ?, description = ?, visibility = ?,
            status = ?, source_url = ?, reference_summary = ?, input_schema_json = ?,
            output_schema_json = ?, config_schema_json = ?, tags_json = ?, sort_order = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            normalized["display_name"],
            normalized["category"],
            normalized["scene"],
            normalized["description"],
            normalized["visibility"],
            normalized["status"],
            normalized["source_url"],
            normalized["reference_summary"],
            _json_text(normalized["input_schema"]),
            _json_text(normalized["output_schema"]),
            _json_text(normalized["config_schema"]),
            _json_text(normalized["tags"]),
            normalized["sort_order"],
            now,
            skill_id,
        ),
    )
    conn.commit()
    cur.execute("SELECT * FROM audit_skill WHERE id = ?", (skill_id,))
    saved = _attach_skill_reference_meta(
        cur, _row_to_skill(dict(cur.fetchone())))
    conn.close()
    return saved


def set_skill_status(
    cfg: Dict[str, Any],
    skill_id: str,
    status: str,
) -> Dict[str, Any]:
    status_value = str(status or "").strip().lower()
    if status_value not in {"active", "disabled"}:
        raise ValueError("status must be active or disabled")
    return update_skill(cfg, skill_id, {"status": status_value})


def delete_skill(cfg: Dict[str, Any], skill_id: str) -> Dict[str, Any]:
    ensure_audit_capabilities_seeded(cfg)
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute("SELECT * FROM audit_skill WHERE id = ?", (skill_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise ValueError("skill not found")
    refs = _get_skill_reference_counts(cur, skill_id)
    if refs["in_use"]:
        conn.close()
        raise ValueError(
            "cannot delete a skill that is referenced by templates or agents")
    now = _utc_now_iso()
    cur.execute(
        "UPDATE audit_skill SET status = 'deleted', updated_at = ? WHERE id = ?",
        (now, skill_id),
    )
    conn.commit()
    cur.execute("SELECT * FROM audit_skill WHERE id = ?", (skill_id,))
    item = _attach_skill_reference_meta(
        cur, _row_to_skill(dict(cur.fetchone())))
    conn.close()
    return item


def list_rule_packs(cfg: Dict[str, Any], user_id: str = "", scene: str = "") -> List[Dict[str, Any]]:
    ensure_audit_capabilities_seeded(cfg)
    conn = get_conn(cfg)
    cur = conn.cursor()
    clauses = ["p.status='active'"]
    params: List[Any] = []
    if scene:
        clauses.append("p.scene=?")
        params.append(scene)
    clauses.append("(p.visibility='public' OR p.owner_id=?)")
    params.append(user_id)
    sql = f"""
        SELECT
            p.*,
            CASE
                WHEN p.id='tax_core_default' THEN (SELECT COUNT(1) FROM tax_rule)
                ELSE 0
            END AS rule_count
        FROM audit_rule_pack p
        WHERE {' AND '.join(clauses)}
        ORDER BY p.sort_order ASC, p.display_name ASC
    """
    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()
    return [_row_to_rule_pack(dict(row)) for row in rows]


def get_rule_pack_detail(cfg: Dict[str, Any], pack_id: str, user_id: str = "") -> Optional[Dict[str, Any]]:
    ensure_audit_capabilities_seeded(cfg)
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            p.*,
            CASE
                WHEN p.id='tax_core_default' THEN (SELECT COUNT(1) FROM tax_rule)
                ELSE 0
            END AS rule_count
        FROM audit_rule_pack p
        WHERE p.id=? AND p.status='active' AND (p.visibility='public' OR p.owner_id=?)
        """,
        (pack_id, user_id),
    )
    row = cur.fetchone()
    conn.close()
    return _row_to_rule_pack(dict(row)) if row else None


def list_rule_pack_drafts(
    cfg: Dict[str, Any],
    owner_id: str = "",
    status: str = "",
    base_pack_id: str = "",
    include_all: bool = False,
) -> List[Dict[str, Any]]:
    ensure_audit_capabilities_seeded(cfg)
    conn = get_conn(cfg)
    cur = conn.cursor()
    clauses = ["1=1"]
    params: List[Any] = []
    if not include_all:
        clauses.append("owner_id=?")
        params.append(owner_id)
    if status:
        clauses.append("status=?")
        params.append(status)
    if base_pack_id:
        clauses.append("base_pack_id=?")
        params.append(base_pack_id)
    cur.execute(
        f"""
        SELECT *
        FROM audit_rule_pack_draft
        WHERE {' AND '.join(clauses)}
        ORDER BY updated_at DESC, created_at DESC
        """,
        params,
    )
    rows = cur.fetchall()
    conn.close()
    return [_row_to_rule_pack_draft(dict(row)) for row in rows]


def get_rule_pack_draft(
    cfg: Dict[str, Any],
    draft_id: str,
    owner_id: str = "",
    include_all: bool = False,
) -> Optional[Dict[str, Any]]:
    ensure_audit_capabilities_seeded(cfg)
    conn = get_conn(cfg)
    cur = conn.cursor()
    clauses = ["id=?"]
    params: List[Any] = [draft_id]
    if not include_all:
        clauses.append("owner_id=?")
        params.append(owner_id)
    cur.execute(
        f"""
        SELECT *
        FROM audit_rule_pack_draft
        WHERE {' AND '.join(clauses)}
        """,
        params,
    )
    row = cur.fetchone()
    conn.close()
    return _row_to_rule_pack_draft(dict(row)) if row else None


def create_rule_pack_draft(
    cfg: Dict[str, Any],
    owner_id: str,
    base_pack_id: str,
    display_name: Optional[str] = None,
    description: Optional[str] = None,
    selector: Optional[Dict[str, Any]] = None,
    source_note: Optional[str] = None,
    change_summary: str = "",
) -> Dict[str, Any]:
    ensure_audit_capabilities_seeded(cfg)
    pack = get_rule_pack_detail(cfg, base_pack_id, user_id=owner_id)
    if not pack:
        raise ValueError("rule pack not found")

    now = _utc_now_iso()
    draft_id = str(uuid.uuid4())
    final_display_name = str(
        display_name or pack.get("display_name") or "").strip()
    final_description = str(
        pack.get("description") if description is None else description or ""
    ).strip()
    final_source_note = str(
        pack.get("source_note") if source_note is None else source_note or ""
    ).strip()
    final_selector = _normalize_rule_selector(
        pack.get("selector") if selector is None else selector
    )
    if not final_display_name:
        raise ValueError("display_name is required")

    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO audit_rule_pack_draft(
            id, base_pack_id, owner_id, display_name, scene, description,
            selector_json, source_note, change_summary, status,
            review_comment, submitted_at, reviewed_by, reviewed_at,
            published_version_no, created_at, updated_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            draft_id,
            base_pack_id,
            owner_id,
            final_display_name,
            str(pack.get("scene") or ""),
            final_description,
            _json_text(final_selector),
            final_source_note,
            str(change_summary or "").strip(),
            "draft",
            None,
            None,
            None,
            None,
            None,
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()
    return get_rule_pack_draft(cfg, draft_id, owner_id=owner_id) or {}


def update_rule_pack_draft(
    cfg: Dict[str, Any],
    draft_id: str,
    owner_id: str,
    display_name: Optional[str] = None,
    description: Optional[str] = None,
    selector: Optional[Dict[str, Any]] = None,
    source_note: Optional[str] = None,
    change_summary: Optional[str] = None,
) -> Dict[str, Any]:
    ensure_audit_capabilities_seeded(cfg)
    draft = get_rule_pack_draft(cfg, draft_id, owner_id=owner_id)
    if not draft:
        raise ValueError("rule pack draft not found")
    if str(draft.get("status") or "") != "draft":
        raise ValueError("only draft status can be updated")

    updates: Dict[str, Any] = {}
    if display_name is not None:
        normalized_display_name = str(display_name or "").strip()
        if not normalized_display_name:
            raise ValueError("display_name is required")
        updates["display_name"] = normalized_display_name
    if description is not None:
        updates["description"] = str(description or "").strip()
    if selector is not None:
        updates["selector_json"] = _json_text(
            _normalize_rule_selector(selector))
    if source_note is not None:
        updates["source_note"] = str(source_note or "").strip()
    if change_summary is not None:
        updates["change_summary"] = str(change_summary or "").strip()
    if not updates:
        raise ValueError("no fields to update")

    updates["updated_at"] = _utc_now_iso()
    set_clause = ", ".join(f"{column}=?" for column in updates.keys())
    params = list(updates.values()) + [draft_id, owner_id]
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        f"""
        UPDATE audit_rule_pack_draft
        SET {set_clause}
        WHERE id=? AND owner_id=? AND status='draft'
        """,
        params,
    )
    conn.commit()
    conn.close()
    return get_rule_pack_draft(cfg, draft_id, owner_id=owner_id) or {}


def submit_rule_pack_draft(cfg: Dict[str, Any], draft_id: str, owner_id: str) -> Dict[str, Any]:
    ensure_audit_capabilities_seeded(cfg)
    draft = get_rule_pack_draft(cfg, draft_id, owner_id=owner_id)
    if not draft:
        raise ValueError("rule pack draft not found")
    if str(draft.get("status") or "") != "draft":
        raise ValueError("draft is not in editable status")
    if not str(draft.get("change_summary") or "").strip():
        raise ValueError("change_summary is required before submission")

    now = _utc_now_iso()
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE audit_rule_pack_draft
        SET status=?, submitted_at=?, updated_at=?
        WHERE id=? AND owner_id=? AND status='draft'
        """,
        ("pending_review", now, now, draft_id, owner_id),
    )
    conn.commit()
    conn.close()
    return get_rule_pack_draft(cfg, draft_id, owner_id=owner_id) or {}


def review_rule_pack_draft(
    cfg: Dict[str, Any],
    draft_id: str,
    reviewer_id: str,
    action: str,
    review_comment: str = "",
) -> Dict[str, Any]:
    ensure_audit_capabilities_seeded(cfg)
    normalized_action = str(action or "").strip().lower()
    if normalized_action not in {"approve", "reject"}:
        raise ValueError("action must be approve or reject")

    draft = get_rule_pack_draft(cfg, draft_id, include_all=True)
    if not draft:
        raise ValueError("rule pack draft not found")
    if str(draft.get("status") or "") != "pending_review":
        raise ValueError("draft is not pending review")

    now = _utc_now_iso()
    conn = get_conn(cfg)
    cur = conn.cursor()
    if normalized_action == "reject":
        cur.execute(
            """
            UPDATE audit_rule_pack_draft
            SET status=?, review_comment=?, reviewed_by=?, reviewed_at=?, updated_at=?
            WHERE id=? AND status='pending_review'
            """,
            ("rejected", str(review_comment or "").strip(),
             reviewer_id, now, now, draft_id),
        )
        conn.commit()
        conn.close()
        return get_rule_pack_draft(cfg, draft_id, include_all=True) or {}

    cur.execute(
        "SELECT COALESCE(MAX(version_no), 0) AS max_version_no FROM audit_rule_pack_version WHERE pack_id=?",
        (draft["base_pack_id"],),
    )
    row = cur.fetchone()
    next_version_no = int(row["max_version_no"] or 0) + 1
    version_id = str(uuid.uuid4())
    cur.execute(
        """
        INSERT INTO audit_rule_pack_version(
            id, pack_id, draft_id, version_no, display_name, scene, description,
            selector_json, source_note, change_summary, published_by, published_at,
            created_at, updated_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            version_id,
            draft["base_pack_id"],
            draft_id,
            next_version_no,
            draft["display_name"],
            draft["scene"],
            draft.get("description", ""),
            _json_text(draft.get("selector") or {}),
            draft.get("source_note", ""),
            draft.get("change_summary", ""),
            reviewer_id,
            now,
            now,
            now,
        ),
    )
    cur.execute(
        """
        UPDATE audit_rule_pack
        SET display_name=?, scene=?, description=?, selector_json=?, source_note=?, updated_at=?
        WHERE id=?
        """,
        (
            draft["display_name"],
            draft["scene"],
            draft.get("description", ""),
            _json_text(draft.get("selector") or {}),
            draft.get("source_note", ""),
            now,
            draft["base_pack_id"],
        ),
    )
    cur.execute(
        """
        UPDATE audit_rule_pack_draft
        SET status=?, review_comment=?, reviewed_by=?, reviewed_at=?, published_version_no=?, updated_at=?
        WHERE id=? AND status='pending_review'
        """,
        (
            "approved",
            str(review_comment or "").strip(),
            reviewer_id,
            now,
            next_version_no,
            now,
            draft_id,
        ),
    )
    conn.commit()
    conn.close()
    return get_rule_pack_draft(cfg, draft_id, include_all=True) or {}


def list_rule_pack_versions(
    cfg: Dict[str, Any],
    pack_id: str,
    user_id: str = "",
) -> List[Dict[str, Any]]:
    ensure_audit_capabilities_seeded(cfg)
    pack = get_rule_pack_detail(cfg, pack_id, user_id=user_id)
    if not pack:
        return []
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM audit_rule_pack_version
        WHERE pack_id=?
        ORDER BY version_no DESC, published_at DESC
        """,
        (pack_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return [_row_to_rule_pack_version(dict(row)) for row in rows]


def list_rules(
    cfg: Dict[str, Any],
    pack_id: str = "",
    rule_type: str = "",
    limit: int = 200,
    offset: int = 0,
) -> Dict[str, Any]:
    ensure_audit_capabilities_seeded(cfg)
    selector: Dict[str, Any] = {}
    if pack_id:
        conn = get_conn(cfg)
        cur = conn.cursor()
        cur.execute(
            "SELECT selector_json FROM audit_rule_pack WHERE id=?", (pack_id,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return {"items": [], "total": 0, "pack_id": pack_id}
        selector = _safe_json_loads(row["selector_json"], {})

    clauses = ["1=1"]
    params: List[Any] = []
    if rule_type:
        clauses.append("rule_type=?")
        params.append(rule_type)
    selector_rule_types = selector.get(
        "rule_types") if isinstance(selector, dict) else None
    if isinstance(selector_rule_types, list) and selector_rule_types:
        placeholders = ",".join("?" for _ in selector_rule_types)
        clauses.append(f"rule_type IN ({placeholders})")
        params.extend(selector_rule_types)
    where_sql = " AND ".join(clauses)

    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        f"SELECT COUNT(1) AS total FROM tax_rule WHERE {where_sql}", params)
    total_row = cur.fetchone()
    total = int(total_row["total"]) if total_row else 0
    cur.execute(
        f"""
        SELECT id, law_title, article_no, rule_type, region, industry, effective_date, expiry_date, source_text
        FROM tax_rule
        WHERE {where_sql}
        ORDER BY effective_date DESC, article_no ASC, created_at DESC
        LIMIT ? OFFSET ?
        """,
        params + [max(1, min(limit, 500)), max(0, offset)],
    )
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return {"items": rows, "total": total, "pack_id": pack_id}


def list_templates(cfg: Dict[str, Any], user_id: str = "", scene: str = "") -> List[Dict[str, Any]]:
    ensure_audit_capabilities_seeded(cfg)
    conn = get_conn(cfg)
    cur = conn.cursor()
    clauses = ["status='active'"]
    params: List[Any] = []
    if scene:
        clauses.append("scene=?")
        params.append(scene)
    clauses.append("(visibility='public' OR owner_id=?)")
    params.append(user_id)
    cur.execute(
        f"""
        SELECT *
        FROM audit_template
        WHERE {' AND '.join(clauses)}
        ORDER BY sort_order ASC, display_name ASC
        """,
        params,
    )
    rows = cur.fetchall()
    conn.close()
    return [_row_to_template(dict(row)) for row in rows]


def get_template_detail(cfg: Dict[str, Any], template_id: str, user_id: str = "") -> Optional[Dict[str, Any]]:
    ensure_audit_capabilities_seeded(cfg)
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM audit_template
        WHERE id=? AND status='active' AND (visibility='public' OR owner_id=?)
        """,
        (template_id, user_id),
    )
    row = cur.fetchone()
    conn.close()
    return _row_to_template(dict(row)) if row else None


def _row_to_agent_profile(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    item["enabled_skill_ids"] = _safe_json_loads(
        item.pop("enabled_skill_ids_json", "[]"), [])
    item["enabled_rule_pack_ids"] = _safe_json_loads(
        item.pop("enabled_rule_pack_ids_json", "[]"), [])
    return item


def list_agent_profiles(cfg: Dict[str, Any], owner_id: str, scene: str = "") -> List[Dict[str, Any]]:
    ensure_audit_capabilities_seeded(cfg)
    conn = get_conn(cfg)
    cur = conn.cursor()
    clauses = ["owner_id=?", "status='active'"]
    params: List[Any] = [owner_id]
    if scene:
        clauses.append("scene=?")
        params.append(scene)
    cur.execute(
        f"""
        SELECT *
        FROM agent_profile
        WHERE {' AND '.join(clauses)}
        ORDER BY created_at DESC, display_name ASC
        """,
        params,
    )
    rows = cur.fetchall()
    conn.close()
    return [_row_to_agent_profile(dict(row)) for row in rows]


def get_agent_profile(cfg: Dict[str, Any], profile_id: str, owner_id: str) -> Optional[Dict[str, Any]]:
    ensure_audit_capabilities_seeded(cfg)
    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM agent_profile
        WHERE id=? AND owner_id=? AND status='active'
        """,
        (profile_id, owner_id),
    )
    row = cur.fetchone()
    conn.close()
    return _row_to_agent_profile(dict(row)) if row else None


def create_agent_profile(
    cfg: Dict[str, Any],
    owner_id: str,
    display_name: str,
    template_id: str,
    description: str = "",
    system_prompt: str = "",
    enabled_skill_ids: Optional[List[str]] = None,
    enabled_rule_pack_ids: Optional[List[str]] = None,
    max_llm_steps: Optional[int] = None,
    max_skills_per_run: Optional[int] = None,
) -> Dict[str, Any]:
    ensure_audit_capabilities_seeded(cfg)
    template = get_template_detail(cfg, template_id, user_id=owner_id)
    if not template:
        raise ValueError("template not found")

    normalized_skill_ids = _normalize_string_list(
        enabled_skill_ids, "enabled_skill_ids")
    normalized_rule_pack_ids = _normalize_string_list(
        enabled_rule_pack_ids, "enabled_rule_pack_ids")
    _validate_agent_limits(max_llm_steps, max_skills_per_run)

    final_skill_ids = (
        list(normalized_skill_ids)
        if normalized_skill_ids is not None
        else list(template.get("skill_ids") or [])
    )
    final_rule_pack_ids = (
        list(normalized_rule_pack_ids)
        if normalized_rule_pack_ids is not None
        else list(template.get("rule_pack_ids") or [])
    )
    _validate_agent_bindings(
        cfg,
        owner_id=owner_id,
        scene=template["scene"],
        enabled_skill_ids=final_skill_ids,
        enabled_rule_pack_ids=final_rule_pack_ids,
    )
    resolved_max_skills_per_run = max(
        1, int(max_skills_per_run or template.get("max_skills_per_run") or 6))
    _validate_agent_runtime_combination(
        final_skill_ids, resolved_max_skills_per_run)

    now = _utc_now_iso()
    profile_id = str(uuid.uuid4())
    payload = {
        "id": profile_id,
        "owner_id": owner_id,
        "display_name": str(display_name or "").strip(),
        "description": str(description or "").strip(),
        "scene": str(template["scene"]),
        "template_id": template_id,
        "enabled_skill_ids_json": _json_text(final_skill_ids),
        "enabled_rule_pack_ids_json": _json_text(final_rule_pack_ids),
        "system_prompt": str(system_prompt or "").strip(),
        "max_llm_steps": max(1, int(max_llm_steps or template.get("max_llm_steps") or 2)),
        "max_skills_per_run": resolved_max_skills_per_run,
        "status": "active",
        "created_at": now,
        "updated_at": now,
    }
    if not payload["display_name"]:
        raise ValueError("display_name is required")

    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO agent_profile(
            id, owner_id, display_name, description, scene, template_id,
            enabled_skill_ids_json, enabled_rule_pack_ids_json, system_prompt,
            max_llm_steps, max_skills_per_run, status, created_at, updated_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            payload["id"],
            payload["owner_id"],
            payload["display_name"],
            payload["description"],
            payload["scene"],
            payload["template_id"],
            payload["enabled_skill_ids_json"],
            payload["enabled_rule_pack_ids_json"],
            payload["system_prompt"],
            payload["max_llm_steps"],
            payload["max_skills_per_run"],
            payload["status"],
            payload["created_at"],
            payload["updated_at"],
        ),
    )
    conn.commit()
    conn.close()
    return get_agent_profile(cfg, profile_id, owner_id) or {}


def update_agent_profile(
    cfg: Dict[str, Any],
    profile_id: str,
    owner_id: str,
    display_name: Optional[str] = None,
    description: Optional[str] = None,
    system_prompt: Optional[str] = None,
    enabled_skill_ids: Optional[List[str]] = None,
    enabled_rule_pack_ids: Optional[List[str]] = None,
    max_llm_steps: Optional[int] = None,
    max_skills_per_run: Optional[int] = None,
) -> Dict[str, Any]:
    ensure_audit_capabilities_seeded(cfg)
    current = get_agent_profile(cfg, profile_id, owner_id)
    if not current:
        raise ValueError("agent profile not found")

    normalized_skill_ids = _normalize_string_list(
        enabled_skill_ids, "enabled_skill_ids")
    normalized_rule_pack_ids = _normalize_string_list(
        enabled_rule_pack_ids, "enabled_rule_pack_ids")
    _validate_agent_limits(max_llm_steps, max_skills_per_run)

    next_skill_ids = (
        list(normalized_skill_ids)
        if normalized_skill_ids is not None
        else list(current.get("enabled_skill_ids") or [])
    )
    next_rule_pack_ids = (
        list(normalized_rule_pack_ids)
        if normalized_rule_pack_ids is not None
        else list(current.get("enabled_rule_pack_ids") or [])
    )
    next_max_skills_per_run = (
        int(max_skills_per_run)
        if max_skills_per_run is not None
        else int(current.get("max_skills_per_run") or 1)
    )

    _validate_agent_bindings(
        cfg,
        owner_id=owner_id,
        scene=str(current.get("scene") or ""),
        enabled_skill_ids=next_skill_ids,
        enabled_rule_pack_ids=next_rule_pack_ids,
    )
    _validate_agent_runtime_combination(
        next_skill_ids, next_max_skills_per_run)

    updates: Dict[str, Any] = {}
    if display_name is not None:
        normalized_display_name = str(display_name or "").strip()
        if not normalized_display_name:
            raise ValueError("display_name is required")
        updates["display_name"] = normalized_display_name
    if description is not None:
        updates["description"] = str(description or "").strip()
    if system_prompt is not None:
        updates["system_prompt"] = str(system_prompt or "").strip()
    if normalized_skill_ids is not None:
        updates["enabled_skill_ids_json"] = _json_text(next_skill_ids)
    if normalized_rule_pack_ids is not None:
        updates["enabled_rule_pack_ids_json"] = _json_text(next_rule_pack_ids)
    if max_llm_steps is not None:
        updates["max_llm_steps"] = int(max_llm_steps)
    if max_skills_per_run is not None:
        updates["max_skills_per_run"] = next_max_skills_per_run

    if not updates:
        raise ValueError("no fields to update")

    updates["updated_at"] = _utc_now_iso()
    set_clause = ", ".join(f"{column}=?" for column in updates.keys())
    params = list(updates.values()) + [profile_id, owner_id]

    conn = get_conn(cfg)
    cur = conn.cursor()
    cur.execute(
        f"""
        UPDATE agent_profile
        SET {set_clause}
        WHERE id=? AND owner_id=? AND status='active'
        """,
        params,
    )
    conn.commit()
    conn.close()
    return get_agent_profile(cfg, profile_id, owner_id) or {}
