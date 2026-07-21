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


def _skill_file(
    path: str,
    *,
    entry_type: str = "file",
    content_text: str = "",
    size_bytes: Optional[int] = None,
    branch_name: str = "main",
    sort_order: int = 100,
) -> Dict[str, Any]:
    encoded_size = len(str(content_text or "").encode("utf-8"))
    return {
        "path": path,
        "entry_type": entry_type,
        "content_text": content_text,
        "size_bytes": int(size_bytes if size_bytes is not None else encoded_size),
        "branch_name": branch_name if entry_type == "file" else "",
        "sort_order": sort_order,
    }


def _skill_version(
    version_tag: str,
    *,
    published_at: str = "",
    is_latest: bool = True,
    download_url: str = "",
    changelog_items: Optional[List[str]] = None,
    sort_order: int = 100,
) -> Dict[str, Any]:
    items = [str(item).strip()
             for item in (changelog_items or []) if str(item).strip()]
    return {
        "version_tag": version_tag,
        "release_label": "Latest" if is_latest else "",
        "published_at": published_at,
        "is_latest": bool(is_latest),
        "download_url": download_url,
        "changelog_text": "\n".join(f"- {item}" for item in items),
        "changelog_json": items,
        "sort_order": sort_order,
    }


def _skill_card(
    *,
    overview: str,
    publisher_name: str,
    publisher_handle: str,
    version: str,
    license_name: str,
    geography: Optional[List[str]] = None,
    use_type: str = "",
    use_case: str = "",
    review_before_use: Optional[List[Dict[str, str]]] = None,
    ethical_considerations: str = "",
    output_behavior: Optional[Dict[str, Any]] = None,
    references: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    return {
        "overview": overview,
        "publisher_name": publisher_name,
        "publisher_handle": publisher_handle,
        "version": version,
        "license_name": license_name,
        "geography": geography or [],
        "use_type": use_type,
        "use_case": use_case,
        "review_before_use": review_before_use or [],
        "ethical_considerations": ethical_considerations,
        "output_behavior": output_behavior or {},
        "references": references or [],
    }


def _build_default_skill_md(item: Dict[str, Any]) -> str:
    display_name = str(item.get("display_name") or item.get("id") or "Skill")
    description = str(item.get("description") or "").strip()
    reference_summary = str(item.get("reference_summary") or "").strip()
    source_url = str(item.get("source_url") or "").strip()
    tags = _safe_json_loads(item.get("tags_json", "[]"), [])
    tag_line = ", ".join(tags) if tags else "N/A"
    lines = [
        f"# {display_name}",
        "",
        "## Overview",
        description or "暂无补充说明。",
    ]
    if reference_summary:
        lines.extend(["", "## Reference Summary", reference_summary])
    lines.extend([
        "",
        "## Metadata",
        f"- Skill ID: `{item.get('id', '')}`",
        f"- Category: `{item.get('category', '')}`",
        f"- Scene: `{item.get('scene', '')}`",
        f"- Tags: {tag_line}",
    ])
    if source_url:
        lines.append(f"- Source URL: {source_url}")
    return "\n".join(lines).strip() + "\n"


def _build_default_skill_detail(item: Dict[str, Any]) -> Dict[str, Any]:
    skill_md_text = _build_default_skill_md(item)
    display_name = str(item.get("display_name") or item.get("id") or "Skill")
    source_url = str(item.get("source_url") or "").strip()
    references = []
    if source_url:
        references.append(
            {"label": "Source URL", "url": source_url, "type": "external"})
    references.append(
        {"label": "SKILL.md", "url": "SKILL.md", "type": "internal"})
    card = _skill_card(
        overview=str(item.get("description") or "").strip(
        ) or f"{display_name} 的结构化能力说明。",
        publisher_name="AI-Law-Assistant",
        publisher_handle="@system",
        version="v1.0.0",
        license_name="Internal Reference",
        use_type="Internal / project use",
        use_case=str(item.get("reference_summary") or "").strip(),
        output_behavior={
            "types": ["Structured JSON"],
            "format": "Internal skill execution payload",
            "parameters": "2D",
            "side_effects": ["Read-only by default"],
        },
        references=references,
    )
    return {
        "publisher_name": "AI-Law-Assistant",
        "publisher_handle": "@system",
        "install_command": "",
        "skill_md_text": skill_md_text,
        "skill_card": card,
        "current_version": "v1.0.0",
        "license_name": "Internal Reference",
        "security_audit_status": "internal",
        "downloads_30d": 0,
        "downloads_all_time": 0,
        "last_published_at": "",
        "files": [
            _skill_file("SKILL.md", content_text=skill_md_text, sort_order=10),
            _skill_file("skill-card.md",
                        content_text=_json_text(card), sort_order=20),
        ],
        "versions": [
            _skill_version(
                "v1.0.0",
                is_latest=True,
                changelog_items=[
                    "Initial internal mirrored version for AI-Law-Assistant."],
                sort_order=10,
            )
        ],
    }


TAX_DIGITAL_LOCALIZATION_SKILL_MD = """---
name: tax-digital-localization
description: 用于财税数字化系统程序内文案与相关本地化内容的翻译与轻量润色。适用于将中文页面功能名、表单标签、按钮文案、菜单项、状态词、校验提示、错误提示、成功提示、系统通知，以及发票、税务、申报、合规相关系统文案翻译为目标国家常用语言，尤其适合越南、马来西亚、新加坡、波兰、意大利、德国等市场。用户提到国际化、i18n、多语言、页面翻译、按钮翻译、提示语翻译、系统文案翻译、发票系统翻译、税务系统翻译或给出明显属于程序界面的中文文案时，都应使用此 skill。
---
# Tax Digital Localization
## Overview
将中文程序内文案翻译为适合目标国家上线使用的最终译文，重点服务财税、发票、申报、合规类系统。
默认源语言为中文，按目标国家映射固定目标语言，输出默认只返回可直接落入程序的最终译文。
## Default Mapping
按目标国家映射固定目标语言：
- 越南 -> 越南语
- 马来西亚 -> 马来语
- 新加坡 -> 英语
- 波兰 -> 波兰语
- 意大利 -> 意大利语
- 德国 -> 德语
如果用户明确指定了目标语言，则以用户指定为准。
如果用户没有给出目标国家，也没有指定目标语言，先用一句话追问，不要自行猜测。
## Scope
优先处理以下程序内文案：
- 页面名称
- 菜单项
- 按钮
- 表单标签
- 状态词
- 校验提示
- 错误提示
- 成功提示
- Toast、弹窗、确认提示
- 发票、税务、申报、合规相关 UI 文案
## Resource Routing
始终先参考 [ui-patterns.md](./references/ui-patterns.md)。
涉及发票、税务、申报、税号、合规等高风险语义时，再读取 [tax-terms.md](./references/tax-terms.md)。
按目标国家读取对应的 locale reference：
- [vietnam.md](./references/locales/vietnam.md)
- [malaysia.md](./references/locales/malaysia.md)
- [singapore.md](./references/locales/singapore.md)
- [poland.md](./references/locales/poland.md)
- [italy.md](./references/locales/italy.md)
- [germany.md](./references/locales/germany.md)
## Workflow
1. 识别输入属于哪类程序文案。
2. 确认目标国家或目标语言。
3. 判断属于轻量翻译还是高风险术语。
4. 高风险术语优先结合 tax-terms 与 locale 资料校准。
5. 输出最终译文。
## Preservation
默认保持占位符、ICU 结构、HTML/XML/Markdown、字段名、错误码和换行结构不变。
"""


TAX_DIGITAL_LOCALIZATION_SKILL_CARD = _skill_card(
    overview="Localizes Chinese UI and system copy for digital tax, invoice, filing, and compliance products into market-appropriate language.",
    publisher_name="Munich949",
    publisher_handle="@munich949",
    version="v1.0.0",
    license_name="MIT-0",
    geography=["Vietnam", "Malaysia", "Singapore",
               "Poland", "Italy", "Germany"],
    use_type="Commercial / non-commercial",
    use_case="Developers and product teams translate Chinese program UI strings for tax and e-invoicing systems while preserving placeholders, keys, markup, ordering, and product-ready wording.",
    review_before_use=[
        {
            "risk": "Country-to-language defaults may not match multilingual deployments or local compliance expectations.",
            "mitigation": "Specify the exact target locale or language for each deployment, especially for Singapore, Malaysia, and other multilingual markets.",
        },
        {
            "risk": "Tax, invoice, filing, and compliance terms can carry legal or business meaning that translation may not fully resolve.",
            "mitigation": "Have important tax wording reviewed by local subject-matter experts before production use, and verify high-risk terms against authoritative local sources when needed.",
        },
    ],
    ethical_considerations="Users should review generated text before production use and apply their organization's compliance requirements.",
    output_behavior={
        "types": ["Text", "Guidance"],
        "format": "Plain text translation with an optional note line when ambiguity or semantic risk is present.",
        "parameters": "1D",
        "side_effects": [
            "Preserves code keys and placeholders",
            "Preserves ICU structures and markup",
            "Preserves line breaks and ordering",
        ],
    },
    references=[
        {"label": "ClawHub skill page",
            "url": "https://clawhub.ai/munich949/skills/tax-digital-localization", "type": "external"},
        {"label": "ui-patterns.md",
            "url": "references/ui-patterns.md", "type": "internal"},
        {"label": "tax-terms.md", "url": "references/tax-terms.md", "type": "internal"},
    ],
)


TAX_DIGITAL_LOCALIZATION_FILES = [
    _skill_file("SKILL.md", content_text=TAX_DIGITAL_LOCALIZATION_SKILL_MD,
                size_bytes=5800, sort_order=10),
    _skill_file("skill-card.md", content_text=_json_text(
        TAX_DIGITAL_LOCALIZATION_SKILL_CARD), size_bytes=2700, sort_order=20),
    _skill_file("agents", entry_type="dir", sort_order=30),
    _skill_file("agents/openai.yaml", content_text="model: openai-compatible\nmode: translation\n",
                size_bytes=234, sort_order=40),
    _skill_file("references", entry_type="dir", sort_order=50),
    _skill_file("references/tax-terms.md",
                content_text="# Tax Terms\n高风险财税术语表，用于校准开票、申报、抵扣、作废、红冲等专业表达。\n", size_bytes=2900, sort_order=60),
    _skill_file("references/ui-patterns.md",
                content_text="# UI Patterns\n本文件用于约束程序内文案的通用翻译风格，优先级高于逐字直译。\n- 优先让用户一眼看懂，而不是保留中文句法。\n- 优先写成真实产品会使用的文案，而不是教科书式翻译。\n- 同一轮任务中，术语必须保持前后一致。\n", size_bytes=1900, sort_order=70),
    _skill_file("references/locales", entry_type="dir", sort_order=80),
    _skill_file("references/locales/germany.md",
                content_text="# Germany Locale\n- 默认输出德语\n- 保留官方名称和缩写：E-Rechnung、XRechnung、ZUGFeRD、Peppol\n- 提交类动作优先使用 einreichen / übermitteln\n", size_bytes=1300, sort_order=81),
    _skill_file("references/locales/malaysia.md",
                content_text="# Malaysia Locale\n默认输出马来语，涉及税务和电子发票语义时优先稳妥专业表达。\n", size_bytes=1400, sort_order=82),
    _skill_file("references/locales/singapore.md",
                content_text="# Singapore Locale\n默认输出英语，优先适配新加坡电子发票与税务系统语境。\n", size_bytes=1300, sort_order=83),
    _skill_file("references/locales/vietnam.md",
                content_text="# Vietnam Locale\n默认输出越南语，发票、税号和申报提示要优先保证专业准确。\n", size_bytes=1500, sort_order=84),
    _skill_file("references/locales/italy.md",
                content_text="# Italy Locale\n默认输出意大利语，发票与申报相关术语需贴近本地企业软件表达。\n", size_bytes=1400, sort_order=85),
    _skill_file("references/locales/poland.md",
                content_text="# Poland Locale\n默认输出波兰语，优先保持专业术语一致性与界面可用性。\n", size_bytes=1300, sort_order=86),
]


TAX_DIGITAL_LOCALIZATION_VERSIONS = [
    _skill_version(
        "v1.0.0",
        published_at="2026-04-07",
        is_latest=True,
        download_url="https://clawhub.ai/munich949/skills/tax-digital-localization#versions",
        changelog_items=[
            "Initial release of tax-digital-localization.",
            "Supports Vietnam, Malaysia, Singapore, Poland, Italy, and Germany.",
            "Adds locale-aware routing for high-risk tax terminology.",
            "Preserves placeholders, keys, markup, and line ordering.",
        ],
        sort_order=10,
    )
]


BUILTIN_SKILL_DETAIL_SEEDS: Dict[str, Dict[str, Any]] = {
    "tax_digital_localization": {
        "publisher_name": "Munich949",
        "publisher_handle": "@munich949",
        "install_command": "openclaw skills install @munich949/tax-digital-localization",
        "skill_md_text": TAX_DIGITAL_LOCALIZATION_SKILL_MD,
        "skill_card": TAX_DIGITAL_LOCALIZATION_SKILL_CARD,
        "current_version": "v1.0.0",
        "license_name": "MIT-0",
        "security_audit_status": "pass",
        "downloads_30d": 116,
        "downloads_all_time": 116,
        "last_published_at": "2026-04-07",
        "files": TAX_DIGITAL_LOCALIZATION_FILES,
        "versions": TAX_DIGITAL_LOCALIZATION_VERSIONS,
    },
    "china_tax_law_knowledge": {
        "publisher_name": "Richie Dirkson",
        "publisher_handle": "@codefarmerman",
        "install_command": "openclaw skills install @codefarmerman/china-tax-law",
        "skill_md_text": "# 中国财税法律专业知识助手\n\n## 角色定位\n作为中国资深财税律师的专业助手，提供准确、全面的中国税法知识支持，协助完成税务咨询、筹划、合规和争议解决等工作。\n\n## 核心工作原则\n- 法规优先：所有建议必须有明确法律法规依据。\n- 时效意识：税法更新频繁，应提示核实最新规定。\n- 风险提示：税务筹划必须明确说明合规边界。\n- 专业严谨：区分合法避税与违法逃税。\n",
        "skill_card": _skill_card(
            overview="中国财税法律知识助手，覆盖增值税、企业所得税、个人所得税、印花税等咨询、筹划和合规审查。",
            publisher_name="Richie Dirkson",
            publisher_handle="@codefarmerman",
            version="v1.0.0",
            license_name="MIT-0",
            geography=["China"],
            use_type="Commercial / non-commercial",
            use_case="用于税法咨询、税务筹划、税务争议处理以及税收政策解读。",
            review_before_use=[
                {"risk": "税法政策变化较快，旧条文可能失效。",
                    "mitigation": "回答中注明政策版本和时效，并核查国家税务总局最新公告。"},
                {"risk": "筹划建议可能触发合规边界问题。", "mitigation": "所有筹划建议必须附带风险提示和合法性边界说明。"},
            ],
            ethical_considerations="不应输出任何规避税法或违法逃税建议。",
            output_behavior={
                "types": ["Text", "Guidance", "Citation"],
                "format": "法规优先的专业说明，附引用条文。",
                "parameters": "2D",
                "side_effects": ["May require external law verification"],
            },
            references=[
                {"label": "ClawHub skill page",
                    "url": "https://clawhub.ai/codefarmerman/skills/china-tax-law", "type": "external"},
            ],
        ),
        "current_version": "v1.0.0",
        "license_name": "MIT-0",
        "security_audit_status": "pass",
        "downloads_30d": 259,
        "downloads_all_time": 259,
        "last_published_at": "2026-04-07",
        "files": [
            _skill_file(
                "SKILL.md", content_text="# 中国财税法律专业知识助手\n\n覆盖税务咨询、税务筹划、税务合规审查、税务争议处理等任务。\n", sort_order=10),
            _skill_file("skill-card.md",
                        content_text="法规优先、时效意识、风险提示、专业严谨。", sort_order=20),
            _skill_file("references", entry_type="dir", sort_order=30),
            _skill_file("references/tax-rates.md",
                        content_text="# Tax Rates\n中国主要税种税率与优惠政策速查。\n", sort_order=40),
        ],
        "versions": [
            _skill_version("v1.0.0", published_at="2026-04-07", is_latest=True, download_url="https://clawhub.ai/codefarmerman/skills/china-tax-law",
                           changelog_items=["Initial mirrored version for China tax law knowledge."], sort_order=10)
        ],
    },
    "receipt_assistant": {
        "publisher_name": "YY-C8",
        "publisher_handle": "@yy-c8",
        "install_command": "openclaw skills install @yy-c8/receipt-assistant",
        "skill_md_text": "# 报销助手\n\n自动处理报销票据：识别、提取信息、重命名、生成报表。\n\n## 工作流程\n1. 扫描目录\n2. 视觉识别\n3. 提取信息\n4. 重命名文件\n5. 生成报表\n",
        "skill_card": _skill_card(
            overview="报销票据处理助手，识别火车票、打车发票、酒店发票等并生成汇总。",
            publisher_name="YY-C8",
            publisher_handle="@yy-c8",
            version="v1.0.0",
            license_name="MIT-0",
            geography=["China"],
            use_type="Commercial / non-commercial",
            use_case="报销票据识别、字段提取、文件标准化重命名与 Excel 汇总。",
            review_before_use=[
                {"risk": "OCR 识别结果可能有误。", "mitigation": "关键字段如金额、日期、发票号应进行人工复核。"},
            ],
            output_behavior={
                "types": ["Structured JSON", "Excel"],
                "format": "JSON extraction + report export",
                "parameters": "2D",
                "side_effects": ["Renames local files", "Generates Excel report"],
            },
            references=[
                {"label": "ClawHub skill page",
                    "url": "https://clawhub.ai/yy-c8/skills/receipt-assistant", "type": "external"},
            ],
        ),
        "current_version": "v1.0.0",
        "license_name": "MIT-0",
        "security_audit_status": "pass",
        "downloads_30d": 97,
        "downloads_all_time": 97,
        "last_published_at": "2026-04-07",
        "files": [
            _skill_file(
                "SKILL.md", content_text="# 报销助手\n\n自动处理报销票据：识别、提取信息、重命名、生成报表。\n", sort_order=10),
            _skill_file(
                "skill-card.md", content_text="Receipt Assistant summary card", sort_order=20),
            _skill_file("references/rename-rules.md",
                        content_text="# Rename Rules\n火车票、打车发票、酒店发票的标准命名规则。\n", sort_order=30),
        ],
        "versions": [
            _skill_version("v1.0.0", published_at="2026-04-07", is_latest=True, download_url="https://clawhub.ai/yy-c8/skills/receipt-assistant",
                           changelog_items=["Initial mirrored version for receipt processing skill."], sort_order=10)
        ],
    },
    "aitaxs_assistant": {
        "publisher_name": "Internal Reference Mirror",
        "publisher_handle": "@internal-mirror",
        "install_command": "",
        "skill_md_text": "# AI TaxS Assistant\n\n## Overview\n面向个体户和小微企业的综合财税助手，覆盖工资个税、经营所得、申报提醒和节税建议。\n\n## Focus Areas\n- 工资与薪资个税计算与提醒\n- 个体户经营所得分析\n- 小微企业常见申报节点提示\n- 合规前提下的节税建议\n\n## Note\n该技能根据用户提供的外部摘要建立为内部参考镜像，原始外部页面当前未完成核验。\n",
        "skill_card": _skill_card(
            overview="个体户、小微企业日常财税助手，侧重个税、经营所得、申报提醒与轻量节税建议。",
            publisher_name="Internal Reference Mirror",
            publisher_handle="@internal-mirror",
            version="v0.1.0",
            license_name="Internal Reference",
            geography=["China"],
            use_type="Internal reference",
            use_case="为小微企业和个体户提供常见税务问答、提醒和合规建议。",
            review_before_use=[
                {"risk": "原始外部 skill 页面未核验。",
                    "mitigation": "当前条目仅作为内部参考镜像使用，正式建议需结合权威法规复核。"},
                {"risk": "节税建议易触及合规边界。", "mitigation": "输出时必须附加合规边界和风险提示。"},
            ],
            ethical_considerations="不得输出规避税法或违法逃税建议。",
            output_behavior={
                "types": ["Text", "Checklist"],
                "format": "Structured advisory text",
                "parameters": "2D",
                "side_effects": ["May trigger compliance reminders"],
            },
            references=[
                {"label": "User-provided source URL",
                    "url": "https://clawhub.ai/xhj2aidevs/skills/aitaxs-assistant", "type": "external"},
            ],
        ),
        "current_version": "v0.1.0",
        "license_name": "Internal Reference",
        "security_audit_status": "pending-verification",
        "downloads_30d": 0,
        "downloads_all_time": 0,
        "last_published_at": "2026-07-21",
        "files": [
            _skill_file(
                "SKILL.md", content_text="# AI TaxS Assistant\n面向个体户/小微企业的全能财税助手。\n", sort_order=10),
            _skill_file(
                "skill-card.md", content_text="Internal reference mirror for small-business tax assistant.", sort_order=20),
            _skill_file("references/source-note.md",
                        content_text="来源于用户提供的 skill 摘要，待外部页面核验。", sort_order=30),
        ],
        "versions": [
            _skill_version("v0.1.0", published_at="2026-07-21", is_latest=True, changelog_items=[
                           "Create internal reference mirror from user-provided summary."], sort_order=10),
        ],
    },
    "zhang_tax_law": {
        "publisher_name": "Internal Reference Mirror",
        "publisher_handle": "@internal-mirror",
        "install_command": "",
        "skill_md_text": "# Zhang Tax Law\n\n## Overview\n聚焦企业税务筹划、税务稽查、争议处理，覆盖企税汇算、增值税留抵退税、股权激励等主题。\n\n## Focus Areas\n- 企业税负优化与筹划边界\n- 税务稽查应对\n- 留抵退税与汇算清缴\n- 股权激励相关税务处理\n",
        "skill_card": _skill_card(
            overview="企业税务筹划与争议处理内部参考技能。",
            publisher_name="Internal Reference Mirror",
            publisher_handle="@internal-mirror",
            version="v0.1.0",
            license_name="Internal Reference",
            geography=["China"],
            use_type="Internal reference",
            use_case="辅助企业税务筹划、税务争议处理与稽查应对。",
            review_before_use=[
                {"risk": "原始外部 skill 内容未核验。", "mitigation": "正式使用前应结合权威法规和案例再次验证。"},
            ],
            output_behavior={
                "types": ["Analysis", "Risk Notes"],
                "format": "Structured advisory output",
                "parameters": "2D",
                "side_effects": ["May require manual legal review"],
            },
            references=[
                {"label": "User-provided source URL",
                    "url": "https://clawhub.ai/skills/skills/zhang-tax-law", "type": "external"},
            ],
        ),
        "current_version": "v0.1.0",
        "license_name": "Internal Reference",
        "security_audit_status": "pending-verification",
        "downloads_30d": 0,
        "downloads_all_time": 0,
        "last_published_at": "2026-07-21",
        "files": [
            _skill_file(
                "SKILL.md", content_text="# Zhang Tax Law\n企业税务筹划 + 稽查 + 争议处理。\n", sort_order=10),
            _skill_file(
                "skill-card.md", content_text="Internal reference mirror for enterprise tax planning and disputes.", sort_order=20),
        ],
        "versions": [
            _skill_version("v0.1.0", published_at="2026-07-21", is_latest=True, changelog_items=[
                           "Create internal reference mirror from user-provided summary."], sort_order=10),
        ],
    },
    "zhang_intl_tax_law": {
        "publisher_name": "Internal Reference Mirror",
        "publisher_handle": "@internal-mirror",
        "install_command": "",
        "skill_md_text": "# Zhang International Tax Law\n\n## Overview\n聚焦国际税法、转让定价、CRS 报告及跨境合规，适合出海公司与跨国集团。\n\n## Focus Areas\n- 国际税法规则解读\n- 转让定价与关联交易风险\n- CRS 报告与跨境信息交换\n- 跨国集团合规与申报义务\n",
        "skill_card": _skill_card(
            overview="国际税法与跨境税务专项内部参考技能。",
            publisher_name="Internal Reference Mirror",
            publisher_handle="@internal-mirror",
            version="v0.1.0",
            license_name="Internal Reference",
            geography=["Cross-border"],
            use_type="Internal reference",
            use_case="辅助出海公司和跨国集团处理国际税法、转让定价、CRS 及跨境合规问题。",
            review_before_use=[
                {"risk": "原始外部 skill 内容未核验。", "mitigation": "正式使用前需按目标国家法规与双边税收协定复核。"},
            ],
            output_behavior={
                "types": ["Analysis", "Compliance Checklist"],
                "format": "Structured cross-border advisory output",
                "parameters": "2D",
                "side_effects": ["May require jurisdiction-specific follow-up"],
            },
            references=[
                {"label": "User-provided source URL",
                    "url": "https://clawhub.ai/skills/skills/zhang-intl-tax-law", "type": "external"},
            ],
        ),
        "current_version": "v0.1.0",
        "license_name": "Internal Reference",
        "security_audit_status": "pending-verification",
        "downloads_30d": 0,
        "downloads_all_time": 0,
        "last_published_at": "2026-07-21",
        "files": [
            _skill_file(
                "SKILL.md", content_text="# Zhang International Tax Law\n国际税法 + 转让定价 + CRS 报告。\n", sort_order=10),
            _skill_file(
                "skill-card.md", content_text="Internal reference mirror for international tax law and transfer pricing.", sort_order=20),
        ],
        "versions": [
            _skill_version("v0.1.0", published_at="2026-07-21", is_latest=True, changelog_items=[
                           "Create internal reference mirror from user-provided summary."], sort_order=10),
        ],
    },
}

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
        "id": "aitaxs_assistant",
        "display_name": "个体户与小微企业财税助手",
        "category": "knowledge",
        "scene": "tax_small_business_advisory",
        "description": "面向个体户、小微企业的日常财税咨询与合规辅助，涵盖工资个税、经营所得、申报提醒与节税建议。",
        "source_url": "https://clawhub.ai/xhj2aidevs/skills/aitaxs-assistant",
        "reference_summary": "基于用户提供的 skill 介绍录入为内部参考镜像，适合小微企业财税咨询与日常申报提醒场景。",
        "tags_json": _json_text(["tax", "small-business", "individual", "advisory"]),
        "input_schema_json": _json_text(
            {"type": "object", "properties": {"question": {"type": "string"},
                                              "entity_type": {"type": "string"}, "tax_type": {"type": "string"}}}
        ),
        "output_schema_json": _json_text(
            {"type": "object", "properties": {"answer": {"type": "string"},
                                              "reminders": {"type": "array"}, "risk_level": {"type": "string"}}}
        ),
        "config_schema_json": _json_text({"type": "object", "properties": {"jurisdiction": {"type": "string"}}}),
        "sort_order": 35,
    },
    {
        "id": "zhang_tax_law",
        "display_name": "企业税务筹划与争议处理",
        "category": "knowledge",
        "scene": "tax_general_advisory",
        "description": "聚焦企业税务筹划、税务稽查应对、税务争议处理等高复杂度场景，覆盖企税汇算、留抵退税、股权激励等主题。",
        "source_url": "https://clawhub.ai/skills/skills/zhang-tax-law",
        "reference_summary": "基于用户提供的 skill 介绍录入为内部参考镜像，用于企业税务筹划和争议处理辅助。",
        "tags_json": _json_text(["tax", "planning", "dispute", "enterprise"]),
        "input_schema_json": _json_text(
            {"type": "object", "properties": {"question": {"type": "string"},
                                              "topic": {"type": "string"}, "company_stage": {"type": "string"}}}
        ),
        "output_schema_json": _json_text(
            {"type": "object", "properties": {"analysis": {"type": "string"},
                                              "risk_points": {"type": "array"}, "citations": {"type": "array"}}}
        ),
        "config_schema_json": _json_text({"type": "object", "properties": {"citation_required": {"type": "boolean"}}}),
        "sort_order": 40,
    },
    {
        "id": "zhang_intl_tax_law",
        "display_name": "国际税法与转让定价",
        "category": "knowledge",
        "scene": "tax_cross_border_advisory",
        "description": "面向出海公司和跨国集团的国际税法、转让定价、CRS 报告与跨境合规咨询。",
        "source_url": "https://clawhub.ai/skills/skills/zhang-intl-tax-law",
        "reference_summary": "基于用户提供的 skill 介绍录入为内部参考镜像，适用于国际税法和跨境税务专项场景。",
        "tags_json": _json_text(["tax", "international", "transfer-pricing", "cross-border"]),
        "input_schema_json": _json_text(
            {"type": "object", "properties": {"question": {"type": "string"}, "country_pair": {
                "type": "string"}, "business_model": {"type": "string"}}}
        ),
        "output_schema_json": _json_text(
            {"type": "object", "properties": {"analysis": {"type": "string"}, "cross_border_risks": {
                "type": "array"}, "filing_obligations": {"type": "array"}}}
        ),
        "config_schema_json": _json_text({"type": "object", "properties": {"transfer_pricing_mode": {"type": "string"}}}),
        "sort_order": 45,
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


def _get_skill_seed_detail(item: Dict[str, Any]) -> Dict[str, Any]:
    detail = BUILTIN_SKILL_DETAIL_SEEDS.get(str(item.get("id") or "").strip())
    if detail:
        return detail
    return _build_default_skill_detail(item)


def _save_skill_detail(
    cur,
    skill_id: str,
    detail: Dict[str, Any],
    now: str,
) -> None:
    cur.execute(
        """
        UPDATE audit_skill
        SET publisher_name = ?, publisher_handle = ?, install_command = ?, skill_md_text = ?,
            skill_card_json = ?, current_version = ?, license_name = ?, security_audit_status = ?,
            downloads_30d = ?, downloads_all_time = ?, last_published_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            detail.get("publisher_name", ""),
            detail.get("publisher_handle", ""),
            detail.get("install_command", ""),
            detail.get("skill_md_text", ""),
            _json_text(detail.get("skill_card", {})),
            detail.get("current_version", ""),
            detail.get("license_name", ""),
            detail.get("security_audit_status", ""),
            int(detail.get("downloads_30d", 0) or 0),
            int(detail.get("downloads_all_time", 0) or 0),
            detail.get("last_published_at", ""),
            now,
            skill_id,
        ),
    )
    cur.execute("DELETE FROM audit_skill_file WHERE skill_id = ?", (skill_id,))
    for index, file_item in enumerate(detail.get("files", []), start=1):
        file_path = str(file_item.get("path") or "").strip()
        if not file_path:
            continue
        cur.execute(
            """
            INSERT INTO audit_skill_file(
                id, skill_id, path, entry_type, size_bytes, branch_name,
                content_text, sort_order, created_at, updated_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                f"{skill_id}::file::{index}",
                skill_id,
                file_path,
                str(file_item.get("entry_type") or "file"),
                int(file_item.get("size_bytes", 0) or 0),
                str(file_item.get("branch_name") or ""),
                str(file_item.get("content_text") or ""),
                int(file_item.get("sort_order", index) or index),
                now,
                now,
            ),
        )
    cur.execute(
        "DELETE FROM audit_skill_version WHERE skill_id = ?", (skill_id,))
    for index, version_item in enumerate(detail.get("versions", []), start=1):
        version_tag = str(version_item.get("version_tag") or "").strip()
        if not version_tag:
            continue
        cur.execute(
            """
            INSERT INTO audit_skill_version(
                id, skill_id, version_tag, release_label, published_at, is_latest,
                download_url, changelog_text, changelog_json, sort_order, created_at, updated_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                f"{skill_id}::version::{index}",
                skill_id,
                version_tag,
                str(version_item.get("release_label") or ""),
                str(version_item.get("published_at") or ""),
                1 if version_item.get("is_latest") else 0,
                str(version_item.get("download_url") or ""),
                str(version_item.get("changelog_text") or ""),
                _json_text(version_item.get("changelog_json", [])),
                int(version_item.get("sort_order", index) or index),
                now,
                now,
            ),
        )


def ensure_audit_capabilities_seeded(cfg: Dict[str, Any]) -> None:
    now = _utc_now_iso()
    conn = get_conn(cfg)
    cur = conn.cursor()
    try:
        for item in BUILTIN_SKILL_SEEDS:
            cur.execute("SELECT * FROM audit_skill WHERE id = ?",
                        (item["id"],))
            existing_row = cur.fetchone()
            should_insert = existing_row is None
            if should_insert:
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
                cur.execute(
                    "SELECT * FROM audit_skill WHERE id = ?", (item["id"],))
                existing_row = cur.fetchone()
            existing_item = _row_to_skill(dict(existing_row))
            detail = _get_skill_seed_detail(item)
            cur.execute(
                "SELECT COUNT(1) FROM audit_skill_file WHERE skill_id = ?", (
                    item["id"],)
            )
            file_count = int((cur.fetchone() or [0])[0] or 0)
            cur.execute(
                "SELECT COUNT(1) FROM audit_skill_version WHERE skill_id = ?", (
                    item["id"],)
            )
            version_count = int((cur.fetchone() or [0])[0] or 0)
            needs_detail_seed = should_insert or (
                not str(existing_item.get("publisher_name") or "").strip()
                and not str(existing_item.get("skill_md_text") or "").strip()
                and file_count == 0
                and version_count == 0
            )
            if needs_detail_seed:
                _save_skill_detail(cur, item["id"], detail, now)

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
    item["skill_card"] = _safe_json_loads(
        item.pop("skill_card_json", "{}"), {})
    item["downloads_30d"] = int(item.get("downloads_30d") or 0)
    item["downloads_all_time"] = int(item.get("downloads_all_time") or 0)
    return item


def _row_to_skill_file(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    item["size_bytes"] = int(item.get("size_bytes") or 0)
    item["sort_order"] = int(item.get("sort_order") or 0)
    return item


def _row_to_skill_version(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    item["is_latest"] = bool(item.get("is_latest"))
    item["sort_order"] = int(item.get("sort_order") or 0)
    item["changelog_items"] = _safe_json_loads(
        item.pop("changelog_json", "[]"), [])
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


def _attach_skill_detail_meta(cur, item: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(item)
    skill_id = str(item.get("id") or "")
    cur.execute(
        """
        SELECT *
        FROM audit_skill_file
        WHERE skill_id = ?
        ORDER BY sort_order ASC, path ASC
        """,
        (skill_id,),
    )
    out["files"] = [_row_to_skill_file(dict(row)) for row in cur.fetchall()]
    cur.execute(
        """
        SELECT *
        FROM audit_skill_version
        WHERE skill_id = ?
        ORDER BY sort_order ASC, published_at DESC, version_tag DESC
        """,
        (skill_id,),
    )
    out["versions"] = [_row_to_skill_version(
        dict(row)) for row in cur.fetchall()]
    return out


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
    if not row:
        conn.close()
        return None
    item = _attach_skill_detail_meta(cur, _row_to_skill(dict(row)))
    conn.close()
    return item


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


def _normalize_optional_int(value: Any, field_name: str, default: int = 0) -> int:
    if value in (None, ""):
        return int(default)
    try:
        return int(value)
    except Exception as exc:
        raise ValueError(f"{field_name} must be an integer") from exc


def _normalize_skill_files(
    value: Any,
    default: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    raw_items = default if value is None else value
    if raw_items in (None, ""):
        raw_items = []
    if not isinstance(raw_items, list):
        raise ValueError("files must be a list")
    items: List[Dict[str, Any]] = []
    seen_paths = set()
    for index, raw_item in enumerate(raw_items, start=1):
        if not isinstance(raw_item, dict):
            raise ValueError("files contains invalid item")
        path = str(raw_item.get("path") or "").strip()
        if not path:
            raise ValueError("files.path is required")
        if path in seen_paths:
            raise ValueError(f"duplicate file path: {path}")
        seen_paths.add(path)
        entry_type = str(raw_item.get("entry_type") or "file").strip().lower()
        if entry_type not in {"file", "dir"}:
            raise ValueError("files.entry_type must be file or dir")
        content_text = "" if entry_type == "dir" else str(
            raw_item.get("content_text") or "")
        size_value = raw_item.get("size_bytes")
        size_bytes = len(content_text.encode("utf-8")) if size_value in (None, "") else _normalize_optional_int(
            size_value, "files.size_bytes", 0
        )
        items.append(
            {
                "path": path,
                "entry_type": entry_type,
                "content_text": content_text,
                "size_bytes": max(size_bytes, 0),
                "branch_name": str(raw_item.get("branch_name") or ""),
                "sort_order": _normalize_optional_int(
                    raw_item.get("sort_order"), "files.sort_order", index
                ),
            }
        )
    return items


def _normalize_changelog_items(raw_item: Dict[str, Any]) -> List[str]:
    changelog_items = raw_item.get("changelog_items")
    if changelog_items is None:
        text = str(raw_item.get("changelog_text") or "")
        lines = []
        for line in text.splitlines():
            cleaned = line.strip()
            if not cleaned:
                continue
            if cleaned.startswith("- "):
                cleaned = cleaned[2:].strip()
            lines.append(cleaned)
        return lines
    if not isinstance(changelog_items, list):
        raise ValueError("versions.changelog_items must be a list")
    items: List[str] = []
    for value in changelog_items:
        text = str(value or "").strip()
        if text:
            items.append(text)
    return items


def _normalize_skill_versions(
    value: Any,
    default: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    raw_items = default if value is None else value
    if raw_items in (None, ""):
        raw_items = []
    if not isinstance(raw_items, list):
        raise ValueError("versions must be a list")
    items: List[Dict[str, Any]] = []
    seen_tags = set()
    latest_found = False
    for index, raw_item in enumerate(raw_items, start=1):
        if not isinstance(raw_item, dict):
            raise ValueError("versions contains invalid item")
        version_tag = str(raw_item.get("version_tag") or "").strip()
        if not version_tag:
            raise ValueError("versions.version_tag is required")
        if version_tag in seen_tags:
            raise ValueError(f"duplicate version tag: {version_tag}")
        seen_tags.add(version_tag)
        changelog_items = _normalize_changelog_items(raw_item)
        is_latest = bool(raw_item.get("is_latest"))
        latest_found = latest_found or is_latest
        items.append(
            {
                "version_tag": version_tag,
                "release_label": str(raw_item.get("release_label") or "").strip(),
                "published_at": str(raw_item.get("published_at") or "").strip(),
                "is_latest": is_latest,
                "download_url": str(raw_item.get("download_url") or "").strip(),
                "changelog_items": changelog_items,
                "changelog_text": "\n".join(f"- {item}" for item in changelog_items),
                "changelog_json": changelog_items,
                "sort_order": _normalize_optional_int(
                    raw_item.get("sort_order"), "versions.sort_order", index
                ),
            }
        )
    if items and not latest_found:
        items[0]["is_latest"] = True
        if not items[0]["release_label"]:
            items[0]["release_label"] = "Latest"
    return items


def _upsert_detail_file(
    files: List[Dict[str, Any]],
    *,
    path: str,
    content_text: str,
    sort_order: int,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    matched = False
    encoded_size = len(content_text.encode("utf-8"))
    for item in files:
        if str(item.get("path") or "") == path:
            next_item = dict(item)
            next_item["entry_type"] = "file"
            next_item["content_text"] = content_text
            next_item["size_bytes"] = encoded_size
            next_item["sort_order"] = int(item.get("sort_order") or sort_order)
            out.append(next_item)
            matched = True
        else:
            out.append(dict(item))
    if not matched:
        out.append(
            {
                "path": path,
                "entry_type": "file",
                "content_text": content_text,
                "size_bytes": encoded_size,
                "branch_name": "main",
                "sort_order": sort_order,
            }
        )
    return sorted(
        out,
        key=lambda item: (
            int(item.get("sort_order") or 0),
            str(item.get("path") or ""),
        ),
    )


def _normalize_skill_detail_payload(
    payload: Dict[str, Any],
    normalized_skill: Dict[str, Any],
    existing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    current = existing or {}
    skill_card = _normalize_json_schema(
        payload.get("skill_card", current.get("skill_card", {})),
        "skill_card",
    )
    detail = {
        "publisher_name": str(
            payload.get("publisher_name", current.get(
                "publisher_name", "")) or ""
        ).strip(),
        "publisher_handle": str(
            payload.get("publisher_handle", current.get(
                "publisher_handle", "")) or ""
        ).strip(),
        "install_command": str(
            payload.get("install_command", current.get(
                "install_command", "")) or ""
        ).strip(),
        "skill_md_text": str(
            payload.get("skill_md_text", current.get(
                "skill_md_text", "")) or ""
        ),
        "skill_card": skill_card,
        "current_version": str(
            payload.get("current_version", current.get(
                "current_version", "")) or ""
        ).strip(),
        "license_name": str(
            payload.get("license_name", current.get("license_name", "")) or ""
        ).strip(),
        "security_audit_status": str(
            payload.get(
                "security_audit_status",
                current.get("security_audit_status", ""),
            ) or ""
        ).strip(),
        "downloads_30d": _normalize_optional_int(
            payload.get("downloads_30d", current.get("downloads_30d", 0)),
            "downloads_30d",
            0,
        ),
        "downloads_all_time": _normalize_optional_int(
            payload.get("downloads_all_time", current.get(
                "downloads_all_time", 0)),
            "downloads_all_time",
            0,
        ),
        "last_published_at": str(
            payload.get("last_published_at", current.get(
                "last_published_at", "")) or ""
        ).strip(),
    }
    detail["files"] = _normalize_skill_files(
        payload.get("files"),
        default=current.get("files", []),
    )
    detail["versions"] = _normalize_skill_versions(
        payload.get("versions"),
        default=current.get("versions", []),
    )
    if not detail["current_version"] and detail["versions"]:
        latest = next(
            (item for item in detail["versions"] if item.get("is_latest")),
            detail["versions"][0],
        )
        detail["current_version"] = str(latest.get("version_tag") or "")
    if not detail["publisher_name"]:
        detail["publisher_name"] = "AI-Law-Assistant"
    if not detail["publisher_handle"]:
        detail["publisher_handle"] = "@system"
    if not detail["license_name"]:
        detail["license_name"] = "Internal Reference"
    if not detail["skill_md_text"]:
        detail["skill_md_text"] = _build_default_skill_md(
            {
                "id": normalized_skill["id"],
                "display_name": normalized_skill["display_name"],
                "category": normalized_skill["category"],
                "scene": normalized_skill["scene"],
                "description": normalized_skill["description"],
                "source_url": normalized_skill["source_url"],
                "reference_summary": normalized_skill["reference_summary"],
                "tags_json": _json_text(normalized_skill["tags"]),
            }
        )
    detail["files"] = _upsert_detail_file(
        detail["files"],
        path="SKILL.md",
        content_text=detail["skill_md_text"],
        sort_order=10,
    )
    detail["files"] = _upsert_detail_file(
        detail["files"],
        path="skill-card.md",
        content_text=_json_text(detail["skill_card"]),
        sort_order=20,
    )
    return detail


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
        "id": str(current.get("id") or payload.get("id") or "").strip(),
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
    item = _attach_skill_detail_meta(cur, item)
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
    detail_seed_item = {
        "id": skill_id,
        "display_name": normalized["display_name"],
        "category": normalized["category"],
        "scene": normalized["scene"],
        "description": normalized["description"],
        "source_url": normalized["source_url"],
        "reference_summary": normalized["reference_summary"],
        "tags_json": _json_text(normalized["tags"]),
    }
    detail_payload = _normalize_skill_detail_payload(
        payload,
        normalized,
        existing=_build_default_skill_detail(detail_seed_item),
    )
    _save_skill_detail(
        cur,
        skill_id,
        detail_payload,
        now,
    )
    conn.commit()
    cur.execute("SELECT * FROM audit_skill WHERE id = ?", (skill_id,))
    row = cur.fetchone()
    item = _attach_skill_reference_meta(cur, _row_to_skill(dict(row)))
    item = _attach_skill_detail_meta(cur, item)
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
    cur.execute("SELECT * FROM audit_skill WHERE id = ?", (skill_id,))
    saved_row = _row_to_skill(dict(cur.fetchone()))
    existing_detail = _attach_skill_detail_meta(cur, dict(saved_row))
    _save_skill_detail(
        cur,
        skill_id,
        _normalize_skill_detail_payload(
            payload, normalized, existing=existing_detail),
        now,
    )
    conn.commit()
    cur.execute("SELECT * FROM audit_skill WHERE id = ?", (skill_id,))
    saved = _attach_skill_reference_meta(
        cur, _row_to_skill(dict(cur.fetchone())))
    saved = _attach_skill_detail_meta(cur, saved)
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
