"""Prompt and parsing helpers for memory pipeline clause auditing."""

from typing import Any, Dict, List, Tuple
import json
import re


def format_failure_patterns(patterns: List[Dict[str, Any]], lang: str) -> str:
    rows: List[str] = []
    for idx, item in enumerate(list(patterns or []), start=1):
        status = str(item.get("reviewer_status")
                     or item.get("outcome") or "").strip()
        label = str(item.get("risk_label") or "").strip()
        text = str(item.get("pattern_text") or "").strip()
        if not text:
            continue
        rows.append(f"- F{idx} [{status}/{label}] {text}")
    if not rows:
        return ""
    if str(lang or "").lower() == "en":
        return "Failure Memory Patterns:\n" + "\n".join(rows) + "\n\n"
    return "失败经验模式:\n" + "\n".join(rows) + "\n\n"


def format_case_memories(hits: List[Dict[str, Any]], lang: str) -> str:
    rows: List[str] = []
    for idx, item in enumerate(list(hits or []), start=1):
        label = str(item.get("risk_label") or "").strip()
        excerpt = str(item.get("clause_text_excerpt") or "").strip()
        reasoning = str(item.get("risk_reasoning") or "").strip()
        basis = ",".join([str(x).strip() for x in (
            item.get("legal_basis") or []) if str(x).strip()][:2])
        text = " | ".join([x for x in [excerpt, reasoning, basis] if x])
        if not text:
            continue
        rows.append(f"- M{idx} [{label}] {text}")
    if not rows:
        return ""
    if str(lang or "").lower() == "en":
        return "Case Memory Hits:\n" + "\n".join(rows) + "\n\n"
    return "案例记忆命中:\n" + "\n".join(rows) + "\n\n"


def format_workflow_memories(hits: List[Dict[str, Any]], lang: str) -> str:
    rows: List[str] = []
    for idx, item in enumerate(list(hits or []), start=1):
        title = str(item.get("workflow_title") or "").strip()
        steps = str(item.get("workflow_steps") or "").strip()
        if not title and not steps:
            continue
        text = " | ".join([x for x in [title, steps] if x])
        rows.append(f"- W{idx} {text}")
    if not rows:
        return ""
    if str(lang or "").lower() == "en":
        return "Workflow Memory (Planning):\n" + "\n".join(rows) + "\n\n"
    return "工作流记忆(审计规划):\n" + "\n".join(rows) + "\n\n"


def load_llm_json_object(raw_text: str) -> Dict[str, Any]:
    s = str(raw_text or "").strip()
    if not s:
        return json.loads(s)
    fenced = re.sub(r"^\s*```(?:json)?\s*", "", s,
                    count=1, flags=re.IGNORECASE)
    fenced = re.sub(r"\s*```\s*$", "", fenced, count=1,
                    flags=re.IGNORECASE).strip()
    candidates: List[str] = []
    if fenced:
        candidates.append(fenced)
        left = fenced.find("{")
        right = fenced.rfind("}")
        if left >= 0 and right > left:
            obj_text = fenced[left:right + 1].strip()
            if obj_text and obj_text != fenced:
                candidates.append(obj_text)
    if s and s not in candidates:
        candidates.append(s)
    last_error = None
    for item in candidates:
        try:
            out = json.loads(item)
            if isinstance(out, dict):
                return out
        except Exception as e:
            last_error = e
    if last_error is not None:
        raise last_error
    return json.loads(fenced)


def build_clause_prompt(
    *,
    norm_lang: str,
    evidence_whitelist_text: str,
    short_memory: str,
    case_memory_block: str,
    failure_patterns_block: str,
    workflow_memory_block: str,
    global_context_block: str,
    long_memory_block: str,
    clause_title: str,
    clause_text: str,
    full_contract_context: str = "",
) -> Tuple[str, str]:
    """
    Build the LLM prompt for clause-level auditing.

    Args:
        full_contract_context: Condensed full contract text for cross-clause
            joint analysis. When provided, the LLM can verify whether a
            "missing" element is actually covered elsewhere in the contract,
            reducing false-positive risks from isolated clause analysis.
    """
    # Build the full-contract context block (injected before the target clause)
    full_ctx_block = ""
    if full_contract_context and full_contract_context.strip():
        if norm_lang == "en":
            full_ctx_block = (
                "Full Contract Context (for cross-clause verification):\n"
                f"{full_contract_context}\n\n"
            )
        else:
            full_ctx_block = (
                "合同全文上下文(用于跨条款联合校验):\n"
                f"{full_contract_context}\n\n"
            )

    CHECKLIST_ZH = (
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

    CHECKLIST_EN = (
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

    MANDATORY_RULES_ZH = (
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

    MANDATORY_RULES_EN = (
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

    MANDATORY_OUTPUT_RULES_ZH = (
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

    MANDATORY_OUTPUT_RULES_EN = (
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

    KEY_AUDIT_AREAS_ZH = (
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

    KEY_AUDIT_AREAS_EN = (
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

    AUDIT_REQUIREMENTS_ZH = (
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

    AUDIT_REQUIREMENTS_EN = (
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
            + MANDATORY_RULES_EN
            + KEY_AUDIT_AREAS_EN
            + AUDIT_REQUIREMENTS_EN
            + CHECKLIST_EN
            + MANDATORY_OUTPUT_RULES_EN
            + "Audit strictly based on the input; prioritize the whitelisted laws; output MUST be JSON.\n"
            "Check whether this clause matches any known false-positive or false-negative pattern before finalizing.\n"
            "Short memory and long-term hits are for factual reference only; do not directly reuse their historical conclusions.\n"
            "For risk items, if a whitelist item can be matched, you MUST fill citation_id with an exact whitelist ID and MUST NOT invent any ID; only leave citation_id blank when no match can be determined, and still fill law_title and article_no for post-processing mapping and verification.\n"
            "If outputting 'unclear/unspecified/unmentioned/missing' risks, refer to the short memory, long-term hits, and the current clause; suppress this risk ONLY if the same element has been covered by explicit and enforceable provisions.\n"
            "CRITICAL CROSS-CLAUSE RULE: Before flagging any 'missing/unspecified' risk, you MUST first check the Full Contract Context below. "
            "If the element is already explicitly covered in another clause of the same contract, do NOT flag it as a risk. "
            "Only flag a risk when the element is genuinely absent from the ENTIRE contract.\n"
            "CRITICAL CONTEXT RULE: A clause that appears non-compliant in isolation may be fully compliant when read together with other clauses. "
            "Always verify against the full contract context before assigning high/medium risk levels.\n"
            "Do NOT output reasoning processes, analysis processes, or extra explanations. ONLY output the final JSON.\n"
            f"Whitelist:\n{evidence_whitelist_text}\n\n"
            f"Short Memory:\n{short_memory}\n\n"
            f"{case_memory_block}"
            f"{failure_patterns_block}"
            f"{workflow_memory_block}"
            f"{global_context_block}"
            f"{long_memory_block}"
            f"{full_ctx_block}"
            f"Clause Title: {clause_title}\n"
            f"Clause Text:\n{clause_text}\n\n"
            "JSON: {\"summary\":\"\",\"risks\":[{\"level\":\"high|medium|low\",\"issue\":\"\",\"suggestion\":\"\",\"citation_id\":\"\",\"law_title\":\"\",\"article_no\":\"\",\"evidence\":\"\",\"confidence\":0.0}]}"
        )
        return system, user

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
        + MANDATORY_RULES_ZH
        + KEY_AUDIT_AREAS_ZH
        + AUDIT_REQUIREMENTS_ZH
        + CHECKLIST_ZH
        + MANDATORY_OUTPUT_RULES_ZH
        + "仅根据输入审计；优先参考白名单法条；输出必须是JSON。\n"
        "Check whether this clause matches any known false-positive or false-negative pattern before finalizing.\n"
        "短记忆和长期命中仅作为事实参考，不可直接复用其中历史结论。\n"
        "风险项如能匹配白名单，必须填写与白名单完全一致的 citation_id，且不得编造ID；仅在确实无法匹配时才可留空，并必须尽量填写 law_title 与 article_no 供后处理映射校验。\n"
        "若要输出‘未明确/未约定/未提及/缺失’风险，可参考短记忆、长期命中与当前条款；仅在同一要素已被明确且可执行约定覆盖时才抑制该风险。\n"
        "【跨条款联合校验规则】在标记任何‘缺失/未约定/未明确’风险前，必须先核查下方合同全文上下文。"
        "若该要素已在合同其他条款中被明确约定，则不得标记为风险。"
        "仅当该要素在整份合同中确实完全缺失时，才可标记风险。\n"
        "【上下文关联规则】单独看似不合规的条款，结合合同其他条款后可能完全合规。"
        "在给出high/medium风险等级前，必须结合合同全文上下文进行验证，避免因条款脱离正文而产生误判。\n"
        "禁止输出推理过程、分析过程与额外解释，只输出最终JSON。\n"
        f"白名单:\n{evidence_whitelist_text}\n\n"
        f"短记忆:\n{short_memory}\n\n"
        f"{case_memory_block}"
        f"{failure_patterns_block}"
        f"{workflow_memory_block}"
        f"{global_context_block}"
        f"{long_memory_block}"
        f"{full_ctx_block}"
        f"条款标题:{clause_title}\n"
        f"条款正文:\n{clause_text}\n\n"
        "JSON: {\"summary\":\"\",\"risks\":[{\"level\":\"high|medium|low\",\"issue\":\"\",\"suggestion\":\"\",\"citation_id\":\"\",\"law_title\":\"\",\"article_no\":\"\",\"evidence\":\"\",\"confidence\":0.0}]}"
    )
    return system, user
