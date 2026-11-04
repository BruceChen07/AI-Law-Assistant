"""
Contract Audit Facade.
职责: 作为合同审计模块的统一入口，封装 IO 操作并调用拆分后的子模块。
"""
import os
import time
import uuid
import hashlib
import json
import re
import socket
import structlog
from typing import Dict, Any, Optional, Callable, List

from app.core.token_utils import estimate_text_tokens
from app.services.audit_normalizer import (
    build_coverage_manifest,
    normalize_units,
)
from app.services.audit_planner import (
    AuditPolicy,
    plan_audit,
    replan_incomplete_chunk,
)
from app.services.audit_prompt_v2 import (
    LlamaCppTokenCounter,
    MessageTokenCounter,
)
from app.services.audit_source import source_from_text
from app.services.audit_validation import validate_model_json
from app.services.final_report_service import build_contract_final_report
from app.services.result_aggregator import aggregate_chunk_audit_results
from app.services.review_retry import build_reliability_review_plan, build_reliability_summary
from app.core.utils import extract_text_with_config
from app.services.audit_token_policy import (
    build_contract_audit_budget,
    get_audit_token_policy,
    plan_clause_groups,
)
from app.services.audit_utils import _safe_int, _normalize_citation_item, _enrich_citations
from app.services.audit_retrieval import _normalize_retrieval_options, _retrieve_regulation_evidence
from app.services.contract_audit_modules.clause_builder import build_preview_clauses
from app.services.contract_audit_modules import memory_pipeline as memory_pipeline_module
from app.services.contract_audit_modules.memory_pipeline import execute_memory_audit
from app.services.contract_audit_modules.result_assembler import attach_risk_locations
from app.services.contract_audit_modules import chunk_store
from app.services.contract_audit_modules.trace_writer import write_audit_trace, write_round_trace, trace_clip
from app.services.llm_progress import (
    build_llm_progress_detail,
    build_llm_progress_message,
    compute_llm_progress_percent,
    report_progress,
)
from app.memory_system.search import HybridSearcher
from app.memory_system.experience_repo import save_audit_episode
from app.core.llm_trace import new_trace_id

logger = structlog.get_logger(__name__)


def _build_preview_clauses(text: str):
    return build_preview_clauses(text)


def _attach_risk_locations(audit, clauses):
    return attach_risk_locations(audit, clauses)


def _chat_with_task_profile(llm, messages, task_profile: str, overrides=None):
    if hasattr(llm, "chat_with_profile"):
        return llm.chat_with_profile(messages, task_profile, overrides=overrides)
    next_overrides = dict(overrides or {})
    next_overrides["_task_profile"] = task_profile
    return llm.chat(messages, overrides=next_overrides)


def _get_memory_embedder(lang: str = "zh", cfg: Optional[Dict[str, Any]] = None):
    getter = getattr(memory_pipeline_module, "get_memory_embedder", None)
    if not callable(getter):
        return None
    try:
        return getter(lang, cfg=cfg)
    except TypeError:
        try:
            return getter(lang)
        except TypeError:
            try:
                return getter()
            except Exception:
                return None
        except Exception:
            return None
    except Exception:
        return None


def _get_memory_runtime_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    raw = cfg.get("memory_runtime_config") if isinstance(
        cfg.get("memory_runtime_config"), dict) else {}
    mode = str(raw.get("memory_mode_when_disabled")
               or "classic").strip().lower()
    if mode not in {"classic"}:
        mode = "classic"
    return {
        "memory_module_enabled": bool(raw.get("memory_module_enabled", True)),
        "memory_mode_when_disabled": mode,
        "memory_disable_fallback_on_error": bool(raw.get("memory_disable_fallback_on_error", True)),
        "memory_max_prompt_chars_per_clause": max(300, int(raw.get("memory_max_prompt_chars_per_clause") or 2400)),
    }


def _get_memory_temporary_disable_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    raw = cfg.get("memory_temporary_disable") if isinstance(
        cfg.get("memory_temporary_disable"), dict) else {}
    fallback_mode = str(raw.get("fallback_mode") or "classic").strip().lower()
    if fallback_mode not in {"classic"}:
        fallback_mode = "classic"
    return {
        "enabled": bool(raw.get("enabled", False)),
        "fallback_mode": fallback_mode,
        "reason": str(raw.get("reason") or "edge_llm_context_limit").strip() or "edge_llm_context_limit",
        "trigger_source": str(raw.get("trigger_source") or "config.memory_temporary_disable").strip() or "config.memory_temporary_disable",
    }


def _load_llm_json_object(raw_text: str) -> Dict[str, Any]:
    s = str(raw_text or "").strip()
    if not s:
        return {}
    fenced = re.sub(r"^\s*```(?:json)?\s*", "", s,
                    count=1, flags=re.IGNORECASE)
    fenced = re.sub(r"\s*```\s*$", "", fenced, count=1,
                    flags=re.IGNORECASE).strip()
    candidates: List[str] = [x for x in [fenced, s] if x]
    if fenced:
        left = fenced.find("{")
        right = fenced.rfind("}")
        if left >= 0 and right > left:
            obj_text = fenced[left:right + 1].strip()
            if obj_text and obj_text not in candidates:
                candidates.append(obj_text)
    for item in candidates:
        try:
            parsed = json.loads(item)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue
    return {}


def _build_classic_prompt_payload(
    cfg: Dict[str, Any],
    text: str,
    lang: str,
    preview_clauses: List[Dict[str, Any]],
    evidence_items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    norm_lang = "en" if str(lang or "").lower() == "en" else "zh"
    allowed_citation_ids = {
        str(it.get("citation_id") or "").strip()
        for it in evidence_items
        if str(it.get("citation_id") or "").strip()
    }
    evidence_items_used = list(evidence_items or [])[:24]
    lines = []
    evidence_content_truncated_count = 0
    reference_lines = []
    for idx, it in enumerate(evidence_items_used, start=1):
        cid = str(it.get("citation_id") or "").strip()
        law = str(it.get("law_title") or it.get("title") or "").strip()
        article = str(it.get("article_no") or "").strip()
        if cid and law:
            lines.append(f"- C{idx} [{cid}] {law} {article}".strip())
        content = str(it.get("content") or it.get("source_text")
                      or it.get("excerpt") or "").strip()
        if len(content) > 1200:
            evidence_content_truncated_count += 1
        header = f"[E{idx}] {law} {article}".strip()
        if content:
            reference_lines.append(f"{header}\n{content[:1200]}")
        elif law:
            reference_lines.append(header)
    whitelist_text = "\n".join(lines) if lines else "-"
    reference_evidence_text = "\n\n".join(
        reference_lines) if reference_lines else ""
    if reference_evidence_text:
        reference_evidence_text = (
            "参考法规证据（共{}条）:\n".format(len(reference_lines))
            + reference_evidence_text
        )
    max_clause_chars = int(_get_memory_runtime_config(
        cfg).get("memory_max_prompt_chars_per_clause") or 2400)
    prompt_clauses = list(preview_clauses or [])[:10]
    clause_lines = []
    clause_chars_truncated_count = 0
    for clause in prompt_clauses:
        clause_id = str(clause.get("clause_id") or "")
        title = str(clause.get("title") or clause.get("clause_path") or "")
        body = str(clause.get("clause_text") or clause.get("text") or "")
        if len(body) > max_clause_chars:
            clause_chars_truncated_count += 1
        clause_lines.append(
            f"[{clause_id}] {title}\n{body[:max_clause_chars]}")
    clause_text = "\n\n".join(clause_lines) if clause_lines else str(
        text or "")[:max_clause_chars]
    full_ctx_budget = max(
        1500, int(cfg.get("memory_full_context_budget_chars") or 6000))
    full_text_context = str(text or "")[:full_ctx_budget]
    CLASSIC_CHECKLIST_ZH = (
        "【境内合同税务与合规审核要点 Checklist】请严格按照以下四大类逐项对照审查，不得遗漏：\n\n"
        "一、合同标的与税率匹配\n"
        "  1) 明确合同标的性质（货物销售 / 服务提供 / 不动产 / 无形资产 / 加工修理修配劳务），匹配适用增值税税率及征收率（13% / 9% / 6% / 5% / 3% / 1% 等），若税率与标的不匹配需标记风险；\n"
        "  2) 避免「模糊标的」导致税率适用争议：涉及混合销售、兼营业务时，必须核查是否明确划分不同标的价款及对应税率，未明确划分属高风险。\n\n"
        "二、价款与税额约定\n"
        "  1) 明确价款是否含税，清晰区分「含税价（含增值税）」与「不含税价 + 增值税税额」两种表述方式，未写明是否含税即属风险；\n"
        "  2) 是否约定「税率变动条款」：若国家税收政策发生变化，合同价款中不含税部分保持不变、增值税部分按新政策相应调整。缺少此约定属于可优化项。\n\n"
        "三、发票条款合规性\n"
        "  1) 是否约定提供符合国家税收法律法规规定的**增值税专用发票**（除非明确适用简易征收或免税项目）；\n"
        "  2) 开票信息准确性条款：乙方（开票方）提供的发票中名称、纳税人识别号、地址电话、开户行及账号必须与甲方（受票方）提供的完全一致；如发票不合规（如项目不齐全、字迹不清、压线错格、商品税收分类编码错误、虚开等），乙方必须在接到通知后限期内无偿重开或换开，并承担由此产生的一切损失；\n"
        "  3) 涉及违约金、赔偿金、质保金、罚款等：是否明确区分是否属于增值税「价外费用」，凡价外费用是否明确需一并开具发票，未区分属风险；\n"
        "  4) 合同变更、部分履行、提前终止时的发票处理：是否明确红字增值税专用发票的开具流程、时限及配合义务；\n"
        "  5) 提供不合规发票的赔偿条款：如乙方提供不合规发票导致甲方无法抵扣或被税务机关查处，乙方需赔偿甲方由此产生的全部损失，包括但不限于：\n"
        "     - 无法抵扣的增值税进项税额损失；\n"
        "     - 对应补缴的城市维护建设税、教育费附加、地方教育附加等附加税费损失；\n"
        "     - 因成本无法税前扣除导致多缴的企业所得税税款损失；\n"
        "     - 税务机关处以的滞纳金、罚款及合理的维权律师费。\n"
        "  缺少上述任何一项赔偿约定均属于风险。\n\n"
        "四、业务实质与形式一致性（防范虚开）\n"
        "  1) 是否体现「三流/四流一致」原则：资金流向（付款）、发票流向（开票）、货物流向（发货/收货/签收）、合同流（签约主体）必须相互对应，任何可能导致主体不一致的条款（如第三方代付、第三方收货、第三方开票、挂靠经营等）未作合规披露与约定即属风险；\n"
        "  2) 合同条款必须与真实商业目的匹配：警惕并标记任何可能被认定为「阴阳合同」「拆分合同」「循环交易」「走账开票」等形式规避税负的条款，确保税务处理完全基于真实业务实质。\n\n"
    )
    CLASSIC_CHECKLIST_EN = (
        "[Domestic Contract Tax & Compliance Audit Checklist] Please strictly review against the following four categories without omission:\n\n"
        "I. Contract Subject Matter & Applicable Tax Rate Matching\n"
        "  1) Explicitly identify the nature of the contractual subject matter (goods sale / service provision / immovable property / intangible asset / processing, repair & replacement services) and verify that the applicable VAT tax rate or levy rate (13% / 9% / 6% / 5% / 3% / 1%, etc.) matches; flag as a risk if there is a mismatch.\n"
        "  2) Avoid tax-rate application disputes caused by ambiguous subject matters: for mixed sales and concurrent businesses, verify whether the consideration for each distinct subject matter and its corresponding tax rate are clearly demarcated. Absence of such demarcation is a high-risk issue.\n\n"
        "II. Pricing & Tax Amount Arrangements\n"
        "  1) Explicitly state whether the contract price is tax-inclusive or tax-exclusive, clearly distinguishing between 'tax-inclusive price (including VAT)' and 'tax-exclusive price + VAT amount'. Failure to specify whether the price includes tax is a risk.\n"
        "  2) Include a 'tax rate change clause': if national tax policies change, the tax-exclusive portion of the contract price remains unchanged, while the VAT portion is adjusted accordingly per the new policy. Absence of such provision is an actionable improvement item.\n\n"
        "III. Invoice Clause Compliance\n"
        "  1) Agree to provide VAT Special Invoices compliant with applicable tax laws and regulations (unless simplified levy or tax-exempt treatment is explicitly applicable).\n"
        "  2) Accuracy of invoice-issuing information: the Seller (invoice-issuing party) must ensure that the name, taxpayer identification number, address & telephone, and bank name & account number on the invoice exactly match those provided by the Buyer (invoice-receiving party). In case of non-compliant invoices (incomplete items, illegible handwriting, overstepped margins, incorrect commodity tax classification coding, falsely-issued invoices, etc.), the Seller shall reissue or replace the invoice free of charge within the specified period upon receipt of notice and bear all consequential losses.\n"
        "  3) For liquidated damages, compensation, retention money, administrative penalties, etc.: explicitly distinguish whether they constitute 'additional consideration outside the price' subject to VAT; where they constitute additional consideration, explicitly state that an invoice must be issued together with the principal amount. Absence of such distinction is a risk.\n"
        "  4) Invoice handling upon contract modification, partial performance, or early termination: explicitly stipulate the process, timeframe and cooperation obligations for issuing RED VAT Special Invoices.\n"
        "  5) Indemnification clause for non-compliant invoices: if the Seller provides a non-compliant invoice causing the Buyer to be unable to claim input tax credit or be investigated by tax authorities, the Seller shall compensate the Buyer for ALL resulting losses including but not limited to:\n"
        "     - Non-recoverable VAT input tax losses;\n"
        "     - Corresponding supplementary payments of urban maintenance and construction tax, education surcharge, local education surcharge and other surcharges;\n"
        "     - Additional corporate income tax caused by non-deductible costs for CIT purposes;\n"
        "     - Late-payment surcharges, administrative fines imposed by tax authorities, and reasonable legal fees.\n"
        "  Lack of any indemnification item above constitutes a risk.\n\n"
        "IV. Consistency of Business Substance and Legal Form (Anti- False Issuance)\n"
        "  1) Adhere to the 'Three / Four Consistencies' principle: the flow of funds (payment), the flow of invoices (issuance & receipt), the flow of goods (delivery / receipt / sign-off), and the contractual flow (signing parties) must all correspond to one another. Any clauses potentially causing inconsistency among parties (third-party payment, third-party receipt of goods, third-party invoicing, affiliated/affiliated business operations, etc.) without proper compliant disclosure and agreement is a risk.\n"
        "  2) Contract terms must match the genuine commercial purpose: be alert to and flag any clauses likely to be characterized as 'yin-yang dual contracts', 'contract splitting', 'circular transactions' or 'invoice-issuing without genuine business'; all tax treatments must be firmly grounded in the true business substance.\n\n"
    )
    CLASSIC_MANDATORY_RULES_ZH = (
        "【最高优先级强制审核规则：合同标的与增值税税率】\n"
        "（你必须首先识别合同的真实交易性质，再审核合同约定的增值税税率。"
        "不得仅根据合同标题、合同名称或对方提供的税率作出判断，"
        "必须结合合同标的、服务内容、交付成果、验收标准、付款依据、"
        "商品所有权转移及实际业务实质进行判断。）\n\n"
        "第一步：识别合同交易类型\n"
        "必须明确判断合同属于以下哪一种或哪几种交易：\n"
        "1. 货物销售；\n"
        "2. 一般服务；\n"
        "3. 加工、修理修配服务；\n"
        "4. 有形动产租赁；\n"
        "5. 交通运输、邮政、基础电信、建筑或不动产租赁服务；\n"
        "6. 无形资产转让或许可使用；\n"
        "7. 不动产销售或土地使用权转让；\n"
        "8. 混合合同或包含两个以上独立应税交易；\n"
        "9. 无法根据现有合同内容确定交易性质。\n\n"
        "第二步：强制核验增值税税率\n"
        "在交易双方均为中国境内增值税一般纳税人，"
        "且不适用免税、零税率、简易计税或其他特殊政策的前提下：\n"
        "1. 一般货物销售通常适用13%增值税税率；\n"
        "2. 一般服务及无形资产通常适用6%增值税税率；\n"
        "3. 加工、修理修配服务及有形动产租赁通常适用13%增值税税率；\n"
        "4. 交通运输、邮政、基础电信、建筑、不动产租赁服务等通常适用9%增值税税率；\n"
        "5. 部分特定货物可能适用9%；\n"
        "6. 小规模纳税人、简易计税、免税、零税率或其他特殊事项，"
        "应根据现行有效税收法规单独判断。\n\n"
        "第三步：执行税率交叉验证\n"
        "必须同时比较以下三项：\n"
        "1. 根据合同业务实质判断的应适用税率；\n"
        "2. 合同正文、附件、报价单或结算条款约定的税率；\n"
        "3. 合同约定开具的发票类型、开票项目及税率。\n"
        "如果三者不一致，必须列为税务风险，不得遗漏。\n\n"
        "特别检查以下情形：\n"
        "1. 合同实质为货物销售，但约定按照6%开具服务发票；\n"
        "2. 合同实质为一般服务，但约定按照13%开具货物发票；\n"
        "3. 合同名称为服务合同，但实际包含设备、商品或其他货物交付；\n"
        "4. 合同名称为采购合同或购销合同，但实际包含独立的咨询、推广、维护、技术或管理服务；\n"
        "5. 合同同时包含货物和服务，但未分别列示标的、金额、税率及开票项目；\n"
        "6. 合同价款表、付款条款、发票条款或附件中的税率相互矛盾；\n"
        "7. 合同未约定税率，或仅约定按国家规定开票，导致税率和税负承担不明确；\n"
        "8. 发票项目名称与合同约定及真实业务内容不一致；\n"
        "9. 供应商纳税人身份、计税方法或优惠政策不明确，无法验证合同税率；\n"
        "10. 以人为拆分合同、改变合同名称或改变开票项目的方式适用较低税率。\n\n"
        "视同销售专项判断\n"
        "审核是否存在视同应税交易风险，重点关注：\n"
        "- 赠品\n- 样品\n- 促销礼品\n- 员工福利\n- 对外捐赠\n"
        "- 赞助实物提供\n- 无偿转让无形资产\n"
        "无偿提供服务原则上不属于视同应税交易，"
        "不得仅因免费提供服务即判定存在视同应税交易风险。\n\n"
        "混合合同专项判断\n"
        "如一份合同同时包含货物和服务，必须判断属于：\n"
        "1. 两项或多项可以独立区分的应税交易；\n"
        "2. 一项交易中包含多个组成部分，应按主要业务确定税率；\n"
        "3. 合同没有分别列明货物与服务金额，存在无法分别核算或从高适用税率的风险。\n"
        "如货物和服务可以独立区分，应建议合同分别列明：\n"
        "1. 货物名称、数量及不含税金额；\n"
        "2. 服务内容、期间及不含税金额；\n"
        "3. 各自适用的增值税税率；\n"
        "4. 各自对应的增值税税额；\n"
        "5. 发票开具项目及开票时间。\n\n"
        "其他税务审核（完成税率核验后继续）：\n"
        "1. 合同价格是否明确为含税价或不含税价；\n"
        "2. 税款是否单独列示，金额计算是否正确；\n"
        "3. 税费承担主体及税率变化调整机制是否明确；\n"
        "4. 发票类型、开票主体、开票内容及开票时间是否合规；\n"
        "5. 开票条件是否与付款、验收及纳税义务发生时间匹配；\n"
        "6. 是否存在无法抵扣进项税额的风险；\n"
        "7. 是否存在成本费用不能在企业所得税前扣除的风险；\n"
        "8. 预付款、押金、保证金、质保金、违约金、赔偿金、折扣及返利的税务处理是否正确。\n\n"
    )
    CLASSIC_MANDATORY_RULES_EN = (
        "[HIGHEST-PRIORITY MANDATORY REVIEW RULE: CONTRACT SUBJECT MATTER AND VAT RATE]\n"
        "(You MUST first identify the genuine transaction nature of the contract, then review the contractually agreed VAT rate. "
        "You MUST NOT judge solely based on contract title, contract name or the tax rate provided by the counterparty; "
        "judgment MUST be made combining subject matter, service scope, deliverables, acceptance criteria, payment triggers, "
        "transfer of ownership in goods, and true business substance.)\n\n"
        "Step 1 – Identify the Contract Transaction Type\n"
        "You must explicitly determine which one or more of the following transaction categories the contract belongs to:\n"
        "1. Sale of goods;\n"
        "2. General services;\n"
        "3. Processing, repair and replacement services;\n"
        "4. Tangible movable property leasing;\n"
        "5. Transportation, postal services, basic telecommunications, construction or immovable property leasing services;\n"
        "6. Assignment or licensing of intangible assets;\n"
        "7. Sale of immovable property or assignment of land-use right;\n"
        "8. Mixed contract or contract including two or more separate taxable transactions;\n"
        "9. Transaction nature cannot be determined based on available contract contents.\n\n"
        "Step 2 – Mandatory Verification of VAT Rate\n"
        "Where both parties to the transaction are general VAT taxpayers within the territory of China, "
        "and where no tax exemption, zero-rate, simplified tax method or other special policies apply:\n"
        "1. General sale of goods usually subject to 13% VAT rate;\n"
        "2. General services and intangible assets are usually subject to 6% VAT rate;\n"
        "3. Processing, repair and replacement services and tangible movable property leasing usually 13% VAT rate;\n"
        "4. Transportation, postal, basic telecommunications, construction, immovable property leasing services, etc. are usually subject to 9% VAT rate;\n"
        "5. Some specified goods may be subject to 9%;\n"
        "6. Small-scale taxpayers, simplified tax method, tax exemption, zero-rate or other special matters shall be separately determined in accordance with currently effective tax laws and regulations.\n\n"
        "Step 3 – Perform Cross-Validation of Tax Rates\n"
        "You MUST compare ALL three things simultaneously:\n"
        "(1) The applicable tax rate determined based on the true business substance of the contract;\n"
        "(2) The tax rate agreed in the main contract text, annexes, quotation or settlement clauses;\n"
        "(3) The invoice type, invoice-issuing item and tax rate contractually agreed for issuance.\n"
        "Where ANY inconsistency among any of them must be listed as a tax risk, no omission allowed.\n\n"
        "Pay SPECIAL attention to the following scenarios:\n"
        "1. Contract substance is sale of goods, but agreement to issue service invoices at 6%;\n"
        "2. Contract substance is general services, but agreement to issue goods invoices at 13%;\n"
        "3. Contract is titled 'Service Contract' but actually includes delivery of equipment, goods or other tangible products;\n"
        "4. Contract is titled 'Procurement Contract' or 'Purchase and Sale Contract' but actually includes independent consulting, promotion, maintenance, technical or management services;\n"
        "5. Contract includes both goods and services, but fails to separately list subject matters, amounts, tax rates and invoicing items;\n"
        "6. Tax rates are contradictory among price schedule, payment clauses, invoice clauses or annexes;\n"
        "7. Contract does not specify tax rate, or only provides 'issue invoices in accordance with national regulations', resulting in ambiguity of tax rate and tax-bearing party;\n"
        "8. The name of the invoice-issuing item is inconsistent with the contract terms and true business content;\n"
        "9. The supplier's taxpayer identity, tax calculation method or preferential policy is unclear, making it impossible to verify the contract tax rate;\n"
        "10. Applying a lower tax rate by artificially splitting the contract, changing the contract name or altering the-issuing item.\n\n"
        "Deemed Taxable Transaction Special Assessment\n"
        "Assess whether there is a risk of deemed taxable transactions, focusing on:\n"
        "- Free gifts\n- Samples\n- Promotional giveaways\n- Employee welfare\n- External donations\n- Sponsored in-kind deliveries\n- Gratuitous assignment of intangible assets\n"
        "Gratuitous provision of services is, in principle, NOT a deemed taxable transaction. Do NOT solely because services are provided free of charge.\n\n"
        "Mixed Contract Special Assessment\n"
        "Where a single contract includes both goods and services, you MUST determine whether it is:\n"
        "1. Two or more separately distinguishable taxable transactions;\n"
        "2. One transaction comprising multiple components, where the tax rate shall be determined by its principal business;\n"
        "3. Where the contract does not separately state the amounts for goods and services, there is a risk of failure to account separately or application of the higher tax rate.\n"
        "Where goods and services are independently distinguishable, recommend that the contract should separately set out:\n"
        "1. Name, quantity and tax-exclusive amount of goods;\n"
        "2. Service content, period and tax-exclusive amount of services;\n"
        "3. Applicable VAT tax rate for each respectively;\n"
        "4. Corresponding VAT tax amount respectively;\n"
        "5. Invoice-issuing items and timing.\n\n"
        "Other Tax Review (continue after rate verification is completed):\n"
        "1. Whether the contract price explicitly states whether it is tax-inclusive or tax-exclusive;\n"
        "2. Whether the tax amount is separately shown and the amount is correctly calculated;\n"
        "3. Whether the tax-bearing party and the tax-rate change adjustment mechanism are clear;\n"
        "4. Whether the invoice type,-issuing party, invoice content and invoice-issuing time are compliant;\n"
        "5. Whether the invoice-issuing conditions match payment, acceptance and the timing of occurrence of tax liability;\n"
        "6. Whether there is a risk that input VAT cannot be credited;\n"
        "7. Whether there is a risk that costs and expenses cannot be deducted for Enterprise Income Tax purposes;\n"
        "8. Whether the tax treatments of prepayments, deposits, security deposits, retention money, liquidated damages, compensation, discounts and rebates are correct.\n\n"
    )
    CLASSIC_MANDATORY_OUTPUT_RULES_ZH = (
        "【强制输出要求】\n"
        "1. 如果合同中没有明确税率，也必须输出税率判断结果，"
        "并将合同未明确约定适用税率列为风险或待确认事项。\n"
        "2. 如果合同包含货物销售，必须明确回答是否应适用13%；"
        "如果合同包含一般服务，必须明确回答是否应适用6%。"
        "如不适用，必须说明具体原因及适用的特殊规则。\n"
        "3. 不得以建议进一步确认税率等笼统表述代替专业判断。"
        "只有在合同信息确实不足时，才可以输出需补充信息，"
        "并明确列出缺少的信息。\n"
        "4. 必须区分：\n"
        "   - 税率明确错误；\n"
        "   - 税率可能错误；\n"
        "   - 合同未约定税率；\n"
        "   - 业务性质不清导致无法判断；\n"
        "   - 税率正确；\n"
        "   - 涉及特殊税率或特殊计税方法。\n\n"
    )
    CLASSIC_MANDATORY_OUTPUT_RULES_EN = (
        "[MANDATORY OUTPUT REQUIREMENTS]\n"
        "1. Even if the contract does not explicitly specify a tax rate, you MUST still output your VAT rate determination result, "
        "and list 'Contract does not explicitly stipulate the applicable VAT rate' as a risk or item pending confirmation.\n"
        "2. If the contract includes sale of goods, you MUST explicitly answer whether 13% should apply; "
        "if the includes general services, you MUST explicitly answer whether 6% should apply. "
        "If not, you MUST specify the concrete reason and the applicable special rule.\n"
        "3. Vague statements such as 'It is recommended to further confirm the tax rate' MUST NOT be used as a substitute for professional judgment. "
        "Only when the contract information is truly insufficient may 'Additional information required' be output, "
        "with the missing information explicitly listed.\n"
        "4. You MUST categorize each VAT-rate-related into:\n"
        "   - Clearly incorrect tax rate;\n"
        "   - Potentially incorrect tax rate;\n"
        "   - Contract does not stipulate tax rate;\n"
        "   - Cannot be determined due to unclear business nature;\n"
        "   - Correct tax rate;\n"
        "   - Involves special tax rate or special tax calculation method.\n\n"
    )
    CLASSIC_KEY_AUDIT_AREAS_ZH = (
        "【重点审核内容（12 大领域）】\n"
        "重点审核以下内容：\n\n"
        "1. 合同价格是否明确为含税价或不含税价；\n"
        "2. 税率、征税项目及税务处理是否准确；\n"
        "3. 税费承担主体及税率变化的处理机制是否明确；\n"
        "4. 发票类型、开票主体、开票内容、开票税率及开票时间是否合规；\n"
        "5. 发票条款与付款、结算、验收及纳税义务发生时间是否匹配；\n"
        "6. 是否存在无法取得合规发票、无法抵扣进项税额或成本费用无法税前扣除的风险；\n"
        "7. 预付款、里程碑付款、尾款、质保金、押金、违约金、赔偿款及折扣返利的税务处理是否合理；\n"
        "8. 收入确认、成本费用扣除及资产资本化处理是否存在风险；\n"
        "9. 跨境付款是否涉及增值税、企业所得税、代扣代缴、税收协定、常设机构及外汇支付风险；\n"
        "10. 关联交易是否存在转让定价、受益所有人、同期资料及独立交易原则风险；\n"
        "11. 合同约定是否与业务实质、交易流程及实际履行方式一致；\n"
        "12. 是否存在可能导致补税、滞纳金、罚款、进项税转出、成本费用不得扣除、税务争议、审计调整或公司商业损失的其他问题。\n\n"
    )
    CLASSIC_KEY_AUDIT_AREAS_EN = (
        "[KEY AUDIT AREAS (12 DOMAINS)]\n"
        "Focus audit on the following:\n\n"
        "1. Whether the contract price is explicitly stated as tax-inclusive or tax-exclusive;\n"
        "2. Whether the tax rate, taxable item classification and tax treatment are accurate;\n"
        "3. Whether the tax-bearing party and the adjustment mechanism upon tax-rate changes are clearly specified;\n"
        "4. Whether the invoice type, -issuing party, invoice content, applicable tax rate and -issuing timing are compliant;\n"
        "5. Whether invoice clauses are aligned with payment, settlement, acceptance and timing of tax-liability occurrence;\n"
        "6. Whether risks exist that compliant invoices cannot be obtained, input VAT cannot be credited, or costs/expenses are non-deductible for CIT purposes;\n"
        "7. Whether tax treatments of prepayments, milestone payments, retention payments, security deposits, liquidated damages, compensation, discounts and rebates are reasonable;\n"
        "8. Whether there are risks in revenue recognition, cost/expense deductibility and asset capitalization treatments;\n"
        "9. Whether cross-border payments involve VAT, EIT, withholding agent obligations, tax treaties, permanent establishment (PE) or foreign-exchange payment risks;\n"
        "10. Whether related-party transactions bear transfer-pricing, beneficial-owner, contemporaneous-documentation or arm's-length principle risks;\n"
        "11. Whether contractual terms are consistent with business substance, transaction flow and actual performance methods;\n"
        "12. Any other issues that may trigger tax arrears, late-payment surcharges, administrative penalties, input-VAT transfer-out, non-deductible costs, tax disputes, audit adjustments or commercial losses to the company.\n\n"
    )
    CLASSIC_AUDIT_REQUIREMENTS_ZH = (
        "【通用审核要求】\n"
        "审核要求：\n"
        "- 采用严谨、审慎、保守的审核原则，以维护公司利益及确保税务合规为最高优先级；\n"
        "- 只识别有明确依据或合理风险逻辑支持的问题，不得臆测或虚构风险；\n"
        "- 对每项问题明确说明对应条款、风险类型、风险原因、潜在影响及修改建议；\n"
        "- 修改建议应具体、可执行，必要时直接提供建议替换的合同条款；\n"
        "- 如果合同信息不足以作出判断，应明确标记为需补充信息，并列出需要确认的事项；\n"
        "- 区分明确不合规 / 高风险 / 一般风险 / 优化建议 / 需补充信息，不得将措辞优化夸大为实质性税务违规；\n"
        "- 不得遗漏可能影响增值税抵扣、企业所得税税前扣除、发票合规、代扣代缴或跨境支付的关键问题；\n"
        "- 如果未发现税务问题，不得为了形成审核意见而虚构问题。\n\n"
    )
    CLASSIC_AUDIT_REQUIREMENTS_EN = (
        "[GENERAL AUDIT REQUIREMENTS]\n"
        "Review principles and requirements:\n"
        "- Adopt a rigorous, prudent and conservative review approach; the HIGHEST priority is to protect the company's interests and ensure tax compliance;\n"
        "- Only identify issues supported by explicit legal basis or sound risk logic; NO speculative or fabricated risks;\n"
        "- For EACH issue explicitly state: the clause in question, the TYPE of risk, the REASON/Cause of the risk, its POTENTIAL IMPACT, and suggested amendment;\n"
        "- Amendment suggestions must be concrete and actionable; where necessary, directly provide the suggested replacement contract clause text;\n"
        "- If contract information is insufficient to reach a judgment, explicitly mark it as 'Additional Information Required' and list the specific items to be confirmed;\n"
        "- Categorize into FIVE levels: Clearly Non-Compliant / High Risk / General Risk / Optimization Suggestion / Additional Information Required. DO NOT exaggerate drafting-wording improvements as substantive tax non-compliance;\n"
        "- DO NOT omit any critical issue that may affect VAT creditability, CIT deductibility, invoice compliance, withholding obligations or cross-border payments;\n"
        "- If NO tax issues are identified, DO NOT fabricate issues merely to produce 'review opinions'.\n\n"
    )
    if norm_lang == "en":
        system = (
            "You are a senior Tax Director and Contract Tax Review Specialist with more than 20 years of "
            "experience at Fortune 500 multinational corporations, holding CPA (PRC Certified Public "
            "Accountant), CTA (Certified Tax Advisor) and ACCA (Association of Chartered Certified "
            "Accountants) and other domestic and international professional tax and accounting qualifications. "
            "You have long been responsible for contract tax review, tax compliance, risk management and "
            "internal control, as well as VAT administration, Enterprise Income Tax compliance, invoice risk "
            "control and internal tax governance for large multinational enterprises. "
            "You are thoroughly familiar with PRC current tax laws, regulations and multinational corporate tax "
            "administration requirements, including but not limited to Value-Added Tax, Enterprise Income Tax, "
            "Individual Income Tax, Stamp Duty, Invoice Administration, Tax Collection Administration, "
            "cross-border payments, non-resident enterprise taxation, withholding agent obligations, "
            "related-party transactions and transfer pricing. "
            "You are now required, from the perspective of a Fortune-500 MNC Tax Director, to review "
            "internal procurement, sales, service, engineering, leasing, marketing promotion, technology "
            "licensing and cross-border contracts, etc., and identify ALL tax-related non-compliances, "
            "potential risks and wording deficiencies. "
            "You are also required to review contracts executed internally within the company and between the "
            "company and its external counterparties, identifying ALL tax-related non-compliances, VAT-rate "
            "errors, invoice risks, tax losses, and execution risks. "
            "Strictly comply with the [Highest-Priority Mandatory Review Rules], [Mandatory Output Requirements], "
            "[Key Audit Areas (12 Domains)] and [General Audit Requirements] set out in the user prompt. "
            "Output ONLY in JSON format."
        )
        user = (
            f"Language: {norm_lang}\n"
            + CLASSIC_MANDATORY_RULES_EN
            + CLASSIC_KEY_AUDIT_AREAS_EN
            + CLASSIC_AUDIT_REQUIREMENTS_EN
            + CLASSIC_CHECKLIST_EN
            + CLASSIC_MANDATORY_OUTPUT_RULES_EN
            + "Use only the contract text and reference evidence below.\n"
            + "Do not output reasoning process.\n\n"
            + "TAX RISK ONLY: Only output tax-related risks (tax rate, invoicing, tax obligations, withholding tax, tax compliance). "
            + "Do NOT output contract clause descriptions, breach of contract, or payment obligation issues that are not tax risks.\n"
            + "CITATION REQUIRED: Every risk MUST include a citation_id from the whitelist and the corresponding law_title. "
            + "If you cannot match a risk to a regulation in the whitelist, do NOT output that risk.\n"
            + "RISK DESCRIPTION: The 'issue' field must describe the specific tax compliance risk and why it violates or conflicts with the cited regulation. "
            + "Do NOT simply copy contract clause text as the issue.\n"
            + "CROSS-CLAUSE RULE: Before flagging 'missing/unspecified' risks, verify against the full contract text. "
            + "If the element is covered elsewhere in the contract, do NOT flag it.\n"
        )
        if reference_evidence_text:
            user += f"{reference_evidence_text}\n\n"
        user += f"Whitelist (citation IDs):\n{whitelist_text}\n\n"
        user += f"Full Contract Text (for cross-clause verification):\n{full_text_context}\n\n"
        user += f"Contract Clauses (structured):\n{clause_text}\n\n"
        user += "JSON: {\"summary\":\"\",\"risks\":[{\"level\":\"high|medium|low\",\"issue\":\"\",\"suggestion\":\"\",\"citation_id\":\"\",\"law_title\":\"\",\"article_no\":\"\",\"evidence\":\"\",\"confidence\":0.0,\"clause_id\":\"\"}]}"
    else:
        system = (
            "你是一名拥有20年以上经验的世界500强跨国企业资深税务总监及合同税务审核专家，"
            "持有CPA、CTA、ACCA等国内外专业财税资格，"
            "长期负责大型跨国企业的合同税务审核、税务合规、风险管理及内部控制，"
            "以及增值税管理、企业所得税合规、发票风险控制及内部税务治理。"
            "你熟悉中国现行税收法律法规及跨国企业税务管理要求，"
            "包括但不限于增值税、企业所得税、个人所得税、印花税、发票管理、税收征管、"
            "跨境支付、非居民企业税收、代扣代缴、关联交易及转让定价等规定。"
            "你现在需要从世界500强跨国企业税务总监的视角，"
            "审核公司内部采购、销售、服务、工程、租赁、市场推广、技术许可及跨境交易等合同，"
            "识别合同中所有与税务相关的不合规事项、潜在风险及表述缺陷。"
            "你同时需要审核公司内部及公司与外部合作方签署的合同，"
            "识别合同中所有税务相关的不合规事项、税率错误、发票风险、税务损失及执行风险。"
            "严格遵守user prompt中给出的【最高优先级强制审核规则】、【强制输出要求】、"
            "【重点审核内容（12 大领域）】与【通用审核要求】。"
            "请只输出JSON。"
        )
        user = (
            f"语言:{norm_lang}\n"
            + CLASSIC_MANDATORY_RULES_ZH
            + CLASSIC_KEY_AUDIT_AREAS_ZH
            + CLASSIC_AUDIT_REQUIREMENTS_ZH
            + CLASSIC_CHECKLIST_ZH
            + CLASSIC_MANDATORY_OUTPUT_RULES_ZH
            + "仅根据合同文本与参考法规证据输出结果；不要输出推理过程。\n\n"
            + "【仅限涉税风险】只输出涉税风险（税率、开票、纳税义务、代扣代缴、税务合规等）。"
            + "不要输出合同条款描述、违约责任、支付义务等非涉税内容。\n"
            + "【强制引用】每个风险项必须包含 citation_id（从白名单中选择）和对应的 law_title。"
            + "无法从白名单中匹配到法规条款的风险，不得输出。\n"
            + "【风险描述】issue 字段必须描述具体的税务合规风险及其违反或冲突的法规依据，不得仅复制合同条款原文。\n"
            + "【跨条款联合校验】标记'缺失/未约定'风险前，必须核查合同全文。若该要素已在其他条款中约定，不得标记为风险。\n"
        )
        if reference_evidence_text:
            user += f"{reference_evidence_text}\n\n"
        user += f"白名单（引用ID）:\n{whitelist_text}\n\n"
        user += f"合同全文(用于跨条款联合校验):\n{full_text_context}\n\n"
        user += f"合同条款(结构化拆分):\n{clause_text}\n\n"
        user += "JSON: {\"summary\":\"\",\"risks\":[{\"level\":\"high|medium|low\",\"issue\":\"\",\"suggestion\":\"\",\"citation_id\":\"\",\"law_title\":\"\",\"article_no\":\"\",\"evidence\":\"\",\"confidence\":0.0,\"clause_id\":\"\"}]}"
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    return {
        "norm_lang": norm_lang,
        "messages": messages,
        "allowed_citation_ids": allowed_citation_ids,
        "prompt_meta": {
            "prompt_clause_count": len(prompt_clauses),
            "total_clause_count": len(preview_clauses or []),
            "clauses_omitted": max(0, len(list(preview_clauses or [])) - len(prompt_clauses)),
            "clause_chars_truncated_count": clause_chars_truncated_count,
            "full_text_context_chars": len(full_text_context),
            "full_text_context_truncated": len(str(text or "")) > full_ctx_budget,
            "full_text_context_budget_chars": full_ctx_budget,
            "evidence_items_used": len(evidence_items_used),
            "evidence_items_omitted": max(0, len(list(evidence_items or [])) - len(evidence_items_used)),
            "evidence_content_truncated_count": evidence_content_truncated_count,
            "whitelist_line_count": len(lines),
        },
    }


def _build_classic_audit(
    cfg: Dict[str, Any],
    llm,
    text: str,
    lang: str,
    preview_clauses: List[Dict[str, Any]],
    evidence_items: List[Dict[str, Any]],
    retrieval_opts: Dict[str, Any],
    audit_id: str,
    trace_id: str = "",
    prompt_payload: Optional[Dict[str, Any]] = None,
    capture_raw_response: bool = False,
) -> Dict[str, Any]:
    prompt_payload = prompt_payload or _build_classic_prompt_payload(
        cfg=cfg,
        text=text,
        lang=lang,
        preview_clauses=preview_clauses,
        evidence_items=evidence_items,
    )
    norm_lang = str(prompt_payload.get("norm_lang") or "zh")
    allowed_citation_ids = set(
        prompt_payload.get("allowed_citation_ids") or set())
    messages = list(prompt_payload.get("messages") or [])

    trace_meta = {
        "module": "contract_audit",
        "stage": "contract_classic_audit",
        "audit_id": audit_id,
        "trace_id": trace_id,
        "lang": norm_lang,
        "audit_mode": str(retrieval_opts.get("audit_mode") or ""),
    }
    CLASSIC_AUDIT_MAX_TOKENS = int(
        prompt_payload.get("max_output_tokens")
        if prompt_payload.get("max_output_tokens") is not None
        else (cfg.get("classic_audit_max_tokens") or 4096)
    )
    strict_validation = bool(prompt_payload.get("strict_result_validation"))
    result_text, _raw = _chat_with_task_profile(
        llm,
        messages,
        "contract_audit_main",
        overrides={"max_tokens": CLASSIC_AUDIT_MAX_TOKENS, "enable_thinking": False, "reasoning_effort": "low",
                   "thinking_budget_tokens": 0,
                   "implicit_retry_enabled": not strict_validation,
                   "_trace_meta": trace_meta},
    )
    strict_result = (
        validate_model_json(result_text, evidence_items, preview_clauses)
        if strict_validation
        else None
    )
    parsed = (
        strict_result.get("payload") or {}
        if strict_result is not None
        else _load_llm_json_object(result_text)
    )
    risks = (
        list(strict_result.get("accepted_risks") or [])
        if strict_result is not None and strict_result.get("valid")
        else (
            parsed.get("risks")
            if isinstance(parsed.get("risks"), list)
            and strict_result is None
            else []
        )
    )

    # ---- Fix #3: 检测响应是否被 max_tokens 截断 ----
    raw_usage = (_raw or {}).get("usage") if isinstance(_raw, dict) else None
    completion_tokens = int((raw_usage or {}).get("completion_tokens") or 0)
    response_truncated = completion_tokens >= CLASSIC_AUDIT_MAX_TOKENS

    # ---- Fix #1: 检测 JSON 解析是否失败 ----
    # 当 LLM 输出非 JSON（如思考链草稿）时，parsed 为空但 result_text 非空
    parse_failed = (
        not bool(strict_result.get("valid"))
        if strict_result is not None
        else (not parsed and bool(result_text and result_text.strip()))
    )
    if parse_failed:
        logger.warning(
            "classic_audit_json_parse_failed",
            audit_id=audit_id,
            response_length=len(result_text),
            completion_tokens=completion_tokens,
            max_tokens=CLASSIC_AUDIT_MAX_TOKENS,
            response_head=trace_clip(result_text, 200),
        )
    clause_map = {str(c.get("clause_id") or ""): c for c in preview_clauses}
    normalized_risks = []
    for idx, r in enumerate(risks, start=1):
        if not isinstance(r, dict):
            continue
        level = str(r.get("level") or "medium").strip().lower()
        if level not in {"high", "medium", "low"}:
            level = "medium"
        clause_id = str(r.get("clause_id") or "")
        c = clause_map.get(clause_id) or (
            preview_clauses[0] if preview_clauses else {})
        input_cid = str(r.get("citation_id") or "").strip()
        mapped_cid = input_cid if input_cid in allowed_citation_ids else ""
        law_title = str(r.get("law_title") or "")
        article_no = str(r.get("article_no") or "")
        basis = f"{law_title} {article_no}".strip()
        issue_text = str(r.get("issue") or "").strip()
        evidence_text = str(r.get("evidence") or "").strip()
        # Skip risks with completely empty content (LLM returned a skeleton without substance)
        if len(issue_text) < 6 and len(evidence_text) < 6:
            continue
        # Skip risks without any legal reference (no citation_id AND no law_title)
        # These are likely the model echoing contract text rather than doing tax risk analysis
        if not mapped_cid and not law_title.strip():
            continue
        normalized_risks.append(
            {
                "level": level,
                "issue": issue_text,
                "suggestion": str(r.get("suggestion") or ""),
                "basis": basis,
                "law_reference": basis,
                "citation_id": mapped_cid,
                "citation_status": "mapped" if mapped_cid else "unmapped",
                "verification_status": str(
                    r.get("verification_status") or "needs_review"
                ),
                "evidence": evidence_text,
                "law_title": law_title,
                "article_no": article_no,
                "location": {
                    "risk_id": f"classic-r{idx}",
                    "clause_id": str(c.get("clause_id") or ""),
                    "anchor_id": str(c.get("anchor_id") or ""),
                    "page_no": int(c.get("page_no") or 0),
                    "paragraph_no": str(c.get("paragraph_no") or ""),
                    "clause_path": str(c.get("clause_path") or ""),
                    # Verbatim source text of the clause, so the exporter can still
                    # anchor the comment when the model's quote is a paraphrase.
                    "clause_text": str(c.get("clause_text") or "")[:600],
                    "locate_source": str(c.get("locate_source") or "preview_clause"),
                    "quote": evidence_text,
                    "score": round(float(r.get("confidence") or 0.0), 4),
                },
            }
        )
    risk_summary = {"high": 0, "medium": 0, "low": 0}
    for item in normalized_risks:
        risk_summary[str(item.get("level") or "low")] += 1
    summary = str(parsed.get("summary") or "").strip() or (
        f"经典审计完成，共发现 {len(normalized_risks)} 项风险" if norm_lang != "en" else
        f"Classic audit completed, found {len(normalized_risks)} risks"
    )
    # ---- Fix #1: 动态 legal_validation（替代硬编码 ok=True） ----
    legal_issues = []
    if parse_failed:
        legal_issues.append({
            "risk_id": "classic_parse",
            "message": (
                f"LLM 未返回有效 JSON（响应 {len(result_text)} 字符，"
                f"completion_tokens={completion_tokens}/{CLASSIC_AUDIT_MAX_TOKENS}），"
                f"审计结果可能不完整"
            ) if norm_lang != "en" else (
                f"LLM did not return valid JSON (response {len(result_text)} chars, "
                f"completion_tokens={completion_tokens}/{CLASSIC_AUDIT_MAX_TOKENS}), "
                f"audit result may be incomplete"
            ),
        })
    if response_truncated and not parse_failed:
        legal_issues.append({
            "risk_id": "classic_truncation",
            "message": (
                f"LLM 响应被 max_tokens={CLASSIC_AUDIT_MAX_TOKENS} 截断"
                f"（completion_tokens={completion_tokens}），部分风险可能丢失"
            ) if norm_lang != "en" else (
                f"LLM response truncated at max_tokens={CLASSIC_AUDIT_MAX_TOKENS} "
                f"(completion_tokens={completion_tokens}), some risks may be lost"
            ),
        })
    legal_validation = {
        "ok": len(legal_issues) == 0,
        "issues": legal_issues,
    }
    audit = {
        "summary": summary,
        "executive_opinion": [],
        "risk_summary": risk_summary,
        "risks": normalized_risks,
        "citations": _enrich_citations(evidence_items, evidence_items),
        "legal_validation": legal_validation,
    }

    # ---- Traceability: 记录审计全量上下文（证据/条款/LLM处理状态） ----
    write_audit_trace(cfg, "classic_audit_full_context", {
        "audit_id": audit_id,
        "evidence_count": len(evidence_items),
        "evidence_citation_ids": [
            str(it.get("citation_id") or "") for it in evidence_items
        ],
        "evidence_law_titles": [
            trace_clip(str(it.get("law_title") or it.get("title") or ""), 120)
            for it in evidence_items
        ],
        "preview_clause_count": len(preview_clauses),
        "preview_clause_ids": [
            str(c.get("clause_id") or "") for c in preview_clauses
        ],
        "preview_clause_titles": [
            trace_clip(str(c.get("title") or c.get("clause_path") or ""), 120)
            for c in preview_clauses
        ],
        "max_tokens": CLASSIC_AUDIT_MAX_TOKENS,
        "completion_tokens": completion_tokens,
        "response_truncated": response_truncated,
        "parse_failed": parse_failed,
        "response_length": len(result_text),
        "risks_count": len(normalized_risks),
        "raw_response_preview": trace_clip(result_text, 2000),
        "normalized_risks_summary": [
            {
                "level": item.get("level"),
                "law_title": item.get("law_title", ""),
                "article_no": item.get("article_no", ""),
                "issue_preview": trace_clip(item.get("issue", ""), 200),
            }
            for item in normalized_risks
        ],
    })

    return {
        "audit": audit,
        "meta": {
            "memory_mode": False,
            "memory_mode_enabled": False,
            "execution_path": "classic",
            "memory_llm_call_count": 1,
            "memory_report_risk_count": len(normalized_risks),
            "memory_validation_ok": legal_validation["ok"],
            "classic_parse_failed": parse_failed,
            "classic_response_truncated": response_truncated,
            "classic_completion_tokens": completion_tokens,
            "classic_max_tokens": CLASSIC_AUDIT_MAX_TOKENS,
        },
        "raw": {
            "mode": "classic",
            "raw_response": str(result_text) if capture_raw_response else "",
            "rejected_risks": list(
                (strict_result or {}).get("rejected_risks") or []
            ),
            "validation_errors": list(
                (strict_result or {}).get("errors") or []
            ),
        },
    }


def _select_multipass_evidence_items(
    evidence_items: List[Dict[str, Any]],
    max_items: int,
) -> List[Dict[str, Any]]:
    def _score(item: Dict[str, Any]) -> float:
        try:
            return float(item.get("final_score") or item.get("score") or item.get("tax_relevance") or 0.0)
        except Exception:
            return 0.0

    ranked = sorted(
        [item for item in list(evidence_items or [])
         if isinstance(item, dict)],
        key=_score,
        reverse=True,
    )
    return ranked[:max_items]


def _build_chunk_clause_range(chunk_id: str, clauses: List[Dict[str, Any]]) -> Dict[str, Any]:
    clause_ids = [str(item.get("clause_id") or "") for item in clauses]
    clause_paths = [str(item.get("clause_path") or item.get(
        "title") or "") for item in clauses]
    page_numbers = [
        int(item.get("page_no") or 0)
        for item in clauses
        if int(item.get("page_no") or 0) > 0
    ]
    return {
        "chunk_id": chunk_id,
        "start_clause_id": clause_ids[0] if clause_ids else "",
        "end_clause_id": clause_ids[-1] if clause_ids else "",
        "clause_ids": clause_ids,
        "clause_paths": clause_paths,
        "page_start": min(page_numbers) if page_numbers else 0,
        "page_end": max(page_numbers) if page_numbers else 0,
        "paragraph_start": str(clauses[0].get("paragraph_no") or "") if clauses else "",
        "paragraph_end": str(clauses[-1].get("paragraph_no") or "") if clauses else "",
    }


def _build_chunk_audit_result(
    *,
    chunk_id: str,
    round_index: int,
    clauses: List[Dict[str, Any]],
    audit_result: Dict[str, Any],
) -> Dict[str, Any]:
    audit = audit_result.get("audit") if isinstance(
        audit_result.get("audit"), dict) else {}
    meta = audit_result.get("meta") if isinstance(
        audit_result.get("meta"), dict) else {}
    risks = [item for item in list(
        audit.get("risks") or []) if isinstance(item, dict)]
    citations = [item for item in list(
        audit.get("citations") or []) if isinstance(item, dict)]
    evidence_links = []
    evidence_seen = set()
    for item in citations:
        key = (
            str(item.get("citation_id") or "").strip(),
            str(item.get("law_title") or item.get("title") or "").strip(),
            str(item.get("article_no") or "").strip(),
        )
        if key in evidence_seen:
            continue
        evidence_seen.add(key)
        evidence_links.append(
            {
                "citation_id": key[0],
                "law_title": key[1],
                "article_no": key[2],
            }
        )
    confidence_values = []
    for risk in risks:
        location = risk.get("location") if isinstance(
            risk.get("location"), dict) else {}
        try:
            confidence_values.append(
                float(location.get("score") or risk.get("confidence") or 0.0))
        except Exception:
            continue
    confidence_score = round(
        sum(confidence_values) / len(confidence_values), 4) if confidence_values else 0.0
    return {
        "chunk_id": chunk_id,
        "round_index": int(round_index or 0),
        "clause_range": _build_chunk_clause_range(chunk_id, clauses),
        "risk_items": risks,
        "risk_count": len(risks),
        "evidence_links": evidence_links,
        "confidence_score": confidence_score,
        "parse_failed_flag": bool(meta.get("classic_parse_failed")),
        "truncated_flag": bool(meta.get("classic_response_truncated")),
        "completion_tokens": int(meta.get("classic_completion_tokens") or 0),
        "max_output_tokens": int(meta.get("classic_max_tokens") or 0),
        "summary": str(audit.get("summary") or ""),
    }


def _run_multipass_chunk_round(
    *,
    cfg: Dict[str, Any],
    llm,
    text: str,
    lang: str,
    clauses: List[Dict[str, Any]],
    selected_evidence: List[Dict[str, Any]],
    retrieval_opts: Dict[str, Any],
    audit_id: str,
    trace_id: str,
    round_index: int,
    chunk_id: str,
    estimated_clause_tokens: int,
    budget_trigger: List[str],
    retry_index: int = 0,
    retry_reasons: Optional[List[str]] = None,
) -> Dict[str, Any]:
    chunk_text = "\n\n".join(
        [
            f"{item.get('clause_path') or item.get('title') or item.get('clause_id') or ''}\n"
            f"{item.get('clause_text') or item.get('text') or ''}"
            for item in clauses
        ]
    )
    chunk_payload = _build_classic_prompt_payload(
        cfg=cfg,
        text=chunk_text or text,
        lang=lang,
        preview_clauses=clauses,
        evidence_items=selected_evidence,
    )
    chunk_result = _build_classic_audit(
        cfg=cfg,
        llm=llm,
        text=chunk_text or text,
        lang=lang,
        preview_clauses=clauses,
        evidence_items=selected_evidence,
        retrieval_opts=retrieval_opts,
        audit_id=f"{audit_id}_r{round_index}_try{retry_index}",
        trace_id=trace_id,
        prompt_payload=chunk_payload,
    )
    round_meta = chunk_result.get("meta") if isinstance(
        chunk_result.get("meta"), dict) else {}
    chunk_audit_result = _build_chunk_audit_result(
        chunk_id=chunk_id,
        round_index=int(round_index or 0),
        clauses=clauses,
        audit_result=chunk_result,
    )
    write_round_trace(
        cfg,
        int(round_index or 0),
        "chunk_audit_done" if retry_index <= 0 else "chunk_retry_done",
        {
            "audit_id": audit_id,
            "chunk_id": chunk_id,
            "retry_index": retry_index,
            "retry_reasons": list(retry_reasons or []),
            "risk_count": chunk_audit_result.get("risk_count"),
            "confidence_score": chunk_audit_result.get("confidence_score"),
            "parse_failed_flag": chunk_audit_result.get("parse_failed_flag"),
            "truncated_flag": chunk_audit_result.get("truncated_flag"),
        },
    )
    return {
        "round_index": round_index,
        "chunk_id": chunk_id,
        "clause_ids": [str(item.get("clause_id") or "") for item in clauses],
        "clause_paths": [str(item.get("clause_path") or item.get("title") or "") for item in clauses],
        "estimated_clause_tokens": estimated_clause_tokens,
        "audit": chunk_result.get("audit"),
        "meta": round_meta,
        "chunk_audit_result": chunk_audit_result,
        "budget_trigger": list(budget_trigger or []),
        "retry_index": int(retry_index or 0),
        "retry_reasons": list(retry_reasons or []),
    }


def _merge_multipass_audits(
    rounds: List[Dict[str, Any]],
    cfg: Dict[str, Any],
    preview_clauses: List[Dict[str, Any]],
    lang: str,
) -> Dict[str, Any]:
    parse_failed_rounds = 0
    truncated_rounds = 0
    chunk_audit_results = []
    for round_result in rounds:
        meta = round_result.get("meta") if isinstance(
            round_result.get("meta"), dict) else {}
        if meta.get("classic_parse_failed"):
            parse_failed_rounds += 1
        if meta.get("classic_response_truncated"):
            truncated_rounds += 1
        if isinstance(round_result.get("chunk_audit_result"), dict):
            chunk_audit_results.append(round_result["chunk_audit_result"])
    aggregated = aggregate_chunk_audit_results(
        cfg=cfg,
        chunk_audit_results=chunk_audit_results,
        preview_clauses=preview_clauses,
        lang=lang,
    )
    aggregated_audit = aggregated.get("audit") if isinstance(
        aggregated.get("audit"), dict) else {}
    aggregated_meta = aggregated.get("meta") if isinstance(
        aggregated.get("meta"), dict) else {}
    legal_validation = aggregated_audit.get("legal_validation") if isinstance(
        aggregated_audit.get("legal_validation"), dict) else {}
    return {
        "audit": aggregated_audit,
        "meta": {
            "memory_mode": False,
            "memory_mode_enabled": False,
            "execution_path": "multipass_classic_stage1",
            "memory_llm_call_count": len(rounds),
            "memory_report_risk_count": len(list(aggregated_audit.get("risks") or [])),
            "memory_validation_ok": bool(legal_validation.get("ok", False)),
            "classic_parse_failed": parse_failed_rounds > 0,
            "classic_response_truncated": truncated_rounds > 0,
            "classic_completion_tokens": sum(
                int((item.get("meta") or {}).get(
                    "classic_completion_tokens") or 0)
                for item in rounds
            ),
            "classic_max_tokens": max(
                [int((item.get("meta") or {}).get("classic_max_tokens") or 0)
                 for item in rounds] or [0]
            ),
            "multipass_round_count": len(rounds),
            "multipass_parse_failed_rounds": parse_failed_rounds,
            "multipass_truncated_rounds": truncated_rounds,
            **aggregated_meta,
        },
        "raw": {
            "mode": "multipass_classic_stage1",
            "rounds": rounds,
            "chunk_audit_results": chunk_audit_results,
            "aggregated_exports": aggregated.get("exports") if isinstance(aggregated.get("exports"), dict) else {},
        },
    }


def _build_multipass_classic_audit(
    cfg: Dict[str, Any],
    llm,
    text: str,
    lang: str,
    preview_clauses: List[Dict[str, Any]],
    evidence_items: List[Dict[str, Any]],
    retrieval_opts: Dict[str, Any],
    audit_id: str,
    trace_id: str,
    token_budget: Dict[str, Any],
    progress_cb: Optional[Callable[..., None]] = None,
) -> Dict[str, Any]:
    policy = dict((token_budget or {}).get("policy") or {})
    clause_plan = plan_clause_groups(preview_clauses, policy)
    selected_evidence = _select_multipass_evidence_items(
        evidence_items,
        int(policy.get("max_evidence_items_per_round") or 12),
    )
    rounds: List[Dict[str, Any]] = []
    progress_state = {
        "planned_total": len(clause_plan.get("rounds") or []),
        "started": 0,
        "completed": 0,
        "last_completed_label": "",
    }

    def _emit_classic_progress(
        *,
        request_status: str,
        current_index: int = 0,
        current_label: str = "",
        request_kind: str = "",
        retry_index: int = 0,
        round_index: int = 0,
        last_error: str = "",
    ) -> Dict[str, Any]:
        total = int(progress_state.get("planned_total") or 0)
        detail = build_llm_progress_detail(
            execution_path="classic",
            total_planned=total,
            total_upper_bound=total,
            exact_total_known=True,
            started=int(progress_state.get("started") or 0),
            completed=int(progress_state.get("completed") or 0),
            current_index=current_index,
            current_label=current_label,
            request_kind=request_kind,
            request_status=request_status,
            retry_index=retry_index,
            round_index=round_index,
            last_completed_label=str(
                progress_state.get("last_completed_label") or ""),
            last_error=last_error,
        )
        report_progress(
            progress_cb,
            "auditing",
            compute_llm_progress_percent(
                int(progress_state.get("completed") or 0),
                max(1, total),
            ),
            build_llm_progress_message(detail),
            detail=detail,
        )
        return detail

    logger.info(
        "audit_llm_plan_created",
        audit_id=audit_id,
        execution_path="classic",
        llm_request_total_planned=int(
            progress_state.get("planned_total") or 0),
        llm_request_exact_total_known=True,
    )
    _emit_classic_progress(request_status="planned")

    clause_group_map: Dict[str, List[Dict[str, Any]]] = {}
    for round_info, group in zip(clause_plan.get("rounds") or [], clause_plan.get("groups") or []):
        chunk_id = f"chunk-{int(round_info.get('round_index') or 0):03d}"
        clause_group_map[chunk_id] = group
        progress_state["started"] = int(progress_state.get("started") or 0) + 1
        current_index = int(progress_state.get("started") or 0)
        current_round_index = int(round_info.get("round_index") or 0)
        logger.info(
            "audit_llm_request_start",
            audit_id=audit_id,
            execution_path="classic",
            llm_request_current_index=current_index,
            llm_request_total_planned=int(
                progress_state.get("planned_total") or 0),
            request_kind="chunk",
            chunk_id=chunk_id,
            retry_index=0,
            round_index=current_round_index,
        )
        _emit_classic_progress(
            request_status="running",
            current_index=current_index,
            current_label=chunk_id,
            request_kind="chunk",
            retry_index=0,
            round_index=current_round_index,
        )
        round_result = _run_multipass_chunk_round(
            cfg=cfg,
            llm=llm,
            text=text,
            lang=lang,
            clauses=group,
            selected_evidence=selected_evidence,
            retrieval_opts=retrieval_opts,
            audit_id=audit_id,
            trace_id=trace_id,
            round_index=current_round_index,
            chunk_id=chunk_id,
            estimated_clause_tokens=int(
                round_info.get("estimated_clause_tokens") or 0),
            budget_trigger=list((token_budget or {}).get("reasons") or []),
        )
        rounds.append(round_result)
        chunk_audit_result = round_result.get(
            "chunk_audit_result") if isinstance(round_result.get("chunk_audit_result"), dict) else {}
        progress_state["completed"] = int(
            progress_state.get("completed") or 0) + 1
        progress_state["last_completed_label"] = chunk_id
        logger.info(
            "audit_llm_request_done",
            audit_id=audit_id,
            execution_path="classic",
            llm_request_current_index=current_index,
            llm_request_total_planned=int(
                progress_state.get("planned_total") or 0),
            request_kind="chunk",
            chunk_id=chunk_id,
            retry_index=0,
            round_index=current_round_index,
            risk_count=int(chunk_audit_result.get("risk_count") or 0),
            parse_failed_flag=bool(
                chunk_audit_result.get("parse_failed_flag")),
            truncated_flag=bool(chunk_audit_result.get("truncated_flag")),
        )
        _emit_classic_progress(
            request_status="done",
            current_index=current_index,
            current_label=chunk_id,
            request_kind="chunk",
            retry_index=0,
            round_index=current_round_index,
        )
    merged = _merge_multipass_audits(
        rounds,
        cfg=cfg,
        preview_clauses=preview_clauses,
        lang=lang,
    )
    review_plan = build_reliability_review_plan(
        cfg=cfg,
        aggregated_audit=merged.get("audit") if isinstance(
            merged.get("audit"), dict) else {},
        aggregated_meta=merged.get("meta") if isinstance(
            merged.get("meta"), dict) else {},
        chunk_audit_results=list(
            (merged.get("raw") or {}).get("chunk_audit_results") or []),
    )
    write_audit_trace(
        cfg,
        "audit_review_plan",
        {
            "audit_id": audit_id,
            "retry_chunk_ids": list(review_plan.get("retry_chunk_ids") or []),
            "review_item_count": int(review_plan.get("review_item_count") or 0),
            "should_retry": bool(review_plan.get("should_retry")),
            "initial_reliability_level": str(
                review_plan.get("initial_reliability_level") or "medium"),
        },
    )
    retry_logs: List[Dict[str, Any]] = []
    if bool(review_plan.get("should_retry")):
        round_index_map = {
            str(item.get("chunk_id") or ""): index
            for index, item in enumerate(rounds)
            if isinstance(item, dict)
        }
        max_retry_per_chunk = int(
            ((review_plan.get("policy") or {}).get("max_retry_per_chunk") or 0))
        for retry_target in list(review_plan.get("retry_chunks") or []):
            chunk_id = str(retry_target.get("chunk_id") or "")
            if not chunk_id or chunk_id not in round_index_map:
                continue
            current_round = rounds[round_index_map[chunk_id]]
            current_retry_index = int(current_round.get("retry_index") or 0)
            if current_retry_index >= max_retry_per_chunk:
                retry_logs.append(
                    {
                        "chunk_id": chunk_id,
                        "retry_performed": False,
                        "reason": "retry_limit_reached",
                        "retry_reasons": list(retry_target.get("reasons") or []),
                    }
                )
                continue
            old_total = int(progress_state.get("planned_total") or 0)
            progress_state["planned_total"] = old_total + 1
            logger.info(
                "audit_llm_plan_adjusted",
                audit_id=audit_id,
                execution_path="classic",
                llm_request_total_planned_before=old_total,
                llm_request_total_planned_after=int(
                    progress_state.get("planned_total") or 0),
                reason="reliability_retry",
                chunk_id=chunk_id,
            )
            progress_state["started"] = int(
                progress_state.get("started") or 0) + 1
            current_index = int(progress_state.get("started") or 0)
            current_round_index = int(current_round.get("round_index") or 0)
            retry_step = current_retry_index + 1
            logger.info(
                "audit_llm_request_start",
                audit_id=audit_id,
                execution_path="classic",
                llm_request_current_index=current_index,
                llm_request_total_planned=int(
                    progress_state.get("planned_total") or 0),
                request_kind="retry",
                chunk_id=chunk_id,
                retry_index=retry_step,
                round_index=current_round_index,
            )
            _emit_classic_progress(
                request_status="running",
                current_index=current_index,
                current_label=chunk_id,
                request_kind="retry",
                retry_index=retry_step,
                round_index=current_round_index,
            )
            next_round = _run_multipass_chunk_round(
                cfg=cfg,
                llm=llm,
                text=text,
                lang=lang,
                clauses=clause_group_map.get(chunk_id) or [],
                selected_evidence=selected_evidence,
                retrieval_opts=retrieval_opts,
                audit_id=audit_id,
                trace_id=trace_id,
                round_index=int(current_round.get("round_index") or 0),
                chunk_id=chunk_id,
                estimated_clause_tokens=int(
                    current_round.get("estimated_clause_tokens") or 0),
                budget_trigger=list(current_round.get("budget_trigger") or []),
                retry_index=retry_step,
                retry_reasons=list(retry_target.get("reasons") or []),
            )
            retry_logs.append(
                {
                    "chunk_id": chunk_id,
                    "retry_performed": True,
                    "retry_index": retry_step,
                    "retry_reasons": list(retry_target.get("reasons") or []),
                    "before_parse_failed": bool((current_round.get("chunk_audit_result") or {}).get("parse_failed_flag")),
                    "after_parse_failed": bool((next_round.get("chunk_audit_result") or {}).get("parse_failed_flag")),
                    "before_truncated": bool((current_round.get("chunk_audit_result") or {}).get("truncated_flag")),
                    "after_truncated": bool((next_round.get("chunk_audit_result") or {}).get("truncated_flag")),
                    "before_risk_count": int((current_round.get("chunk_audit_result") or {}).get("risk_count") or 0),
                    "after_risk_count": int((next_round.get("chunk_audit_result") or {}).get("risk_count") or 0),
                }
            )
            retry_chunk_audit_result = next_round.get(
                "chunk_audit_result") if isinstance(next_round.get("chunk_audit_result"), dict) else {}
            progress_state["completed"] = int(
                progress_state.get("completed") or 0) + 1
            progress_state["last_completed_label"] = chunk_id
            logger.info(
                "audit_llm_request_done",
                audit_id=audit_id,
                execution_path="classic",
                llm_request_current_index=current_index,
                llm_request_total_planned=int(
                    progress_state.get("planned_total") or 0),
                request_kind="retry",
                chunk_id=chunk_id,
                retry_index=retry_step,
                round_index=current_round_index,
                risk_count=int(
                    retry_chunk_audit_result.get("risk_count") or 0),
                parse_failed_flag=bool(
                    retry_chunk_audit_result.get("parse_failed_flag")),
                truncated_flag=bool(
                    retry_chunk_audit_result.get("truncated_flag")),
            )
            _emit_classic_progress(
                request_status="done",
                current_index=current_index,
                current_label=chunk_id,
                request_kind="retry",
                retry_index=retry_step,
                round_index=current_round_index,
            )
            write_audit_trace(
                cfg,
                "audit_review_retry_done",
                {
                    "audit_id": audit_id,
                    "chunk_id": chunk_id,
                    "retry_index": retry_step,
                    "retry_reasons": list(retry_target.get("reasons") or []),
                    "before_parse_failed": bool((current_round.get("chunk_audit_result") or {}).get("parse_failed_flag")),
                    "after_parse_failed": bool((next_round.get("chunk_audit_result") or {}).get("parse_failed_flag")),
                    "before_truncated": bool((current_round.get("chunk_audit_result") or {}).get("truncated_flag")),
                    "after_truncated": bool((next_round.get("chunk_audit_result") or {}).get("truncated_flag")),
                    "before_risk_count": int((current_round.get("chunk_audit_result") or {}).get("risk_count") or 0),
                    "after_risk_count": int((next_round.get("chunk_audit_result") or {}).get("risk_count") or 0),
                },
            )
            rounds[round_index_map[chunk_id]] = next_round
        merged = _merge_multipass_audits(
            rounds,
            cfg=cfg,
            preview_clauses=preview_clauses,
            lang=lang,
        )
    merged_meta = merged.get("meta") if isinstance(
        merged.get("meta"), dict) else {}
    reliability_summary = build_reliability_summary(
        review_plan=review_plan,
        retry_logs=retry_logs,
        final_audit=merged.get("audit") if isinstance(
            merged.get("audit"), dict) else {},
    )
    merged_meta["token_budget_triggered"] = True
    merged_meta["token_budget_reasons"] = list(
        (token_budget or {}).get("reasons") or [])
    merged_meta["token_budget_round_plan"] = clause_plan.get("rounds") or []
    merged_meta["multipass_overflow_clause_count"] = int(
        clause_plan.get("overflow_clause_count") or 0)
    merged_meta["review_item_count"] = int(
        reliability_summary.get("review_item_count") or 0)
    merged_meta["review_retry_count"] = int(
        reliability_summary.get("retry_count") or 0)
    merged_meta["unresolved_review_item_count"] = int(
        reliability_summary.get("unresolved_review_item_count") or 0)
    merged_meta["reliability_level"] = str(
        reliability_summary.get("reliability_level") or "medium")
    merged["meta"] = merged_meta
    merged_raw = merged.get("raw") if isinstance(
        merged.get("raw"), dict) else {}
    merged_raw["review_plan"] = review_plan
    merged_raw["review_retry_logs"] = retry_logs
    merged["raw"] = merged_raw
    return merged


def _v2_enabled(cfg: Dict[str, Any]) -> bool:
    raw = cfg.get("audit_optimization")
    if not isinstance(raw, dict):
        return False
    value = raw.get("enabled", False)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _is_context_overflow_error(exc: Exception) -> bool:
    messages: List[str] = []
    current: Optional[BaseException] = exc
    seen = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        messages.append(str(current).lower())
        current = current.__cause__ or current.__context__
    combined = " ".join(messages)
    return any(
        marker in combined
        for marker in (
            "context length",
            "context size",
            "context window",
            "too many tokens",
            "exceeds the available context",
            "exceed_context_size",
        )
    )


def _build_v2_context_index(
    preview_clauses: List[Dict[str, Any]],
) -> str:
    rows: List[str] = []
    for clause in preview_clauses:
        if not isinstance(clause, dict):
            continue
        clause_id = str(clause.get("clause_id") or "").strip()
        clause_path = str(
            clause.get("clause_path") or clause.get("title") or ""
        ).strip()
        if clause_id:
            rows.append(f"[{clause_id}] {clause_path}".strip())
    return "\n".join(rows)


def _v2_chunk_clauses(
    units,
    preview_clauses: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Clause dicts for one chunk, carrying the ORIGINAL locator metadata.

    Chunking must not cost us traceability: every clause_id is mapped back to the
    clause produced by the parser (page_no / paragraph_no / clause_path / text) so
    the exported comment can be anchored to the right paragraph.
    """
    clause_index = {
        str(item.get("clause_id") or ""): item
        for item in list(preview_clauses or [])
        if isinstance(item, dict) and str(item.get("clause_id") or "")
    }
    clauses: List[Dict[str, Any]] = []
    seen = set()
    for unit in units:
        clause_ids = [
            clause_id
            for ref in unit.source_refs
            for clause_id in ref.preview_clause_ids
            if clause_id
        ]
        if not clause_ids:
            clause_ids = [unit.unit_id]
        page_no = next(
            (
                int(ref.page_no)
                for ref in unit.source_refs
                if ref.page_no is not None
            ),
            0,
        )
        for clause_id in clause_ids:
            if clause_id in seen:
                continue
            seen.add(clause_id)
            original = clause_index.get(clause_id)
            if isinstance(original, dict):
                clauses.append(
                    {
                        "clause_id": clause_id,
                        "anchor_id": str(
                            original.get("anchor_id") or f"clause-{clause_id}"),
                        "clause_path": str(
                            original.get("clause_path") or unit.section_path or ""),
                        "page_no": int(original.get("page_no") or page_no or 0),
                        "paragraph_no": str(
                            original.get("paragraph_no") or unit.ordinal),
                        "clause_text": str(
                            original.get("clause_text") or unit.text),
                        "locate_source": "preview_clause",
                    }
                )
                continue
            clauses.append(
                {
                    "clause_id": clause_id,
                    "anchor_id": f"clause-{clause_id}",
                    "clause_path": unit.section_path,
                    "page_no": page_no,
                    "paragraph_no": str(unit.ordinal),
                    "clause_text": unit.text,
                    "locate_source": "chunk_unit",
                }
            )
    return clauses


def _v2_chunk_prompt_payload(
    *,
    chunk,
    lang: str,
    selected_evidence: List[Dict[str, Any]],
    evidence_items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "norm_lang": "en" if str(lang).lower().startswith("en") else "zh",
        "allowed_citation_ids": set(chunk.bundle.evidence_ids),
        "messages": [dict(message) for message in chunk.bundle.messages],
        "prompt_meta": {
            "prompt_clause_count": len(chunk.units),
            "total_clause_count": len(chunk.units),
            "clauses_omitted": 0,
            "clause_chars_truncated_count": 0,
            "full_text_context_truncated": False,
            "evidence_items_used": len(selected_evidence),
            "evidence_items_omitted": max(
                0, len(evidence_items) - len(selected_evidence)
            ),
            "evidence_content_truncated_count": 0,
        },
        "max_output_tokens": chunk.bundle.output_tokens,
        "strict_result_validation": True,
    }


def _run_v2_chunk(
    *,
    cfg: Dict[str, Any],
    llm,
    lang: str,
    chunk,
    chunk_clauses: List[Dict[str, Any]],
    selected_evidence: List[Dict[str, Any]],
    evidence_items: List[Dict[str, Any]],
    retrieval_opts: Dict[str, Any],
    audit_id: str,
    trace_id: str,
    attempt: int,
) -> Dict[str, Any]:
    return _build_classic_audit(
        cfg=cfg,
        llm=llm,
        text="".join(unit.text for unit in chunk.units),
        lang=lang,
        preview_clauses=chunk_clauses,
        evidence_items=selected_evidence,
        retrieval_opts=retrieval_opts,
        audit_id=f"{audit_id}_{chunk.chunk_id}_try{attempt}",
        trace_id=trace_id,
        prompt_payload=_v2_chunk_prompt_payload(
            chunk=chunk,
            lang=lang,
            selected_evidence=selected_evidence,
            evidence_items=evidence_items,
        ),
        capture_raw_response=True,
    )


def _build_v2_classic_audit(
    cfg: Dict[str, Any],
    llm,
    text: str,
    lang: str,
    preview_clauses: List[Dict[str, Any]],
    evidence_items: List[Dict[str, Any]],
    retrieval_opts: Dict[str, Any],
    audit_id: str,
    trace_id: str,
    progress_cb: Optional[Callable[..., None]] = None,
    token_counter: Optional[MessageTokenCounter] = None,
) -> Dict[str, Any]:
    source = source_from_text(
        text,
        document_id=audit_id,
        source_hash=hashlib.sha256(str(text or "").encode("utf-8")).hexdigest(),
    )
    units = normalize_units(source, preview_clauses)
    policy = AuditPolicy.from_config(cfg)
    llm_cfg = cfg.get("llm_config") if isinstance(
        cfg.get("llm_config"), dict) else {}
    counter = token_counter or LlamaCppTokenCounter(
        str(llm_cfg.get("api_base") or ""),
        timeout_seconds=min(30, int(llm_cfg.get("timeout") or 10)),
    )
    legacy_policy = get_audit_token_policy(cfg)
    selected_evidence = _select_multipass_evidence_items(
        evidence_items,
        int(legacy_policy.get("max_evidence_items_per_round") or 12),
    )
    common_context = _build_v2_context_index(preview_clauses)
    plan = plan_audit(
        source,
        units,
        selected_evidence,
        policy,
        counter,
        common_context=common_context,
        language=lang,
    )
    raw_optimization = cfg.get("audit_optimization") if isinstance(
        cfg.get("audit_optimization"), dict) else {}
    max_attempts = int(raw_optimization.get("max_attempts_per_chunk", 2))
    max_attempts = max(1, min(max_attempts, 2))
    max_job_retries = int(raw_optimization.get("max_retries_per_job", 3))
    max_job_retries = max(0, max_job_retries)
    retries_used = 0
    rounds: List[Dict[str, Any]] = []
    successful_unit_ids = set()
    total = len(plan.chunks)
    source_hash_value = str(getattr(source, "source_hash", "") or "")
    plan_hash = chunk_store.chunk_plan_signature(plan)
    chunk_store.persist_plan(
        cfg,
        audit_id=audit_id,
        source_hash=source_hash_value,
        plan_hash=plan_hash,
        plan=plan,
        chunks_total=total,
    )
    reusable_chunks = chunk_store.load_reusable_chunks(
        cfg,
        source_hash=source_hash_value,
        plan_hash=plan_hash,
    )
    reused_chunk_count = 0
    report_progress(
        progress_cb,
        "planning",
        25,
        f"planned {total} audit chunks",
        detail={
            "execution_path": "classic_v2",
            "chunks_total": total,
            "chunks_completed": 0,
            "coverage_planned": plan.coverage.planned_ratio,
        },
    )
    for index, chunk in enumerate(plan.chunks, start=1):
        chunk_clauses = _v2_chunk_clauses(chunk.units, preview_clauses)
        reused = reusable_chunks.get(chunk.chunk_id)
        if isinstance(reused, dict):
            rounds.append(
                chunk_store.reusable_round_entry(
                    chunk=chunk,
                    chunk_clauses=chunk_clauses,
                    reusable=reused,
                    index=index,
                )
            )
            successful_unit_ids.update(chunk.bundle.primary_unit_ids)
            reused_chunk_count += 1
            # Keep a per-audit chunk manifest even when the result came from reuse,
            # so this audit stays fully traceable to its clauses and source spans.
            chunk_store.record_chunk(
                cfg,
                audit_id=audit_id,
                chunk=chunk,
                source_hash=source_hash_value,
                plan_hash=plan_hash,
                chunk_clauses=chunk_clauses,
                attempts=int(reused.get("attempts") or 1),
                status="ok",
                raw_response=str(reused.get("raw_response") or ""),
                parsed=reused.get("parsed"),
                reviewed=bool(reused.get("reviewed")),
            )
            detail = build_llm_progress_detail(
                execution_path="classic_v2",
                total_planned=total,
                total_upper_bound=total + max_job_retries,
                exact_total_known=True,
                started=index,
                completed=len(rounds),
                current_index=index,
                current_label=chunk.chunk_id,
                request_kind="reuse",
                request_status="done",
                retry_index=0,
                round_index=index,
            )
            detail.update(
                {
                    "chunks_total": total,
                    "chunks_completed": len(rounds),
                    "coverage_planned": plan.coverage.planned_ratio,
                    "chunk_reused": True,
                }
            )
            report_progress(
                progress_cb,
                "auditing",
                compute_llm_progress_percent(
                    len(rounds), total, base_percent=30, span=60
                ),
                build_llm_progress_message(detail),
                detail=detail,
            )
            continue
        last_result: Dict[str, Any] = {}
        chunk_valid = False
        final_attempt = 0
        for attempt in range(1, max_attempts + 1):
            final_attempt = attempt
            detail = build_llm_progress_detail(
                execution_path="classic_v2",
                total_planned=total,
                total_upper_bound=total + max_job_retries,
                exact_total_known=True,
                started=index,
                completed=len(rounds),
                current_index=index,
                current_label=chunk.chunk_id,
                request_kind="chunk" if attempt == 1 else "retry",
                request_status="running",
                retry_index=attempt - 1,
                round_index=index,
            )
            detail.update(
                {
                    "chunks_total": total,
                    "chunks_completed": len(rounds),
                    "coverage_planned": plan.coverage.planned_ratio,
                    "coverage_audited": build_coverage_manifest(
                        source,
                        plan.units,
                        audited_unit_ids=successful_unit_ids,
                    ).audited_ratio,
                }
            )
            report_progress(
                progress_cb,
                "auditing",
                compute_llm_progress_percent(
                    len(rounds), total, base_percent=30, span=60
                ),
                build_llm_progress_message(detail),
                detail=detail,
            )
            last_result = _run_v2_chunk(
                cfg=cfg,
                llm=llm,
                lang=lang,
                chunk=chunk,
                chunk_clauses=chunk_clauses,
                selected_evidence=selected_evidence,
                evidence_items=evidence_items,
                retrieval_opts=retrieval_opts,
                audit_id=audit_id,
                trace_id=trace_id,
                attempt=attempt,
            )
            result_meta = last_result.get("meta") if isinstance(
                last_result.get("meta"), dict) else {}
            chunk_valid = not bool(
                result_meta.get("classic_parse_failed")
                or result_meta.get("classic_response_truncated")
            )
            if chunk_valid:
                break
            if attempt < max_attempts and retries_used < max_job_retries:
                retries_used += 1
                continue
            break
        chunk_result = _build_chunk_audit_result(
            chunk_id=chunk.chunk_id,
            round_index=index,
            clauses=chunk_clauses,
            audit_result=last_result,
        )
        last_raw = last_result.get("raw") if isinstance(
            last_result.get("raw"), dict) else {}
        chunk_status = "ok"
        if not chunk_valid:
            chunk_status = (
                "parse_failed"
                if chunk_result.get("parse_failed_flag")
                else "truncated"
            )
        chunk_store.record_chunk(
            cfg,
            audit_id=audit_id,
            chunk=chunk,
            source_hash=source_hash_value,
            plan_hash=plan_hash,
            chunk_clauses=chunk_clauses,
            attempts=final_attempt,
            status=chunk_status,
            raw_response=str(last_raw.get("raw_response") or ""),
            parsed=chunk_result,
            error=chunk_status if chunk_status != "ok" else "",
        )
        if not chunk_valid:
            code = (
                "INVALID_MODEL_JSON"
                if chunk_result.get("parse_failed_flag")
                else "MODEL_RESPONSE_TRUNCATED"
            )
            raise RuntimeError(
                f"{code}: {chunk.chunk_id} did not produce a complete valid result"
            )
        successful_unit_ids.update(chunk.bundle.primary_unit_ids)
        rounds.append(
            {
                "round_index": index,
                "chunk_id": chunk.chunk_id,
                "clause_ids": [
                    str(item.get("clause_id") or "") for item in chunk_clauses
                ],
                "estimated_clause_tokens": chunk.bundle.input_tokens,
                "audit": last_result.get("audit"),
                "meta": last_result.get("meta"),
                "chunk_audit_result": chunk_result,
                "rejected_risks": list(
                    (
                        last_result.get("raw")
                        if isinstance(last_result.get("raw"), dict)
                        else {}
                    ).get("rejected_risks")
                    or []
                ),
                "attempt_count": final_attempt,
                "retry_index": max(0, final_attempt - 1),
            }
        )
    audited_coverage = build_coverage_manifest(
        source,
        plan.units,
        audited_unit_ids=successful_unit_ids,
    )
    if audited_coverage.audited_ratio != 1.0:
        raise RuntimeError(
            "COVERAGE_INCOMPLETE: successful chunks do not cover the source"
        )
    # ---- Review pass: aggregate once, then re-audit only the chunks that show a
    # concrete defect (unparsable, truncated, missing citation, conflict). Source
    # text for the flagged chunk is re-read from the plan's source spans, so the
    # re-audit never works off the previous (lossy) chunk summary.
    pre_merged = _merge_multipass_audits(
        rounds,
        cfg=cfg,
        preview_clauses=preview_clauses,
        lang=lang,
    )
    pre_meta = pre_merged.get("meta") if isinstance(
        pre_merged.get("meta"), dict) else {}
    pre_raw = pre_merged.get("raw") if isinstance(
        pre_merged.get("raw"), dict) else {}
    review_plan = build_reliability_review_plan(
        cfg=cfg,
        aggregated_audit=pre_merged.get("audit") if isinstance(
            pre_merged.get("audit"), dict) else {},
        aggregated_meta=pre_meta,
        chunk_audit_results=list(pre_raw.get("chunk_audit_results") or []),
    )
    write_audit_trace(
        cfg,
        "audit_v2_review_plan",
        {
            "audit_id": audit_id,
            "retry_chunk_ids": list(review_plan.get("retry_chunk_ids") or []),
            "review_item_count": int(review_plan.get("review_item_count") or 0),
            "should_retry": bool(review_plan.get("should_retry")),
        },
    )
    retry_logs: List[Dict[str, Any]] = []
    review_retry_count = 0
    max_retry_per_chunk = int(
        ((review_plan.get("policy") or {}).get("max_retry_per_chunk") or 0))
    if bool(review_plan.get("should_retry")):
        chunk_by_id = {
            str(getattr(item, "chunk_id", "") or ""): item
            for item in plan.chunks
        }
        round_index_by_chunk = {
            str(item.get("chunk_id") or ""): pos
            for pos, item in enumerate(rounds)
            if isinstance(item, dict)
        }
        for retry_target in list(review_plan.get("retry_chunks") or []):
            chunk_id = str(retry_target.get("chunk_id") or "")
            chunk = chunk_by_id.get(chunk_id)
            if chunk is None or chunk_id not in round_index_by_chunk:
                continue
            round_pos = round_index_by_chunk[chunk_id]
            current_round = rounds[round_pos]
            reused_row = reusable_chunks.get(chunk_id)
            if isinstance(reused_row, dict) and reused_row.get("reviewed"):
                # This chunk result was already reviewed and re-audited in an earlier
                # run; reuse it as-is instead of paying for the same review again.
                retry_logs.append(
                    {
                        "chunk_id": chunk_id,
                        "retry_performed": False,
                        "reason": "already_reviewed_reused",
                        "retry_reasons": list(retry_target.get("reasons") or []),
                    }
                )
                continue
            current_retry_index = int(current_round.get("retry_index") or 0)
            if current_retry_index >= max_retry_per_chunk:
                retry_logs.append(
                    {
                        "chunk_id": chunk_id,
                        "retry_performed": False,
                        "reason": "retry_limit_reached",
                        "retry_reasons": list(retry_target.get("reasons") or []),
                    }
                )
                continue
            retry_index = current_retry_index + 1
            attempt_no = int(current_round.get("attempt_count") or 1) + 1
            retry_clauses = _v2_chunk_clauses(chunk.units, preview_clauses)
            logger.info(
                "audit_v2_review_retry_start",
                audit_id=audit_id,
                chunk_id=chunk_id,
                retry_index=retry_index,
                retry_reasons=list(retry_target.get("reasons") or []),
            )
            detail = build_llm_progress_detail(
                execution_path="classic_v2",
                total_planned=total,
                total_upper_bound=total + max_job_retries + len(retry_logs) + 1,
                exact_total_known=False,
                started=total,
                completed=len(rounds),
                current_index=total,
                current_label=chunk_id,
                request_kind="review_retry",
                request_status="running",
                retry_index=retry_index,
                round_index=round_pos + 1,
            )
            report_progress(
                progress_cb,
                "auditing",
                compute_llm_progress_percent(
                    len(rounds), total, base_percent=30, span=60
                ),
                build_llm_progress_message(detail),
                detail=detail,
            )
            retry_result = _run_v2_chunk(
                cfg=cfg,
                llm=llm,
                lang=lang,
                chunk=chunk,
                chunk_clauses=retry_clauses,
                selected_evidence=selected_evidence,
                evidence_items=evidence_items,
                retrieval_opts=retrieval_opts,
                audit_id=f"{audit_id}_review{retry_index}",
                trace_id=trace_id,
                attempt=attempt_no,
            )
            retry_meta = retry_result.get("meta") if isinstance(
                retry_result.get("meta"), dict) else {}
            retry_raw = retry_result.get("raw") if isinstance(
                retry_result.get("raw"), dict) else {}
            retry_chunk_result = _build_chunk_audit_result(
                chunk_id=chunk_id,
                round_index=round_pos + 1,
                clauses=retry_clauses,
                audit_result=retry_result,
            )
            retry_valid = not bool(
                retry_meta.get("classic_parse_failed")
                or retry_meta.get("classic_response_truncated")
            )
            retry_status = "ok"
            if not retry_valid:
                retry_status = (
                    "parse_failed"
                    if retry_chunk_result.get("parse_failed_flag")
                    else "truncated"
                )
            chunk_store.record_chunk(
                cfg,
                audit_id=audit_id,
                chunk=chunk,
                source_hash=source_hash_value,
                plan_hash=plan_hash,
                chunk_clauses=retry_clauses,
                attempts=attempt_no,
                status=retry_status,
                raw_response=str(retry_raw.get("raw_response") or ""),
                parsed=retry_chunk_result,
                error=retry_status if retry_status != "ok" else "",
                reviewed=True,
            )
            previous_chunk_result = current_round.get("chunk_audit_result") if isinstance(
                current_round.get("chunk_audit_result"), dict) else {}
            retry_logs.append(
                {
                    "chunk_id": chunk_id,
                    "retry_performed": True,
                    "retry_index": retry_index,
                    "retry_reasons": list(retry_target.get("reasons") or []),
                    "before_risk_count": int(
                        previous_chunk_result.get("risk_count") or 0),
                    "after_risk_count": int(
                        retry_chunk_result.get("risk_count") or 0),
                    "after_status": retry_status,
                }
            )
            review_retry_count += 1
            if retry_valid:
                rounds[round_pos] = {
                    "round_index": int(
                        current_round.get("round_index") or round_pos + 1),
                    "chunk_id": chunk_id,
                    "clause_ids": [
                        str(item.get("clause_id") or "") for item in retry_clauses
                    ],
                    "estimated_clause_tokens": int(
                        current_round.get("estimated_clause_tokens") or 0),
                    "audit": retry_result.get("audit"),
                    "meta": retry_meta,
                    "chunk_audit_result": retry_chunk_result,
                    "rejected_risks": list(retry_raw.get("rejected_risks") or []),
                    "attempt_count": attempt_no,
                    "retry_index": retry_index,
                    "review_retry": True,
                }
    merged = _merge_multipass_audits(
        rounds,
        cfg=cfg,
        preview_clauses=preview_clauses,
        lang=lang,
    )
    merged_meta = merged.get("meta") if isinstance(
        merged.get("meta"), dict) else {}
    merged_meta.update(
        {
            "execution_path": "classic_v2",
            "engine_version": "v2",
            "prompt_version": (
                plan.chunks[0].bundle.template_version if plan.chunks else ""
            ),
            "plan_version": plan.plan_version,
            "chunks_total": len(plan.chunks),
            "chunks_completed": len(plan.chunks),
            "coverage_planned": plan.coverage.planned_ratio,
            "coverage_audited": audited_coverage.audited_ratio,
            "coverage_missing_ranges": list(audited_coverage.missing_ranges),
            "coverage_duplicate_ranges": list(
                audited_coverage.duplicate_primary_ranges
            ),
            "review_retry_count": retries_used,
            "review_retry_applied_count": review_retry_count,
            "review_retry_chunk_ids": list(
                review_plan.get("retry_chunk_ids") or []),
            "chunk_reused_count": reused_chunk_count,
            "plan_hash": plan_hash,
            "rejected_risk_count": sum(
                len(list(item.get("rejected_risks") or []))
                for item in rounds
            ),
            "token_budget_triggered": True,
            "token_budget_round_plan": [
                {
                    "chunk_id": chunk.chunk_id,
                    "unit_ids": list(chunk.bundle.primary_unit_ids),
                    "input_tokens": chunk.bundle.input_tokens,
                    "output_tokens": chunk.bundle.output_tokens,
                    "safety_tokens": chunk.bundle.safety_tokens,
                    "context_tokens": chunk.bundle.context_tokens,
                    "prompt_hash": chunk.bundle.prompt_hash,
                    "prefix_hash": chunk.bundle.prefix_hash,
                }
                for chunk in plan.chunks
            ],
        }
    )
    merged["meta"] = merged_meta
    merged_raw = merged.get("raw") if isinstance(
        merged.get("raw"), dict) else {}
    merged_raw["mode"] = "classic_v2"
    merged_raw["rounds"] = rounds
    merged_raw["review_plan"] = review_plan
    merged_raw["review_retry_logs"] = retry_logs
    merged["raw"] = merged_raw
    merged_meta["reliability"] = build_reliability_summary(
        review_plan=review_plan,
        retry_logs=retry_logs,
        final_audit=merged.get("audit") if isinstance(
            merged.get("audit"), dict) else {},
    )
    report_progress(
        progress_cb,
        "validating",
        95,
        "all audit chunks validated",
        detail={
            "execution_path": "classic_v2",
            "chunks_total": len(plan.chunks),
            "chunks_completed": len(plan.chunks),
            "coverage_planned": plan.coverage.planned_ratio,
            "coverage_audited": audited_coverage.audited_ratio,
        },
    )
    return merged


def _run_budget_aware_classic_audit(
    cfg: Dict[str, Any],
    llm,
    text: str,
    lang: str,
    preview_clauses: List[Dict[str, Any]],
    evidence_items: List[Dict[str, Any]],
    retrieval_opts: Dict[str, Any],
    audit_id: str,
    trace_id: str,
    token_budget: Dict[str, Any],
    prompt_payload: Dict[str, Any],
    progress_cb: Optional[Callable[..., None]] = None,
) -> Dict[str, Any]:
    if _v2_enabled(cfg):
        return _build_v2_classic_audit(
            cfg=cfg,
            llm=llm,
            text=text,
            lang=lang,
            preview_clauses=preview_clauses,
            evidence_items=evidence_items,
            retrieval_opts=retrieval_opts,
            audit_id=audit_id,
            trace_id=trace_id,
            progress_cb=progress_cb,
        )
    if bool((token_budget or {}).get("requires_multi_pass")):
        return _build_multipass_classic_audit(
            cfg=cfg,
            llm=llm,
            text=text,
            lang=lang,
            preview_clauses=preview_clauses,
            evidence_items=evidence_items,
            retrieval_opts=retrieval_opts,
            audit_id=audit_id,
            trace_id=trace_id,
            token_budget=token_budget,
            progress_cb=progress_cb,
        )
    classic_result = _build_classic_audit(
        cfg=cfg,
        llm=llm,
        text=text,
        lang=lang,
        preview_clauses=preview_clauses,
        evidence_items=evidence_items,
        retrieval_opts=retrieval_opts,
        audit_id=audit_id,
        trace_id=trace_id,
        prompt_payload=prompt_payload,
    )
    result_meta = classic_result.get("meta") if isinstance(
        classic_result.get("meta"), dict) else {}
    result_meta["token_budget_triggered"] = False
    result_meta["token_budget_reasons"] = list(
        (token_budget or {}).get("reasons") or [])
    classic_result["meta"] = result_meta
    return classic_result


def _parse_citation_pack_ref(citation_id: str) -> Dict[str, str]:
    cid = str(citation_id or "").strip()
    if not cid:
        return {"regulation_id": "", "version_id": ""}
    parts = cid.split(":")
    if len(parts) >= 4:
        return {
            "regulation_id": str(parts[1] or "").strip(),
            "version_id": str(parts[2] or "").strip(),
        }
    return {"regulation_id": "", "version_id": ""}


def get_regulation_pack_fingerprint(
    evidence_items: List[Dict[str, Any]],
    retrieval_opts: Optional[Dict[str, Any]] = None,
    lang: str = "zh",
) -> Dict[str, Any]:
    """
    Build deterministic regulation pack identity for memory isolation.
    This function maps to capability: GetRegulationPackFingerprint.
    """
    members: List[str] = []
    citation_refs: List[str] = []
    for item in list(evidence_items or []):
        citation_id = str(item.get("citation_id") or "").strip()
        if citation_id:
            citation_refs.append(citation_id)
        reg_id = str(item.get("regulation_id") or "").strip()
        ver_id = str(item.get("version_id") or "").strip()
        if not reg_id or not ver_id:
            parsed = _parse_citation_pack_ref(citation_id)
            reg_id = reg_id or parsed.get("regulation_id", "")
            ver_id = ver_id or parsed.get("version_id", "")
        if reg_id and ver_id:
            members.append(f"{reg_id}:{ver_id}")
        elif citation_id:
            members.append(f"cid:{citation_id}")
        else:
            law = str(item.get("law_title") or item.get("title") or "").strip()
            article = str(item.get("article_no") or "").strip()
            if law or article:
                members.append(f"law:{law}#{article}")
    members = sorted(set(members))
    citation_refs = sorted(set(citation_refs))

    opts = dict(retrieval_opts or {})
    scope = {
        "lang": str(lang or "zh"),
        "region": str(opts.get("region") or ""),
        "industry": str(opts.get("industry") or ""),
        "date": str(opts.get("date") or ""),
    }
    pack_seed = "|".join(members) if members else f"empty_pack:{scope['lang']}"
    regulation_pack_id = "rp_" + hashlib.sha1(
        pack_seed.encode("utf-8")).hexdigest()[:20]
    fingerprint_payload = {
        "pack_id": regulation_pack_id,
        "members": members,
        "citations": citation_refs,
        "scope": scope,
    }
    regulation_fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_payload,
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "regulation_pack_id": regulation_pack_id,
        "regulation_fingerprint": regulation_fingerprint,
        "regulation_pack_members": members,
    }


def audit_contract(
    cfg: Dict[str, Any],
    llm,
    file_path: str,
    lang: str = "zh",
    embedder=None,
    reranker=None,
    translator=None,
    retrieval_options: Optional[Dict[str, Any]] = None,
    progress_cb: Optional[Callable[..., None]] = None,
    audit_id: str = "",
) -> Dict[str, Any]:
    """
    A unified facade function for contract auditing.
    It integrates text extraction, clause preview, evidence retrieval, and LLM clause-level auditing with memory.
    It remains completely transparent to upper-level calls.
    """
    def _report(stage: str, percent: int, message: str = "", detail: Optional[Dict[str, Any]] = None) -> None:
        report_progress(progress_cb, stage, percent, message, detail=detail)

    audit_started_at = time.perf_counter()
    audit_id = str(audit_id or "").strip() or f"audit_{uuid.uuid4().hex[:12]}"
    trace_id = new_trace_id()
    _report("extracting", 15, "extracting text")
    logger.info("audit_extract_start", file=file_path,
                lang=lang, audit_id=audit_id, trace_id=trace_id)
    text, meta = extract_text_with_config(cfg, file_path)
    preview_clauses = build_preview_clauses(text)

    # Auto-generate internal markdown file for full-text LLM analysis.
    # This file is stored in a hidden internal directory (.audit_internal/)
    # and is NOT exposed to end users. It serves as intermediate material
    # for the LLM's cross-clause joint analysis and subsequent data processing.
    _internal_md_path = ""
    try:
        from app.services.markdown_export import contract_text_to_markdown
        internal_dir = os.path.join(
            cfg.get("data_dir", "data"), ".audit_internal")
        os.makedirs(internal_dir, exist_ok=True)
        source_name = os.path.basename(file_path)
        md_filename = f"{audit_id}_{os.path.splitext(source_name)[0]}.md"
        _internal_md_path = os.path.join(internal_dir, md_filename)
        md_content = contract_text_to_markdown(
            text=text,
            source_filename=source_name,
            meta=meta,
            include_metadata_header=True,
        )
        with open(_internal_md_path, "w", encoding="utf-8") as _mf:
            _mf.write(md_content)
        logger.info("audit_internal_md_generated",
                    path=_internal_md_path, length=len(md_content))
    except Exception as _md_err:
        logger.warning("audit_internal_md_failed", error=str(_md_err))

    logger.info(
        "audit_extract_done",
        file=file_path,
        text_length=len(text),
        ocr_used=meta.get("ocr_used"),
        ocr_engine=meta.get("ocr_engine"),
        page_count=meta.get("page_count")
    )
    _report("extract_done", 30, "extract complete")
    opts = _normalize_retrieval_options(retrieval_options)
    _report("retrieval", 40, "retrieving evidence")
    retrieval_embedder = embedder
    if retrieval_embedder is None:
        try:
            retrieval_embedder = _get_memory_embedder(lang, cfg)
        except TypeError:
            retrieval_embedder = _get_memory_embedder()
    if translator is None:
        retrieved = _retrieve_regulation_evidence(
            cfg, text, lang, opts, embedder=retrieval_embedder, reranker=reranker)
    else:
        retrieved = _retrieve_regulation_evidence(
            cfg, text, lang, opts, embedder=retrieval_embedder, reranker=reranker, translator=translator)
    logger.info(
        "audit_retrieval_done",
        file=file_path,
        mode=opts.get("audit_mode"),
        used=retrieved.get("used"),
        queries=retrieved.get("queries"),
        success=retrieved.get("query_success", 0),
        failed=retrieved.get("query_failed", 0),
        evidence_count=len(retrieved.get("items") or []),
        degraded=retrieved.get("retrieval_degraded", False),
        degraded_reasons=retrieved.get("retrieval_degraded_reasons", []),
    )
    _report("retrieval_done", 55, "evidence ready")
    if opts.get("require_full_coverage") and _safe_int(retrieved.get("query_failed", 0), 0) > 0:
        raise RuntimeError("retrieval coverage incomplete")
    evidence_items = [_normalize_citation_item(
        it) for it in (retrieved.get("items") or [])]
    classic_prompt_payload = _build_classic_prompt_payload(
        cfg=cfg,
        text=text,
        lang=lang,
        preview_clauses=preview_clauses,
        evidence_items=evidence_items,
    )
    classic_audit_max_tokens = int(cfg.get("classic_audit_max_tokens") or 4096)
    token_budget = build_contract_audit_budget(
        cfg,
        full_contract_text=text,
        preview_clauses=preview_clauses,
        evidence_items=evidence_items,
        prompt_messages=list(classic_prompt_payload.get("messages") or []),
        prompt_meta=dict(classic_prompt_payload.get("prompt_meta") or {}),
        requested_output_tokens=classic_audit_max_tokens,
    )
    regulation_identity = get_regulation_pack_fingerprint(
        evidence_items=evidence_items,
        retrieval_opts=opts,
        lang=lang,
    )
    write_audit_trace(
        cfg,
        "audit_token_budget",
        {
            "audit_id": audit_id,
            "file_path": file_path,
            "requires_multi_pass": bool(token_budget.get("requires_multi_pass")),
            "reasons": list(token_budget.get("reasons") or []),
            "truncation_reasons": list(token_budget.get("truncation_reasons") or []),
            "policy": dict(token_budget.get("policy") or {}),
            "estimates": dict(token_budget.get("estimates") or {}),
            "prompt_meta": dict(token_budget.get("prompt_meta") or {}),
        },
    )
    write_audit_trace(
        cfg,
        "contract_split",
        {
            "audit_id": audit_id,
            "file_path": file_path,
            "lang": lang,
            "text_length": len(text),
            "clause_count": len(preview_clauses),
            "clauses": [
                {
                    "clause_id": str(c.get("clause_id") or ""),
                    "clause_path": str(c.get("clause_path") or ""),
                    "page_no": int(c.get("page_no") or 0),
                    "paragraph_no": str(c.get("paragraph_no") or ""),
                    "text_len": len(str(c.get("clause_text") or "")),
                    "text_preview": trace_clip(c.get("clause_text"), 220),
                }
                for c in preview_clauses[:120]
            ],
            "regulation_pack_id": regulation_identity.get("regulation_pack_id", ""),
            "regulation_fingerprint": regulation_identity.get("regulation_fingerprint", ""),
            "regulation_pack_members": regulation_identity.get("regulation_pack_members", []),
        },
    )
    memory_runtime_cfg = _get_memory_runtime_config(cfg)
    memory_temporary_disable_cfg = _get_memory_temporary_disable_config(cfg)
    runtime_memory_enabled = bool(
        memory_runtime_cfg.get("memory_module_enabled", True))
    memory_temporarily_disabled = bool(
        memory_temporary_disable_cfg.get("enabled", False))
    effective_disable_mode = str(
        memory_temporary_disable_cfg.get("fallback_mode")
        or memory_runtime_cfg.get("memory_mode_when_disabled")
        or "classic"
    ).strip().lower()
    memory_enabled = runtime_memory_enabled and not memory_temporarily_disabled
    fallback_on_error = bool(
        memory_runtime_cfg.get("memory_disable_fallback_on_error", True))
    execution_path = "memory"
    memory_result: Dict[str, Any] = {}
    _report("auditing", 70, "auditing clauses")
    if memory_enabled:
        logger.info("audit_memory_enabled", file=file_path,
                    clauses=len(preview_clauses))
        custom_embedder = None
        if callable(_get_memory_embedder):
            try:
                custom_embedder = _get_memory_embedder(lang, cfg)
            except TypeError:
                custom_embedder = _get_memory_embedder()
        if (custom_embedder is not None and hasattr(custom_embedder, "encode")) or HybridSearcher is not None:
            runtime_getter = (lambda _lang="zh", cfg=None: custom_embedder) if (
                custom_embedder is not None and hasattr(
                    custom_embedder, "encode")
            ) else None
            setter = getattr(memory_pipeline_module,
                             "set_runtime_overrides", None)
            if callable(setter):
                setter(
                    get_memory_embedder=runtime_getter,
                    hybrid_searcher=HybridSearcher if HybridSearcher is not None else None,
                )
        try:
            memory_result = execute_memory_audit(
                cfg=cfg,
                llm=llm,
                text=text,
                lang=lang,
                preview_clauses=preview_clauses,
                evidence_items=evidence_items,
                retrieval_opts=opts,
                trace_context={
                    "module": "contract_audit",
                    "file_path": file_path,
                    "audit_id": audit_id,
                    "trace_id": trace_id,
                    "regulation_pack_id": regulation_identity.get("regulation_pack_id", ""),
                    "regulation_fingerprint": regulation_identity.get("regulation_fingerprint", ""),
                },
                progress_cb=progress_cb,
            )
            execution_path = "memory"
        except Exception as e:
            if not fallback_on_error:
                raise
            logger.warning("memory_audit_failed_fallback_to_classic",
                           audit_id=audit_id, error=str(e))
            memory_result = _run_budget_aware_classic_audit(
                cfg=cfg,
                llm=llm,
                text=text,
                lang=lang,
                preview_clauses=preview_clauses,
                evidence_items=evidence_items,
                retrieval_opts=opts,
                audit_id=audit_id,
                trace_id=trace_id,
                token_budget=token_budget,
                prompt_payload=classic_prompt_payload,
                progress_cb=progress_cb,
            )
            execution_path = str(
                (memory_result.get("meta") or {}).get("execution_path") or "classic_fallback")
    else:
        if memory_temporarily_disabled:
            logger.warning(
                "memory_temporarily_disabled audit_id=%s file=%s service_node=%s trigger_source=%s disable_reason=%s fallback_mode=%s runtime_memory_enabled=%s clauses=%s",
                audit_id,
                file_path,
                socket.gethostname(),
                str(memory_temporary_disable_cfg.get(
                    "trigger_source") or "config.memory_temporary_disable"),
                str(memory_temporary_disable_cfg.get(
                    "reason") or "edge_llm_context_limit"),
                effective_disable_mode,
                runtime_memory_enabled,
                len(preview_clauses),
            )
        else:
            logger.info("audit_memory_disabled_use_classic",
                        audit_id=audit_id, file=file_path)
        memory_result = _run_budget_aware_classic_audit(
            cfg=cfg,
            llm=llm,
            text=text,
            lang=lang,
            preview_clauses=preview_clauses,
            evidence_items=evidence_items,
            retrieval_opts=opts,
            audit_id=audit_id,
            trace_id=trace_id,
            token_budget=token_budget,
            prompt_payload=classic_prompt_payload,
            progress_cb=progress_cb,
        )
        execution_path = str(
            (memory_result.get("meta") or {}).get("execution_path")
            or ("classic" if effective_disable_mode == "classic" else effective_disable_mode)
        )
    _report("audit_done", 90, "audit complete")
    memory_meta = memory_result.get("meta") if isinstance(
        memory_result.get("meta"), dict) else {}
    citation_ids = [
        str(it.get("citation_id", "")).strip()
        for it in evidence_items
        if str(it.get("citation_id", "")).strip()
    ]
    audit_duration_ms = int((time.perf_counter() - audit_started_at) * 1000)
    output_meta = {
        "language": "en" if str(lang or "").lower() == "en" else "zh",
        "audit_id": audit_id,
        "text_length": len(text),
        "ocr_used": meta.get("ocr_used"),
        "ocr_engine": meta.get("ocr_engine"),
        "page_count": meta.get("page_count"),
        "llm_model": (cfg.get("llm_config") or {}).get("model", ""),
        "retrieval_mode": opts.get("audit_mode"),
        "risk_detection_mode": opts.get("risk_detection_mode"),
        "retrieval_used": retrieved.get("used"),
        "retrieval_queries": retrieved.get("queries"),
        "retrieval_chunk_total": retrieved.get("chunk_total", 0),
        "retrieval_query_success": retrieved.get("query_success", 0),
        "retrieval_query_failed": retrieved.get("query_failed", 0),
        "retrieval_degraded": bool(retrieved.get("retrieval_degraded", False)),
        "retrieval_degraded_reasons": list(retrieved.get("retrieval_degraded_reasons") or []),
        "retrieval_coverage": 0.0 if _safe_int(retrieved.get("chunk_total", 0), 0) == 0 else round(_safe_int(retrieved.get("query_success", 0), 0) / _safe_int(retrieved.get("chunk_total", 0), 0), 4),
        "retrieval_failed_chunks": retrieved.get("failed_chunks", []),
        "retrieved_chunks": len(evidence_items),
        "evidence_count": len(evidence_items),
        "citation_ids": citation_ids,
        "retrieval_filters": {
            "region": opts.get("region"),
            "date": opts.get("date"),
            "industry": opts.get("industry"),
            "tax_focus": opts.get("tax_focus")
        },
        "tax_focus": opts.get("tax_focus"),
        "require_full_coverage": opts.get("require_full_coverage"),
        "tax_evidence_count": len([
            it for it in evidence_items
            if _safe_int(it.get("tax_relevance", 0), 0) > 0
        ]),
        "preview_clause_total": len(preview_clauses),
        "audit_token_budget": token_budget,
        "audit_token_budget_requires_multi_pass": bool(token_budget.get("requires_multi_pass")),
        "audit_token_budget_reasons": list(token_budget.get("reasons") or []),
        "audit_duration_ms": audit_duration_ms,
        "regulation_pack_id": regulation_identity.get("regulation_pack_id", ""),
        "regulation_fingerprint": regulation_identity.get("regulation_fingerprint", ""),
        "regulation_pack_members": regulation_identity.get("regulation_pack_members", []),
        "memory_module_enabled": memory_enabled,
        "memory_runtime_module_enabled": runtime_memory_enabled,
        "memory_temporarily_disabled": memory_temporarily_disabled,
        "memory_temporary_disable_reason": memory_temporary_disable_cfg.get("reason"),
        "memory_temporary_disable_trigger_source": memory_temporary_disable_cfg.get("trigger_source"),
        "memory_temporary_disable_fallback_mode": memory_temporary_disable_cfg.get("fallback_mode"),
        "execution_path": execution_path,
        **memory_meta,
    }
    write_audit_trace(
        cfg,
        "audit_done",
        {
            "audit_id": audit_id,
            "file_path": file_path,
            "duration_ms": audit_duration_ms,
            "preview_clause_total": len(preview_clauses),
            "memory_rounds": output_meta.get("memory_clause_rounds", 0),
            "memory_llm_call_count": output_meta.get("memory_llm_call_count", 0),
            "memory_llm_total_tokens": output_meta.get("memory_llm_total_tokens", 0),
            "memory_temporarily_disabled": memory_temporarily_disabled,
            "memory_temporary_disable_reason": output_meta.get("memory_temporary_disable_reason", ""),
            "memory_temporary_disable_trigger_source": output_meta.get("memory_temporary_disable_trigger_source", ""),
            "risk_count": output_meta.get("memory_report_risk_count", 0),
            "suppressed_missing_risks": output_meta.get("suppressed_missing_risks", 0),
            "parse_failed_clauses": output_meta.get("parse_failed_clauses", 0),
        },
    )
    logger.info(
        "audit_metrics_done",
        file=file_path,
        duration_ms=audit_duration_ms,
        audit_id=audit_id,
        preview_clauses=len(preview_clauses),
        memory_rounds=output_meta.get("memory_clause_rounds", 0),
        llm_calls=output_meta.get("memory_llm_call_count", 0),
        llm_total_tokens=output_meta.get("memory_llm_total_tokens", 0),
        risks=output_meta.get("memory_report_risk_count", 0),
    )
    episode_meta: Dict[str, Any] = {}
    if execution_path == "memory":
        try:
            episode_meta = save_audit_episode(
                cfg=cfg,
                audit_id=audit_id,
                regulation_pack_id=regulation_identity.get(
                    "regulation_pack_id", ""),
                regulation_fingerprint=regulation_identity.get(
                    "regulation_fingerprint", ""),
                retrieval_opts=opts,
                preview_clauses=preview_clauses,
                audit=memory_result.get("audit") if isinstance(
                    memory_result.get("audit"), dict) else {},
                meta=output_meta,
            )
        except Exception as e:
            logger.warning("save_audit_episode_failed",
                           audit_id=audit_id, error=str(e))
            episode_meta = {"saved": False,
                            "reason": "exception", "error": str(e)}
    else:
        episode_meta = {
            "saved": False,
            "reason": "memory_module_disabled_or_fallback",
        }
    output_meta["episode_saved"] = bool(episode_meta.get("saved", False))
    output_meta["episode_id"] = str(episode_meta.get("episode_id") or "")
    output_meta["episode_status"] = str(episode_meta.get("status") or "")
    final_audit = memory_result.get("audit") if isinstance(
        memory_result.get("audit"), dict) else {}
    final_report = build_contract_final_report(
        audit=final_audit,
        meta=output_meta,
        file_path=file_path,
        contract_id=audit_id,
    )
    final_audit["final_report"] = final_report
    final_audit["pipeline_summary"] = final_report.get("pipeline_summary") if isinstance(
        final_report.get("pipeline_summary"), dict) else {}
    final_audit["reliability_summary"] = final_report.get("reliability_summary") if isinstance(
        final_report.get("reliability_summary"), dict) else {}
    return {
        "audit": final_audit,
        "meta": output_meta,
        "raw": memory_result.get("raw") if isinstance(memory_result.get("raw"), dict) else {"mode": "memory"}
    }
