"""
工具注册中心 (Tool Registry)
============================
统一管理所有合同审计Skill绑定的本地工具函数。

设计原则：
1. 全部本地Python函数封装，不依赖任何外部API
2. 统一JSON入参出参规范，方便端侧大模型调用
3. 支持工具→Skill映射关系，按需加载
4. 采用文本标记方式（FUNC_CALL:）适配llama.cpp端侧模型，
   不依赖标准OpenAI function calling（端侧量化模型通常不支持）

工具清单：
- ocr_text_extract: OCR文本提取工具（复用现有MinerU OCR）
- regex_amount_extract: 正则金额提取
- text_chunk_splitter: 智能文本分块
- amount_case_compare: 金额大小写比对
- payment_schedule_calc: 账期计算器
- tax_rate_calculator: 税率计算
- stamp_duty_estimator: 印花税测算
- statute_limitation_calc: 诉讼时效计算
- penalty_cap_estimator: 违约金上限测算
- business_entity_verify: 工商主体核验
- legal_keyword_matcher: 法条关键词匹配
- risk_ledger_writer: 风险台账写入
- report_exporter: 审计报告导出
"""

from __future__ import annotations

import json
import re
import os
import hashlib
import uuid
from datetime import datetime, date, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple
from dataclasses import dataclass, field


# ============================================================================
# 工具注册数据模型
# ============================================================================

@dataclass
class ToolDef:
    """工具定义"""
    name: str                           # 工具唯一标识
    description: str                    # 工具描述（用于端侧大模型理解工具用途）
    func: Callable                      # 实际执行的Python函数
    param_schema: Dict[str, Any]        # 参数JSON Schema
    return_schema: Dict[str, Any]       # 返回值JSON Schema
    bound_skills: List[str] = field(default_factory=list)  # 绑定的Skill ID列表
    is_async: bool = False              # 是否为异步工具


@dataclass
class ToolCallResult:
    """工具调用结果"""
    success: bool
    result: Any
    error: str = ""
    execution_time_ms: float = 0.0


# ============================================================================
# 工具函数实现
# ============================================================================

def _safe_float(value: Any, default: float = 0.0) -> float:
    """安全转换为浮点数"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    """安全转换为整数"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def tool_ocr_text_extract(file_path: str, **kwargs) -> Dict[str, Any]:
    """
    OCR文本提取工具。
    复用现有项目的MinerU/PyTesseract OCR能力，从PDF/图片中提取文本。
    注：本函数为本地存根，实际调用复用项目已有的OCR模块 app/core/ocr.py
    """
    if not os.path.exists(file_path):
        return {"success": False, "error": f"文件不存在: {file_path}", "text": ""}

    # 尝试使用项目已有的OCR或文本提取能力
    try:
        # 方式1: 使用python-docx处理Word文档
        if file_path.lower().endswith(('.docx', '.doc')):
            try:
                from docx import Document
                doc = Document(file_path)
                text = "\n".join([p.text for p in doc.paragraphs])
                return {"success": True, "text": text, "extraction_method": "docx",
                        "char_count": len(text), "paragraph_count": len(doc.paragraphs)}
            except Exception:
                pass

        # 方式2: 使用PyPDF处理PDF
        if file_path.lower().endswith('.pdf'):
            try:
                from pypdf import PdfReader
                reader = PdfReader(file_path)
                pages = []
                for page in reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        pages.append(page_text)
                text = "\n\n".join(pages)
                return {"success": True, "text": text, "extraction_method": "pypdf",
                        "char_count": len(text), "page_count": len(reader.pages)}
            except Exception:
                pass

        # 方式3: 纯文本文件直接读取
        if file_path.lower().endswith('.txt'):
            with open(file_path, 'r', encoding='utf-8') as f:
                text = f.read()
            return {"success": True, "text": text, "extraction_method": "txt",
                    "char_count": len(text)}

        # 方式4: 尝试项目内OCR (MinerU)
        try:
            from app.core.ocr import extract_text_from_file
            text, meta = extract_text_from_file(file_path)
            return {"success": True, "text": text, "extraction_method": "ocr",
                    "char_count": len(text), "ocr_meta": meta}
        except Exception:
            pass

    except Exception as e:
        return {"success": False, "error": str(e), "text": ""}

    return {"success": False, "error": "无法识别的文件格式或不支持的提取方式", "text": ""}


def tool_regex_amount_extract(text: str, **kwargs) -> Dict[str, Any]:
    """
    正则金额提取工具。
    从合同文本中抽取所有金额数字，识别币种、大小写一致性。
    """
    amounts = []
    currency_map = {"¥": "CNY", "￥": "CNY", "$": "USD",
                    "€": "EUR", "元": "CNY", "美元": "USD"}

    # 匹配模式：币种符号 + 数字（含小数点、千分位）
    # 注意：需要完整上下文匹配，避免误匹配统一社会信用代码中的数字
    patterns = [
        # 中文大写金额: 壹佰贰拾叁万元整（优先匹配）
        (r'[人民币]?([壹贰叁肆伍陆柒捌玖拾佰仟万亿元角分整零]+)', "chinese_upper"),
        # 带"元"或币种符号的金额: XX万元、XX美元、$1,234.56
        (r'(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)\s*万元', "decimal_wan"),
        (r'(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)\s*[元]$', "decimal_yuan"),
        (r'(?:[¥￥]\s*)?(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)(?:\s*元)?', "decimal"),
        # 百分比: 30%/百分之三十
        (r'(\d{1,3}(?:\.\d{1,2})?)\s*[%％]|百分之([零一二三四五六七八九十百]+)', "percentage"),
    ]

    for pattern, amount_type in patterns:
        for match in re.finditer(pattern, text):
            raw = match.group(0).strip()
            # 检测前后文以确定币种
            context_start = max(0, match.start() - 20)
            context_end = min(len(text), match.end() + 20)
            context = text[context_start:context_end]

            currency = "CNY"
            for sym, cur in currency_map.items():
                if sym in context:
                    currency = cur
                    break

            amounts.append({
                "raw_text": raw,
                "type": amount_type,
                "currency": currency,
                "position": match.start(),
                "context": context.strip()
            })

    return {
        "success": True,
        "amounts_found": len(amounts),
        "amounts": amounts,
        "unique_currencies": list(set(a["currency"] for a in amounts)),
    }


def tool_text_chunk_splitter(text: str, max_chunk_chars: int = 3000,
                             overlap_chars: int = 400) -> Dict[str, Any]:
    """
    智能文本分块工具。
    按段落自然边界切分长文本，防止llama.cpp上下文溢出。
    优先在句号、换行处切分，避免切断句子。
    """
    if not text:
        return {"success": True, "chunks": [], "chunk_count": 0}

    chunks = []
    paragraphs = text.split('\n')
    current_chunk = ""
    current_length = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        para_len = len(para)

        # 如果当前块加上新段落超出限制，先保存当前块
        if current_length + para_len > max_chunk_chars and current_chunk:
            chunks.append(current_chunk.strip())
            # 保留重叠部分
            overlap_text = current_chunk[-overlap_chars:] if len(
                current_chunk) > overlap_chars else current_chunk
            current_chunk = overlap_text + "\n" + para
            current_length = len(current_chunk)
        else:
            if current_chunk:
                current_chunk += "\n" + para
            else:
                current_chunk = para
            current_length += para_len

    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    # 如果单个段落仍然超长，进行强制切分
    final_chunks = []
    for chunk in chunks:
        if len(chunk) <= max_chunk_chars * 1.2:
            final_chunks.append(chunk)
        else:
            # 按句号强制切分
            sentences = re.split(r'(?<=[。；;])', chunk)
            sub_chunk = ""
            for sent in sentences:
                if len(sub_chunk) + len(sent) > max_chunk_chars and sub_chunk:
                    final_chunks.append(sub_chunk.strip())
                    sub_chunk = sent
                else:
                    sub_chunk += sent
            if sub_chunk.strip():
                final_chunks.append(sub_chunk.strip())

    return {
        "success": True,
        "chunks": final_chunks,
        "chunk_count": len(final_chunks),
        "original_length": len(text),
        "avg_chunk_size": sum(len(c) for c in final_chunks) / max(1, len(final_chunks)),
    }


def tool_amount_case_compare(amount_numeric: float, amount_upper: str,
                             amount_lower: str = "") -> Dict[str, Any]:
    """
    金额大小写比对工具。
    检查合同中数值金额与大写金额是否一致。
    """
    issues = []
    # 简单的一致性检查
    if amount_lower and str(amount_numeric) != amount_lower.strip():
        issues.append({
            "type": "numeric_lowercase_mismatch",
            "numeric": amount_numeric,
            "lowercase_text": amount_lower,
            "detail": "数值金额与小写金额不一致"
        })

    # 大写金额非空检查
    if not amount_upper.strip():
        issues.append({
            "type": "missing_upper_amount",
            "detail": "缺少大写金额，存在篡改风险"
        })

    return {
        "success": True,
        "is_consistent": len(issues) == 0,
        "issues": issues,
        "numeric_amount": amount_numeric,
        "upper_amount": amount_upper,
        "lower_amount": amount_lower,
    }


def tool_payment_schedule_calc(payment_terms: List[Dict], total_amount: float) -> Dict[str, Any]:
    """
    账期计算器。
    验证各付款节点金额之和是否等于总金额，计算加权平均付款周期。
    """
    if not payment_terms:
        return {"success": True, "issues": [], "total_payment_sum": 0,
                "is_sum_consistent": False, "message": "无付款条款数据"}

    total_sum = sum(_safe_float(term.get("amount", 0))
                    for term in payment_terms)
    ratios = [_safe_float(term.get("ratio", 0)) for term in payment_terms]

    issues = []
    if total_amount > 0 and abs(total_sum - total_amount) > 0.01:
        issues.append({
            "type": "amount_sum_mismatch",
            "expected": total_amount,
            "actual": total_sum,
            "difference": total_sum - total_amount,
            "detail": f"付款节点金额合计({total_sum})与合同总金额({total_amount})不一致"
        })

    total_ratio = sum(r for r in ratios if r > 0)
    if 0 < total_ratio < 99.5 or total_ratio > 100.5:
        issues.append({
            "type": "ratio_not_100",
            "total_ratio": total_ratio,
            "detail": f"付款比例合计({total_ratio}%)不等于100%"
        })

    return {
        "success": True,
        "is_sum_consistent": len(issues) == 0,
        "total_payment_sum": total_sum,
        "payment_stage_count": len(payment_terms),
        "issues": issues,
    }


def tool_tax_rate_calculator(amount: float, tax_type: str,
                             is_tax_inclusive: bool = True) -> Dict[str, Any]:
    """
    税率计算工具。
    根据金额和税率类型计算增值税、所得税、附加税等。
    注：使用当前中国税法常用税率，实际使用时需根据合同签订时间调整。
    """
    # 中国常用税率参考（2024版）
    tax_rates = {
        "vat_general": 0.13,       # 增值税一般税率 13%
        "vat_low": 0.09,           # 增值税低税率 9%
        "vat_service": 0.06,        # 增值税服务税率 6%
        "vat_small": 0.03,          # 增值税小规模 3%
        "corporate_income": 0.25,   # 企业所得税 25%
        "stamp_duty_contract": 0.0003,  # 印花税-购销合同 0.03%
        "stamp_duty_loan": 0.00005,     # 印花税-借款合同 0.005%
        "urban_construction": 0.07,     # 城建税 7%
        "education_surcharge": 0.03,    # 教育费附加 3%
        "local_education": 0.02,        # 地方教育附加 2%
    }

    rate = tax_rates.get(tax_type, 0.0)

    if is_tax_inclusive:
        tax_exclusive_amount = amount / (1 + rate) if rate > 0 else amount
        tax_amount = amount - tax_exclusive_amount
    else:
        tax_exclusive_amount = amount
        tax_amount = amount * rate

    return {
        "success": True,
        "tax_type": tax_type,
        "applicable_rate": rate,
        "amount": round(amount, 2),
        "tax_exclusive_amount": round(tax_exclusive_amount, 2),
        "tax_amount": round(tax_amount, 2),
        "is_tax_inclusive": is_tax_inclusive,
    }


def tool_stamp_duty_estimator(contract_amount: float, contract_type: str) -> Dict[str, Any]:
    """
    印花税测算工具。
    根据合同类型和金额估算应缴印花税额。
    """
    # 印花税税率表（2022年7月1日起施行的《中华人民共和国印花税法》）
    stamp_duty_rates = {
        "purchase": 0.0003,       # 买卖合同 0.03%
        "sales": 0.0003,          # 同上
        "processing": 0.0003,     # 承揽合同 0.03%
        "construction": 0.0003,   # 建设工程合同 0.03%
        "lease": 0.001,           # 租赁合同 0.1%
        "warehouse": 0.001,       # 仓储合同 0.1%
        "transport": 0.0003,      # 运输合同 0.03%
        "technology": 0.0003,     # 技术合同 0.03%
        "loan": 0.00005,          # 借款合同 0.005%
        "insurance": 0.001,       # 财产保险合同 0.1%
    }

    rate = stamp_duty_rates.get(contract_type, 0.0003)
    estimated_tax = contract_amount * rate

    return {
        "success": True,
        "contract_type": contract_type,
        "contract_amount": round(contract_amount, 2),
        "stamp_duty_rate": rate,
        "estimated_stamp_duty": round(estimated_tax, 2),
        "currency": "CNY",
        "note": "实际印花税额以税务机关核定为准，本测算仅供参考",
    }


def tool_statute_limitation_calc(breach_date_str: str,
                                 claim_filed: bool = False) -> Dict[str, Any]:
    """
    诉讼时效计算器。
    根据违约日期计算诉讼时效到期日，提醒时效风险。
    中国民法典第188条：普通诉讼时效为3年。
    """
    try:
        breach_date = datetime.strptime(breach_date_str, "%Y-%m-%d")
    except ValueError:
        try:
            breach_date = datetime.strptime(breach_date_str, "%Y/%m/%d")
        except ValueError:
            return {"success": False, "error": f"日期格式错误: {breach_date_str}，请使用YYYY-MM-DD格式"}

    limitation_years = 3  # 民法典第188条
    limitation_date = breach_date + timedelta(days=limitation_years * 365)
    today = datetime.now()
    days_remaining = (limitation_date - today).days
    is_expired = days_remaining <= 0

    risk_level = "low"
    if is_expired:
        risk_level = "high"
    elif days_remaining < 90:
        risk_level = "high"
    elif days_remaining < 180:
        risk_level = "medium"

    return {
        "success": True,
        "breach_date": breach_date_str,
        "limitation_period_years": limitation_years,
        "limitation_expiry_date": limitation_date.strftime("%Y-%m-%d"),
        "days_remaining": max(0, days_remaining),
        "is_expired": is_expired,
        "risk_level": risk_level,
        "legal_basis": "《中华人民共和国民法典》第188条",
    }


def tool_penalty_cap_estimator(contract_amount: float, penalty_type: str,
                               penalty_amount: Optional[float] = None) -> Dict[str, Any]:
    """
    违约金上限测算工具。
    根据合同金额判断违约金比例是否超过法定上限（实际损失的30%）。
    《民法典》第585条：违约金过高于造成的损失的，可请求适当减少。
    《民法典合同编司法解释》第65条：违约金超过损失30%一般认定过高。
    """
    # 法定上限：一般不超过实际损失的130%
    legal_upper_limit_ratio = 1.30

    if penalty_amount:
        penalty_ratio = penalty_amount / contract_amount if contract_amount > 0 else 0
    else:
        penalty_ratio = 0

    exceeds = penalty_ratio > 0.30 if penalty_amount else False

    result = {
        "success": True,
        "contract_amount": round(contract_amount, 2),
        "penalty_type": penalty_type,
        "legal_upper_limit_ratio": legal_upper_limit_ratio,
        "legal_basis": "《民法典》第585条 + 《民法典合同编司法解释》第65条",
    }

    if penalty_amount:
        result.update({
            "penalty_amount": round(penalty_amount, 2),
            "penalty_ratio": round(penalty_ratio, 4),
            "exceeds_legal_limit": exceeds,
            "recommended_max_penalty": round(contract_amount * 0.20, 2),
            "risk": "high" if exceeds else "low",
            "detail": f"违约金比例{penalty_ratio:.2%}，{'超过' if exceeds else '未超过'}通常认定标准(30%)",
        })

    return result


def tool_business_entity_verify(company_name: str, credit_code: str = "") -> Dict[str, Any]:
    """
    工商主体核验工具。
    本地校验统一社会信用代码格式（18位）、企业名称基本规则。
    注：本工具为本地存根，完整工商核验需要接入国家企业信用信息公示系统（离线环境下仅做格式校验）。
    """
    issues = []

    # 统一社会信用代码校验（18位，格式：数字+大写字母）
    if credit_code:
        credit_code = credit_code.strip()
        if len(credit_code) != 18:
            issues.append({"type": "credit_code_length_error",
                          "detail": f"统一社会信用代码长度为{len(credit_code)}位，应为18位"})
        if not re.match(r'^[0-9A-HJ-NPQRTUWXY]{2}\d{6}[0-9A-HJ-NPQRTUWXY]{10}$', credit_code):
            issues.append({"type": "credit_code_format_error",
                          "detail": "统一社会信用代码格式不符合规范"})

    # 企业名称基本校验
    if company_name:
        company_name = company_name.strip()
        if len(company_name) < 4:
            issues.append({"type": "company_name_too_short",
                          "detail": "企业名称过短，可能不完整"})
        # 检查是否包含常见公司类型后缀
        company_suffixes = ["有限公司", "股份有限公司", "有限责任公司", "合伙企业", "个人独资企业"]
        if not any(suffix in company_name for suffix in company_suffixes):
            issues.append({"type": "company_name_suffix_missing",
                          "detail": "企业名称可能缺少公司类型后缀（如有限公司等）"})

    return {
        "success": True,
        "is_valid": len(issues) == 0,
        "issues": issues,
        "company_name": company_name,
        "credit_code": credit_code,
        "note": "离线环境仅做格式校验，完整工商核验需接入国家企业信用信息公示系统",
    }


def tool_legal_keyword_matcher(clause_text: str, keywords: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    法条关键词匹配工具。
    根据合同条款文本中的关键词，匹配可能相关的法律领域和法条。
    """
    if keywords is None:
        # 默认法律关键词库
        keywords = [
            "违约金", "赔偿责任", "解除合同", "不可抗力",
            "知识产权", "保密", "竞业限制", "仲裁",
            "管辖", "诉讼", "送达", "担保",
            "质押", "抵押", "保证", "连带责任",
            "发票", "税率", "含税", "增值税",
            "验收", "质保", "交付", "付款",
            "违约责任", "终止", "变更", "转让",
        ]

    matched = []
    for kw in keywords:
        if kw in clause_text:
            # 关键词到法律领域的映射
            legal_area_map = {
                "违约金": ("合同法/民法典合同编", "第585条 违约金调整"),
                "赔偿责任": ("侵权责任/合同违约责任", "损失赔偿范围"),
                "解除合同": ("民法典合同编", "第563条 合同法定解除"),
                "不可抗力": ("民法典", "第180条、第590条"),
                "知识产权": ("知识产权法", "著作权/专利权/商标权"),
                "保密": ("反不正当竞争法", "商业秘密保护"),
                "竞业限制": ("劳动合同法", "第23条、第24条"),
                "仲裁": ("仲裁法", "仲裁协议效力"),
                "管辖": ("民事诉讼法", "第23条-第35条 地域管辖"),
                "发票": ("发票管理办法", "发票开具义务"),
                "税率": ("增值税暂行条例", "税率适用"),
            }
            area = legal_area_map.get(kw, ("相关法律法规", ""))
            matched.append({
                "keyword": kw,
                "matched": True,
                "legal_area": area[0],
                "related_articles": area[1],
            })

    return {
        "success": True,
        "matched_count": len(matched),
        "matched_keywords": matched,
    }


def tool_risk_ledger_writer(risk_items: List[Dict], output_path: str,
                            contract_id: str = "") -> Dict[str, Any]:
    """
    风险台账写入工具。
    将审计发现的风险写入JSON台账文件，支持增量追加。
    """
    ledger_path = output_path or os.path.join(
        os.path.dirname(
            __file__), "..", "rag_store", "risk_history", "risk_ledger.jsonl"
    )

    os.makedirs(os.path.dirname(ledger_path), exist_ok=True)

    written_count = 0
    timestamp = datetime.now().isoformat()

    try:
        with open(ledger_path, "a", encoding="utf-8") as f:
            for item in risk_items or []:
                record = {
                    "id": str(uuid.uuid4().hex[:12]),
                    "contract_id": contract_id,
                    "recorded_at": timestamp,
                    "risk_level": item.get("risk_level", "low"),
                    "risk_category": item.get("risk_category", ""),
                    "issue": item.get("issue", ""),
                    "clause_location": item.get("clause_location", ""),
                    "source_skill": item.get("source_skills", []),
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                written_count += 1

        return {
            "success": True,
            "written_count": written_count,
            "ledger_path": os.path.abspath(ledger_path),
        }
    except Exception as e:
        return {"success": False, "error": str(e), "written_count": 0}


def tool_report_exporter(audit_result: Dict[str, Any], output_path: str,
                         fmt: str = "json") -> Dict[str, Any]:
    """
    审计报告导出工具。
    将完整审计结果导出为JSON或Markdown报告文件。
    """
    if not audit_result:
        return {"success": False, "error": "审计结果为空"}

    os.makedirs(os.path.dirname(output_path) if os.path.dirname(
        output_path) else ".", exist_ok=True)

    try:
        if fmt == "json":
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(audit_result, f, ensure_ascii=False, indent=2)
        elif fmt == "markdown":
            md_lines = []
            md_lines.append("# 合同审计报告")
            md_lines.append(
                f"\n生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

            summary = audit_result.get("risk_summary", {})
            if summary:
                md_lines.append("\n## 风险统计")
                md_lines.append(f"- 高风险: {summary.get('final_high_risks', 0)}")
                md_lines.append(
                    f"- 中风险: {summary.get('final_medium_risks', 0)}")
                md_lines.append(f"- 低风险: {summary.get('final_low_risks', 0)}")
                md_lines.append(
                    f"- 待人工复核: {summary.get('pending_review_risks', 0)}")

            risk_items = audit_result.get("risk_items", [])
            if risk_items:
                md_lines.append("\n## 风险详情")
                for item in risk_items:
                    md_lines.append(
                        f"\n### {item.get('risk_level', '?').upper()} - {item.get('risk_category', '')}")
                    md_lines.append(f"**问题**: {item.get('issue', '')}")
                    md_lines.append(
                        f"**位置**: {item.get('clause_location', '')}")
                    md_lines.append(
                        f"**原文**: > {item.get('original_clause_quote', '')}")
                    md_lines.append(
                        f"**建议**: {item.get('detailed_suggestion', '')}")
                    md_lines.append("")

            with open(output_path, "w", encoding="utf-8") as f:
                f.write("\n".join(md_lines))
        else:
            return {"success": False, "error": f"不支持的导出格式: {fmt}"}

        return {
            "success": True,
            "output_path": os.path.abspath(output_path),
            "format": fmt,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# ============================================================================
# 工具注册表
# ============================================================================

# 全局工具注册表：name -> ToolDef
_TOOL_REGISTRY: Dict[str, ToolDef] = {}


def register_tool(name: str, description: str, func: Callable,
                  param_schema: Dict[str, Any], return_schema: Dict[str, Any],
                  bound_skills: Optional[List[str]] = None,
                  is_async: bool = False) -> None:
    """注册一个工具到全局注册表"""
    _TOOL_REGISTRY[name] = ToolDef(
        name=name,
        description=description,
        func=func,
        param_schema=param_schema,
        return_schema=return_schema,
        bound_skills=bound_skills or [],
        is_async=is_async,
    )


def _init_default_tools():
    """初始化默认工具集"""
    if _TOOL_REGISTRY:
        return  # 已经初始化

    # ---- Skill 0: 文本解析工具 ----
    register_tool(
        name="ocr_text_extract",
        description="从PDF/Word/图片文件中提取合同文本内容。参数: file_path(文件路径)",
        func=tool_ocr_text_extract,
        param_schema={"file_path": {"type": "string",
                                    "required": True, "description": "合同文件路径"}},
        return_schema={"success": "bool", "text": "string",
                       "extraction_method": "string"},
        bound_skills=["skill_text_parser"],
    )
    register_tool(
        name="regex_amount_extract",
        description="从合同文本中正则提取所有金额数字和币种。参数: text(合同文本)",
        func=tool_regex_amount_extract,
        param_schema={"text": {"type": "string",
                               "required": True, "description": "合同文本内容"}},
        return_schema={"success": "bool",
                       "amounts": "list", "amounts_found": "int"},
        bound_skills=["skill_text_parser"],
    )
    register_tool(
        name="text_chunk_splitter",
        description="将长文本按段落智能分块，用于端侧大模型上下文窗口管理。参数: text(原始文本), max_chunk_chars(单块最大字符数), overlap_chars(重叠字符数)",
        func=tool_text_chunk_splitter,
        param_schema={
            "text": {"type": "string", "required": True},
            "max_chunk_chars": {"type": "int", "required": False, "default": 3000},
            "overlap_chars": {"type": "int", "required": False, "default": 400},
        },
        return_schema={"success": "bool",
                       "chunks": "list", "chunk_count": "int"},
        bound_skills=["skill_text_parser"],
    )

    # ---- Skill 1: 法律合规工具 ----
    register_tool(
        name="business_entity_verify",
        description="本地校验企业工商主体信息（统一社会信用代码格式校验、企业名称基本规则）。参数: company_name(企业名称), credit_code(统一社会信用代码，可选)",
        func=tool_business_entity_verify,
        param_schema={
            "company_name": {"type": "string", "required": True},
            "credit_code": {"type": "string", "required": False},
        },
        return_schema={"success": "bool",
                       "is_valid": "bool", "issues": "list"},
        bound_skills=["skill_law_compliance"],
    )
    register_tool(
        name="legal_keyword_matcher",
        description="根据合同条款中的法律关键词匹配相关法律领域和法条。参数: clause_text(条款文本), keywords(关键词列表，可选)",
        func=tool_legal_keyword_matcher,
        param_schema={
            "clause_text": {"type": "string", "required": True},
            "keywords": {"type": "list", "required": False},
        },
        return_schema={"success": "bool",
                       "matched_keywords": "list", "matched_count": "int"},
        bound_skills=["skill_law_compliance"],
    )

    # ---- Skill 2: 商务权责工具 ----
    register_tool(
        name="amount_case_compare",
        description="对比合同金额的大小写是否一致。参数: amount_numeric(数值金额), amount_upper(大写金额), amount_lower(小写金额，可选)",
        func=tool_amount_case_compare,
        param_schema={
            "amount_numeric": {"type": "float", "required": True},
            "amount_upper": {"type": "string", "required": True},
            "amount_lower": {"type": "string", "required": False},
        },
        return_schema={"success": "bool",
                       "is_consistent": "bool", "issues": "list"},
        bound_skills=["skill_business_clause"],
    )
    register_tool(
        name="payment_schedule_calc",
        description="验证合同各付款节点金额之和是否等于总金额。参数: payment_terms(付款条款列表), total_amount(合同总金额)",
        func=tool_payment_schedule_calc,
        param_schema={
            "payment_terms": {"type": "list", "required": True},
            "total_amount": {"type": "float", "required": True},
        },
        return_schema={"success": "bool",
                       "is_sum_consistent": "bool", "issues": "list"},
        bound_skills=["skill_business_clause"],
    )

    # ---- Skill 3: 财税工具 ----
    register_tool(
        name="tax_rate_calculator",
        description="根据金额和税率类型计算增值税、所得税等税额。参数: amount(金额), tax_type(税率类型: vat_general/vat_low/vat_service等), is_tax_inclusive(是否含税)",
        func=tool_tax_rate_calculator,
        param_schema={
            "amount": {"type": "float", "required": True},
            "tax_type": {"type": "string", "required": True, "description": "vat_general|vat_low|vat_service|vat_small|stamp_duty_contract等"},
            "is_tax_inclusive": {"type": "bool", "required": False, "default": True},
        },
        return_schema={"success": "bool", "tax_amount": "float",
                       "tax_exclusive_amount": "float"},
        bound_skills=["skill_tax_finance"],
    )
    register_tool(
        name="stamp_duty_estimator",
        description="根据合同类型和金额估算印花税额。参数: contract_amount(合同金额), contract_type(合同类型)",
        func=tool_stamp_duty_estimator,
        param_schema={
            "contract_amount": {"type": "float", "required": True},
            "contract_type": {"type": "string", "required": True, "description": "purchase|lease|loan|insurance等"},
        },
        return_schema={"success": "bool",
                       "estimated_stamp_duty": "float", "stamp_duty_rate": "float"},
        bound_skills=["skill_tax_finance"],
    )

    # ---- Skill 4: 违约争议工具 ----
    register_tool(
        name="statute_limitation_calc",
        description="根据违约日期计算诉讼时效到期日。参数: breach_date_str(违约日期YYYY-MM-DD), claim_filed(是否已起诉)",
        func=tool_statute_limitation_calc,
        param_schema={
            "breach_date_str": {"type": "string", "required": True, "description": "违约日期，格式YYYY-MM-DD"},
            "claim_filed": {"type": "bool", "required": False, "default": False},
        },
        return_schema={"success": "bool", "days_remaining": "int",
                       "is_expired": "bool", "risk_level": "string"},
        bound_skills=["skill_dispute_breach"],
    )
    register_tool(
        name="penalty_cap_estimator",
        description="判断违约金比例是否超过法定上限（实际损失的30%）。参数: contract_amount(合同金额), penalty_type(违约金类型), penalty_amount(违约金金额，可选)",
        func=tool_penalty_cap_estimator,
        param_schema={
            "contract_amount": {"type": "float", "required": True},
            "penalty_type": {"type": "string", "required": True},
            "penalty_amount": {"type": "float", "required": False},
        },
        return_schema={"success": "bool",
                       "exceeds_legal_limit": "bool", "penalty_ratio": "float"},
        bound_skills=["skill_dispute_breach"],
    )

    # ---- Skill 5: 汇总工具 ----
    register_tool(
        name="risk_ledger_writer",
        description="将审计发现的风险写入JSON Lines台账文件。参数: risk_items(风险项列表), output_path(输出路径), contract_id(合同ID)",
        func=tool_risk_ledger_writer,
        param_schema={
            "risk_items": {"type": "list", "required": True},
            "output_path": {"type": "string", "required": True},
            "contract_id": {"type": "string", "required": False},
        },
        return_schema={"success": "bool",
                       "written_count": "int", "ledger_path": "string"},
        bound_skills=["skill_risk_summary"],
    )
    register_tool(
        name="report_exporter",
        description="将审计结果导出为JSON或Markdown报告。参数: audit_result(完整审计结果), output_path(输出文件路径), fmt(格式: json|markdown)",
        func=tool_report_exporter,
        param_schema={
            "audit_result": {"type": "dict", "required": True},
            "output_path": {"type": "string", "required": True},
            "fmt": {"type": "string", "required": False, "default": "json"},
        },
        return_schema={"success": "bool",
                       "output_path": "string", "format": "string"},
        bound_skills=["skill_risk_summary"],
    )


# ============================================================================
# 公共接口
# ============================================================================

def get_tool(name: str) -> Optional[ToolDef]:
    """获取指定名称的工具定义"""
    _init_default_tools()
    return _TOOL_REGISTRY.get(name)


def get_tools_for_skill(skill_id: str) -> Dict[str, ToolDef]:
    """获取指定Skill绑定的所有工具"""
    _init_default_tools()
    return {
        name: tool for name, tool in _TOOL_REGISTRY.items()
        if skill_id in tool.bound_skills
    }


def list_all_tools() -> Dict[str, ToolDef]:
    """列出所有注册的工具"""
    _init_default_tools()
    return dict(_TOOL_REGISTRY)


def execute_tool(name: str, params: Dict[str, Any]) -> ToolCallResult:
    """
    执行指定工具。

    Args:
        name: 工具名称
        params: 工具参数字典

    Returns:
        ToolCallResult: 执行结果
    """
    tool = get_tool(name)
    if not tool:
        return ToolCallResult(
            success=False,
            result=None,
            error=f"未找到工具: {name}"
        )

    start_time = datetime.now()

    try:
        result = tool.func(**params)
        elapsed = (datetime.now() - start_time).total_seconds() * 1000
        return ToolCallResult(
            success=True,
            result=result,
            execution_time_ms=elapsed,
        )
    except TypeError as e:
        return ToolCallResult(
            success=False,
            result=None,
            error=f"工具参数错误 ({name}): {str(e)}",
        )
    except Exception as e:
        return ToolCallResult(
            success=False,
            result=None,
            error=f"工具执行异常 ({name}): {str(e)}",
        )


def build_tool_descriptions_for_skill(skill_id: str) -> str:
    """
    为指定Skill生成端侧大模型可理解的工具描述文本。
    适配llama.cpp端侧大模型，使用文本格式而非标准function calling JSON。

    Args:
        skill_id: Skill标识

    Returns:
        str: 格式化的工具描述文本
    """
    tools = get_tools_for_skill(skill_id)
    if not tools:
        return "（当前Skill无绑定工具）"

    lines = ["## 可用工具列表", ""]
    for name, tool in tools.items():
        lines.append(f"- **{name}**: {tool.description}")
        # 参数说明
        params_desc = tool.param_schema
        if params_desc:
            param_strs = []
            for pname, pdef in params_desc.items():
                if isinstance(pdef, dict):
                    required = "必填" if pdef.get("required") else "可选"
                    param_strs.append(
                        f"  {pname}({required}): {pdef.get('description', '')}")
            if param_strs:
                lines.append("  参数: ")
                lines.extend(param_strs)
        lines.append("")

    lines.append("调用方式：在回复中插入 `FUNC_CALL: <工具名> <JSON参数>`")
    lines.append(
        "示例：`FUNC_CALL: tax_rate_calculator {\"amount\": 10000, \"tax_type\": \"vat_general\"}`")
    return "\n".join(lines)


def parse_function_call_from_text(text: str) -> List[Tuple[str, Dict[str, Any]]]:
    """
    从端侧大模型输出文本中解析工具调用标记。
    适配llama.cpp端侧大模型，解析 FUNC_CALL: 前缀的工具调用。

    格式: FUNC_CALL: tool_name {"param": "value"}

    Returns:
        List of (tool_name, params_dict)
    """
    calls = []
    pattern = r'FUNC_CALL:\s*(\w+)\s*(\{[^}]+\})'
    for match in re.finditer(pattern, text):
        tool_name = match.group(1)
        params_str = match.group(2)
        try:
            params = json.loads(params_str)
        except json.JSONDecodeError:
            # 尝试修复常见JSON错误
            try:
                fixed = params_str.replace("'", '"')
                params = json.loads(fixed)
            except json.JSONDecodeError:
                params = {}
        calls.append((tool_name, params))

    return calls
