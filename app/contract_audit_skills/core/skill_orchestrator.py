"""
Skill路由调度核心引擎 (Skill Orchestrator)
===========================================
合同审计Skill体系的核心调度引擎，负责：

1. 加载skill_config.yaml全局配置
2. 接收Skill0输出的结构化合同元数据，自动识别合同类型
3. 根据合同类型自动加载对应Skill组合，串行执行
4. 自动切割超长合同分块送入端侧大模型，规避llama.cpp上下文溢出
5. 高风险项开启二次复核
6. 交叉校验逻辑：同一风险点由2个关联Skill交叉验证
7. 整合端侧大模型（llama.cpp）、RAG检索、工具调用、Skill Prompt模板

完整执行链路：
  合同文件上传 → Skill0文本解析分块 → 路由匹配对应Skill组串行执行
  → 各Skill独立RAG检索+工具调用推理 → Skill5汇总分级交叉校验
  → 输出完整审计报告JSON+Markdown
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple
from dataclasses import dataclass, field

import yaml

from app.contract_audit_skills.tools.registry import (
    get_tools_for_skill,
    execute_tool,
    build_tool_descriptions_for_skill,
    parse_function_call_from_text,
    ToolCallResult,
)

logger = logging.getLogger("law_assistant.skill_orchestrator")


# ============================================================================
# 数据模型
# ============================================================================

@dataclass
class ContractMetadata:
    """Skill 0 输出的结构化合同元数据"""
    contract_type: str = "default"       # purchase/sales/lease/labor/engineering/other
    contract_type_confidence: float = 0.0
    parties: Dict[str, Any] = field(default_factory=dict)
    subject_matter: str = ""
    total_amount: Dict[str, Any] = field(default_factory=dict)
    currency: str = "CNY"
    effective_date: str = ""
    expiry_date: str = ""
    payment_terms: List[Dict] = field(default_factory=list)
    breach_clause_index: Dict[str, str] = field(default_factory=dict)
    chapters: List[Dict] = field(default_factory=list)
    special_clauses: List[Dict] = field(default_factory=list)
    total_pages: int = 0
    signature_date: str = ""


@dataclass
class SkillResult:
    """单个Skill的执行结果"""
    skill_id: str
    skill_name: str
    success: bool
    risks: List[Dict[str, Any]] = field(default_factory=list)
    raw_output: str = ""
    parsed_json: Dict[str, Any] = field(default_factory=dict)
    rag_results: List[Any] = field(default_factory=list)
    tool_calls_made: int = 0
    execution_time_ms: float = 0.0
    token_usage: Dict[str, int] = field(default_factory=dict)
    error: str = ""


@dataclass
class AuditReport:
    """完整审计报告"""
    report_id: str = ""
    contract_info: Dict[str, Any] = field(default_factory=dict)
    skill_results: Dict[str, SkillResult] = field(default_factory=dict)
    risk_summary: Dict[str, int] = field(default_factory=dict)
    risk_items: List[Dict[str, Any]] = field(default_factory=list)
    cross_validation_notes: List[Dict[str, Any]] = field(default_factory=list)
    overall_risk_level: str = "low"
    executive_summary: str = ""
    audit_metadata: Dict[str, Any] = field(default_factory=dict)


# ============================================================================
# Skill Orchestrator 主类
# ============================================================================

class SkillOrchestrator:
    """
    Skill路由调度器。

    核心流程:
    1. 加载YAML配置
    2. 执行Skill0文本解析
    3. 根据合同类型路由到对应Skill组
    4. 串行执行各Skill（含RAG检索+工具调用）
    5. Skill5汇总+交叉校验
    6. 输出完整审计报告
    """

    def __init__(self, config_path: str = "", llm_service=None, embedder=None):
        """
        Args:
            config_path: skill_config.yaml 路径
            llm_service: 现有的LLM服务实例（LLMService），用于端侧大模型推理
            embedder: embedding函数，用于RAG向量检索
        """
        if not config_path:
            config_path = os.path.join(os.path.dirname(__file__), "..", "skill_config.yaml")
        self.config_path = os.path.abspath(config_path)
        self.config = self._load_config()
        self.llm = llm_service
        self.embedder = embedder

        # 延迟导入RAG管理器（避免循环依赖）
        self._rag_manager = None

        # 文本分块配置
        chunk_cfg = self.config.get("system", {}).get("text_chunking", {})
        self.max_chunk_chars = chunk_cfg.get("max_chunk_chars", 3000)
        self.overlap_chars = chunk_cfg.get("overlap_chars", 400)
        self.max_chunks_per_skill = chunk_cfg.get("max_chunks_per_skill", 8)

        # 交叉校验配置
        cross_val_cfg = self.config.get("system", {}).get("cross_validation", {})
        self.cross_validation_enabled = cross_val_cfg.get("enabled", True)
        self.min_agreement_skills = cross_val_cfg.get("min_agreement_skills", 2)
        self.auto_downgrade_single = cross_val_cfg.get("auto_downgrade_single", True)

        # LLM参数默认值
        llm_defaults = self.config.get("system", {}).get("llm_defaults", {})
        self.llm_temperature = llm_defaults.get("temperature", 0.1)
        self.llm_max_tokens = llm_defaults.get("max_tokens", 2048)

        logger.info("SkillOrchestrator 初始化完成, config=%s", self.config_path)

    def _load_config(self) -> Dict[str, Any]:
        """加载YAML配置文件"""
        if not os.path.exists(self.config_path):
            raise FileNotFoundError(f"配置文件不存在: {self.config_path}")
        with open(self.config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def _get_rag_manager(self):
        """延迟初始化RAG管理器"""
        if self._rag_manager is None:
            from app.contract_audit_skills.core.skill_rag import SkillRAGManager
            rag_base = os.path.join(os.path.dirname(self.config_path),
                                    self.config.get("rag", {}).get("base_path", "rag_store"))
            self._rag_manager = SkillRAGManager(
                base_path=rag_base,
                embedder=self.embedder,
                top_k=8,
                similarity_threshold=0.55,
            )
        return self._rag_manager

    def _get_skill_config(self, skill_id: str) -> Optional[Dict[str, Any]]:
        """获取指定Skill的配置"""
        for skill in self.config.get("skills", []):
            if skill["id"] == skill_id:
                return skill
        return None

    def _get_skills_for_contract_type(self, contract_type: str) -> List[str]:
        """根据合同类型获取Skill执行列表"""
        routing = self.config.get("contract_type_routing", {})
        type_config = routing.get(contract_type, routing.get("default", {}))
        return type_config.get("skills", [])

    def _is_high_risk_review_needed(self, contract_type: str) -> bool:
        """检查是否需要高风险二次复核"""
        routing = self.config.get("contract_type_routing", {})
        type_config = routing.get(contract_type, routing.get("default", {}))
        return type_config.get("high_risk_review", True)

    # ========================================================================
    # Step 0: 文本解析
    # ========================================================================

    def execute_skill_0_text_parser(self, contract_text: str) -> ContractMetadata:
        """
        执行 Skill 0: 前置文本解析。

        输入：原始合同文本
        输出：结构化ContractMetadata
        绑定工具：OCR、正则金额提取、文本分块
        """
        logger.info("开始执行 Skill 0: 文本解析, text_len=%d", len(contract_text))

        # 先执行绑定工具：金额提取 + 文本分块
        from app.contract_audit_skills.tools.registry import (
            tool_regex_amount_extract,
            tool_text_chunk_splitter,
        )

        amount_result = tool_regex_amount_extract(contract_text)
        chunk_result = tool_text_chunk_splitter(contract_text,
                                                 self.max_chunk_chars,
                                                 self.overlap_chars)

        # 构建Prompt（使用Skill 0模板）
        skill_cfg = self._get_skill_config("skill_text_parser")
        template_path = os.path.join(os.path.dirname(self.config_path),
                                     skill_cfg["prompt_template"])
        prompt_text = self._render_skill_prompt(
            template_path,
            skill_id="skill_text_parser",
            skill_name="前置文本解析",
            contract_text=contract_text,
            tool_results=json.dumps({
                "amount_extraction": amount_result,
                "chunk_summary": f"共{chunk_result['chunk_count']}个文本块",
            }, ensure_ascii=False, indent=2),
        )

        # 调用端侧大模型
        raw_output, usage = self._call_llm(
            system_prompt="你是专业合同文本结构化解析引擎。只输出JSON。",
            user_prompt=prompt_text,
            max_tokens=2048,
        )

        # 解析JSON输出
        parsed = self._extract_json(raw_output)

        # 构建ContractMetadata
        metadata = ContractMetadata(
            contract_type=parsed.get("contract_type", "default"),
            contract_type_confidence=parsed.get("contract_type_confidence", 0.0),
            parties=parsed.get("parties", {}),
            subject_matter=parsed.get("subject_matter", ""),
            total_amount=parsed.get("total_amount", {}),
            effective_date=parsed.get("effective_date", ""),
            expiry_date=parsed.get("expiry_date", ""),
            payment_terms=parsed.get("payment_terms", []),
            breach_clause_index=parsed.get("breach_clause_index", {}),
            chapters=parsed.get("chapters", []),
            special_clauses=parsed.get("special_clauses", []),
            total_pages=parsed.get("total_pages", 0),
            signature_date=parsed.get("signature_date", ""),
        )

        logger.info("Skill 0 完成: contract_type=%s confidence=%.2f",
                    metadata.contract_type, metadata.contract_type_confidence)
        return metadata

    # ========================================================================
    # Step 1-4: 执行审计Skill
    # ========================================================================

    def execute_skill(self, skill_id: str, contract_text: str,
                      contract_metadata: ContractMetadata) -> SkillResult:
        """
        执行单个审计Skill（Skill 1-4）。

        流程:
        1. 文本分块
        2. 专属RAG检索
        3. 绑定工具调用
        4. 渲染Prompt模板
        5. 端侧大模型推理
        6. 解析JSON输出

        Args:
            skill_id: Skill标识（如 skill_law_compliance）
            contract_text: 合同原始文本
            contract_metadata: Skill0输出的结构化元数据

        Returns:
            SkillResult
        """
        skill_cfg = self._get_skill_config(skill_id)
        if not skill_cfg:
            return SkillResult(skill_id=skill_id, skill_name=skill_id,
                              success=False, error=f"Skill配置未找到: {skill_id}")

        if not skill_cfg.get("enabled", True):
            logger.info("Skill %s 已禁用，跳过", skill_id)
            return SkillResult(skill_id=skill_id, skill_name=skill_cfg["name"],
                              success=True, error="Skill已禁用")

        skill_name = skill_cfg["name"]
        logger.info("开始执行 Skill: %s (%s)", skill_name, skill_id)
        start_time = time.perf_counter()

        result = SkillResult(skill_id=skill_id, skill_name=skill_name, success=False)
        tool_calls_made = 0

        try:
            # ---- 1. 文本分块 ----
            from app.contract_audit_skills.tools.registry import tool_text_chunk_splitter
            chunk_result = tool_text_chunk_splitter(contract_text,
                                                     self.max_chunk_chars,
                                                     self.overlap_chars)
            chunks = chunk_result["chunks"][:self.max_chunks_per_skill]

            # ---- 2. 专属RAG检索 ----
            rag_results = self._run_skill_rag(skill_id, contract_text, contract_metadata)
            result.rag_results = [r for r in rag_results]

            # ---- 3. 绑定工具调用 ----
            tool_results_text = self._run_bound_tools(skill_id, contract_text, contract_metadata)
            if tool_results_text:
                tool_calls_made = tool_results_text.count("FUNC_RESULT:")

            # ---- 4. 渲染Prompt模板 ----
            prompt_text = self._render_skill_prompt(
                template_path=os.path.join(os.path.dirname(self.config_path),
                                          skill_cfg["prompt_template"]),
                skill_id=skill_id,
                skill_name=skill_name,
                contract_metadata=json.dumps({
                    "contract_type": contract_metadata.contract_type,
                    "parties": contract_metadata.parties,
                    "subject_matter": contract_metadata.subject_matter,
                    "total_amount": contract_metadata.total_amount,
                    "effective_date": contract_metadata.effective_date,
                    "expiry_date": contract_metadata.expiry_date,
                    "payment_terms": contract_metadata.payment_terms,
                    "breach_clause_index": contract_metadata.breach_clause_index,
                    "special_clauses": contract_metadata.special_clauses,
                }, ensure_ascii=False, indent=2),
                contract_chunks=chunks,
                rag_results=rag_results,
                tool_results=tool_results_text,
            )

            # ---- 5. 端侧大模型推理 ----
            raw_output, usage = self._call_llm(
                system_prompt=self._build_system_prompt(skill_id),
                user_prompt=prompt_text,
                max_tokens=self.llm_max_tokens,
            )

            # ---- 6. 解析输出 ----
            parsed = self._extract_json(raw_output)

            result.success = True
            result.raw_output = raw_output
            result.parsed_json = parsed
            result.risks = parsed.get("risks", [])
            result.token_usage = usage

        except Exception as e:
            logger.exception("Skill %s 执行异常: %s", skill_id, str(e))
            result.error = str(e)

        result.tool_calls_made = tool_calls_made
        result.execution_time_ms = (time.perf_counter() - start_time) * 1000

        logger.info("Skill %s 完成: risks=%d time=%.0fms",
                    skill_id, len(result.risks), result.execution_time_ms)
        return result

    # ========================================================================
    # Step 5: 汇总与交叉校验
    # ========================================================================

    def execute_skill_5_summary(self, contract_metadata: ContractMetadata,
                                 upstream_results: Dict[str, SkillResult]) -> SkillResult:
        """
        执行 Skill 5: 风险汇总分级。

        Args:
            contract_metadata: 合同元数据
            upstream_results: 上游各Skill的执行结果 {skill_id: SkillResult}

        Returns:
            汇总结果
        """
        skill_id = "skill_risk_summary"
        skill_cfg = self._get_skill_config(skill_id)
        skill_name = skill_cfg["name"]

        logger.info("开始执行 Skill 5: 风险汇总分级")
        start_time = time.perf_counter()

        result = SkillResult(skill_id=skill_id, skill_name=skill_name, success=False)

        try:
            # ---- 1. 交叉校验 ----
            cross_validation = self._cross_validate(upstream_results)

            # ---- 2. 去重合并 ----
            deduped_risks = self._dedup_and_merge_risks(upstream_results, cross_validation)

            # ---- 3. RAG检索历史风险 ----
            rag_results = self._run_skill_rag(
                skill_id,
                json.dumps(deduped_risks, ensure_ascii=False),
                contract_metadata,
            )

            # ---- 4. 渲染汇总Prompt ----
            # 准备上游Skill结果摘要
            upstream_summary = []
            for sid, sr in upstream_results.items():
                scfg = self._get_skill_config(sid)
                upstream_summary.append({
                    "skill_id": sid,
                    "skill_name": scfg["name"] if scfg else sid,
                    "risks": sr.risks if sr.success else [],
                })

            prompt_text = self._render_skill_prompt(
                template_path=os.path.join(os.path.dirname(self.config_path),
                                          skill_cfg["prompt_template"]),
                skill_id=skill_id,
                skill_name=skill_name,
                contract_metadata=json.dumps({
                    "contract_type": contract_metadata.contract_type,
                    "parties": contract_metadata.parties,
                    "total_amount": contract_metadata.total_amount,
                    "effective_date": contract_metadata.effective_date,
                    "expiry_date": contract_metadata.expiry_date,
                }, ensure_ascii=False, indent=2),
                upstream_skill_results=upstream_summary,
                rag_results=rag_results,
                cross_validation_enabled=self.cross_validation_enabled,
                cross_validation_results=cross_validation,
            )

            # ---- 5. LLM推理 ----
            raw_output, usage = self._call_llm(
                system_prompt=self._build_system_prompt(skill_id),
                user_prompt=prompt_text,
                max_tokens=3072,
            )

            # ---- 6. 解析 + 注入预计算数据 ----
            parsed = self._extract_json(raw_output)

            # 强制注入我们预计算的交叉校验结果和去重数据
            if not parsed.get("risk_summary"):
                parsed["risk_summary"] = {}
            parsed["risk_summary"]["total_raw_risks"] = sum(
                len(sr.risks) for sr in upstream_results.values() if sr.success)
            parsed["risk_summary"]["after_dedup_risks"] = len(deduped_risks)

            if not parsed.get("cross_validation_notes"):
                parsed["cross_validation_notes"] = cross_validation

            # 如果LLM没有返回risk_items，使用预计算的去重结果
            if not parsed.get("risk_items") and deduped_risks:
                parsed["risk_items"] = deduped_risks

            result.success = True
            result.raw_output = raw_output
            result.parsed_json = parsed
            result.risks = deduped_risks  # 使用预计算的去重后风险
            result.token_usage = usage

        except Exception as e:
            logger.exception("Skill 5 汇总异常: %s", str(e))
            result.error = str(e)

        result.execution_time_ms = (time.perf_counter() - start_time) * 1000
        return result

    # ========================================================================
    # 主审计入口
    # ========================================================================

    def audit(self, contract_text: str, file_path: str = "",
              progress_cb: Optional[Callable[[str, int, str], None]] = None,
              **kwargs) -> AuditReport:
        """
        完整审计链路主入口。

        执行链路:
        合同文本 → Skill0文本解析分块 → 路由匹配Skill组串行执行
        → 各Skill独立RAG检索+工具调用推理 → Skill5汇总分级交叉校验
        → 输出完整审计报告

        Args:
            contract_text: 合同文本内容
            file_path: 合同文件路径（用于元数据记录）
            progress_cb: 进度回调 (stage, percent, message)
            **kwargs: 可覆盖LLM参数 temperature, max_tokens

        Returns:
            AuditReport: 完整审计报告
        """
        def _report(stage: str, percent: int, message: str = ""):
            if callable(progress_cb):
                try:
                    progress_cb(stage, percent, message)
                except Exception:
                    pass

        audit_start = time.perf_counter()
        report_id = f"RPT-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

        logger.info("开始完整审计链路 report_id=%s text_len=%d", report_id, len(contract_text))

        # 覆盖LLM参数
        if "temperature" in kwargs:
            self.llm_temperature = float(kwargs["temperature"])
        if "max_tokens" in kwargs:
            self.llm_max_tokens = int(kwargs["max_tokens"])

        # ---- Step 0: 文本解析 ----
        _report("parsing", 10, "文本解析中...")
        metadata = self.execute_skill_0_text_parser(contract_text)
        _report("parsing_done", 20, f"合同类型: {metadata.contract_type}")

        # ---- 路由: 确定Skill执行列表 ----
        contract_type = metadata.contract_type
        skill_list = self._get_skills_for_contract_type(contract_type)
        logger.info("合同类型=%s, 执行Skill列表=%s", contract_type, skill_list)

        # ---- 串行执行审计Skill 1-4 ----
        skill_results: Dict[str, SkillResult] = {}
        non_summary_skills = [s for s in skill_list if s != "skill_risk_summary"]
        total_skills = len(non_summary_skills)
        need_high_risk_review = self._is_high_risk_review_needed(contract_type)

        for idx, skill_id in enumerate(non_summary_skills):
            progress_pct = 20 + int((idx / max(total_skills, 1)) * 50)
            _report("auditing", progress_pct, f"审计中: {skill_id}")

            # 执行Skill
            sr = self.execute_skill(skill_id, contract_text, metadata)
            skill_results[skill_id] = sr

            # 高风险二次复核
            if need_high_risk_review and sr.success:
                high_risks = [r for r in sr.risks if r.get("risk_level") == "high"]
                if high_risks:
                    logger.info("检测到%d个高风险项，启动二次复核 skill=%s",
                               len(high_risks), skill_id)
                    _report("reviewing", progress_pct + 2,
                            f"二次复核中: {skill_id} ({len(high_risks)}个高风险)")

                    # 二次复核: 缩小温度，重新推理
                    original_temp = self.llm_temperature
                    self.llm_temperature = max(0.05, original_temp * 0.5)

                    review_sr = self.execute_skill(skill_id, contract_text, metadata)
                    if review_sr.success:
                        # 合并二次复核结果：仅保留二次复核确认的高风险
                        review_high_ids = {
                            r.get("risk_id") for r in review_sr.risks
                            if r.get("risk_level") == "high"
                        }
                        for r in sr.risks:
                            if r.get("risk_level") == "high" and r.get("risk_id") not in review_high_ids:
                                r["risk_level"] = "medium"
                                r["review_note"] = "二次复核降级：模型未再次确认高风险"

                    self.llm_temperature = original_temp

        _report("audit_done", 70, "审计完成，汇总中...")

        # ---- Step 5: 汇总与交叉校验 ----
        summary_result = self.execute_skill_5_summary(metadata, skill_results)
        skill_results["skill_risk_summary"] = summary_result

        # ---- 构建审计报告 ----
        report = self._build_report(report_id, metadata, skill_results,
                                     contract_type, skill_list, audit_start)

        _report("done", 100, "审计报告生成完毕")
        logger.info("完整审计链路完成 report_id=%s time=%.0fms",
                    report_id, report.audit_metadata.get("total_time_ms", 0))

        return report

    # ========================================================================
    # 内部辅助方法
    # ========================================================================

    def _run_skill_rag(self, skill_id: str, contract_text: str,
                        contract_metadata: ContractMetadata) -> List[Dict[str, Any]]:
        """
        执行指定Skill的专属RAG检索。

        Returns:
            List of RAG检索结果（字典格式，用于注入Prompt上下文）
        """
        skill_cfg = self._get_skill_config(skill_id)
        if not skill_cfg or not skill_cfg.get("rag_store"):
            return []

        # 构建查询文本：合同关键信息摘要
        query_parts = [
            f"合同类型: {contract_metadata.contract_type}",
            f"标的: {contract_metadata.subject_matter}",
        ]

        # Skill特定关键词增强查询
        skill_keywords = {
            "skill_law_compliance": "法律法规 合规 无效条款 资质 知识产权 保密 竞业",
            "skill_business_clause": "付款节点 验收标准 保证金 调价 履约边界",
            "skill_tax_finance": "发票 税率 印花税 含税 抵扣 增值税",
            "skill_dispute_breach": "违约金 仲裁 管辖 诉讼 解除权 损失追偿",
            "skill_risk_summary": "合同风险 重大违约",
        }

        query_parts.append(skill_keywords.get(skill_id, ""))

        # 添加合同的前2000字符作为背景
        if len(contract_text) > 2000:
            query_parts.append(contract_text[:2000])
        else:
            query_parts.append(contract_text)

        query_text = "\n".join(query_parts)

        try:
            rag = self._get_rag_manager()
            rag_cfg = skill_cfg.get("rag_config", {})
            top_k = rag_cfg.get("top_k", 8)
            rag.top_k = top_k
            rag.similarity_threshold = rag_cfg.get("similarity_threshold", 0.55)

            result = rag.search(skill_id, query_text, top_k=top_k)

            return [
                {
                    "document_name": r.document_name,
                    "paragraph": r.paragraph,
                    "content": r.content,
                    "source": r.source,
                    "doc_number": r.doc_number,
                    "doc_type": r.doc_type,
                    "relevance_score": r.relevance_score,
                }
                for r in result.results
            ]
        except Exception as e:
            logger.warning("RAG检索失败 skill=%s: %s", skill_id, str(e))
            return []

    def _run_bound_tools(self, skill_id: str, contract_text: str,
                          contract_metadata: ContractMetadata) -> str:
        """
        执行为指定Skill绑定的工具。

        Returns:
            格式化的工具调用结果文本
        """
        tools = get_tools_for_skill(skill_id)
        if not tools:
            return ""

        results_parts = []
        for tool_name, tool_def in tools.items():
            try:
                if tool_name == "regex_amount_extract":
                    result = execute_tool(tool_name, {"text": contract_text})
                elif tool_name == "text_chunk_splitter":
                    result = execute_tool(tool_name, {
                        "text": contract_text,
                        "max_chunk_chars": self.max_chunk_chars,
                        "overlap_chars": self.overlap_chars,
                    })
                elif tool_name == "business_entity_verify":
                    party_a = contract_metadata.parties.get("party_a", {})
                    party_b = contract_metadata.parties.get("party_b", {})
                    results_parts.append("## 工商主体核验结果")
                    results_parts.append(f"### 甲方: {party_a.get('name', 'N/A')}")
                    ra = execute_tool(tool_name, {
                        "company_name": party_a.get("name", ""),
                        "credit_code": party_a.get("unified_social_credit_code", ""),
                    })
                    results_parts.append(json.dumps(ra.result, ensure_ascii=False, indent=2))
                    results_parts.append(f"### 乙方: {party_b.get('name', 'N/A')}")
                    rb = execute_tool(tool_name, {
                        "company_name": party_b.get("name", ""),
                        "credit_code": party_b.get("unified_social_credit_code", ""),
                    })
                    results_parts.append(json.dumps(rb.result, ensure_ascii=False, indent=2))
                    continue
                elif tool_name == "payment_schedule_calc":
                    total_val = contract_metadata.total_amount.get("value", 0) if isinstance(contract_metadata.total_amount, dict) else 0
                    result = execute_tool(tool_name, {
                        "payment_terms": contract_metadata.payment_terms,
                        "total_amount": float(total_val) if total_val else 0,
                    })
                elif tool_name == "stamp_duty_estimator":
                    total_val = contract_metadata.total_amount.get("value", 0) if isinstance(contract_metadata.total_amount, dict) else 0
                    result = execute_tool(tool_name, {
                        "contract_amount": float(total_val) if total_val else 0,
                        "contract_type": contract_metadata.contract_type,
                    })
                elif tool_name == "legal_keyword_matcher":
                    # 仅提取违约条款相关文本匹配
                    breach_text = contract_text
                    result = execute_tool(tool_name, {"clause_text": breach_text[:3000]})
                else:
                    continue  # 跳过不需要自动执行的工具

                if result.success:
                    results_parts.append(f"\n### {tool_name}\n```json\n{json.dumps(result.result, ensure_ascii=False, indent=2)}\n```")

            except Exception as e:
                logger.warning("工具调用失败 %s: %s", tool_name, str(e))

        return "\n".join(results_parts)

    def _render_skill_prompt(self, template_path: str, **kwargs) -> str:
        """
        渲染Jinja2 Skill Prompt模板。

        如果Jinja2不可用，使用简单的字符串替换。
        """
        if not os.path.exists(template_path):
            # 返回基本格式的提示文本
            return self._fallback_prompt(**kwargs)

        try:
            from jinja2 import Environment, FileSystemLoader
            env = Environment(loader=FileSystemLoader(os.path.dirname(template_path)))
            template = env.get_template(os.path.basename(template_path))
            return template.render(**kwargs)
        except Exception as e:
            logger.warning("Jinja2渲染失败，使用回退: %s", str(e))
            return self._fallback_prompt(**kwargs)

    def _fallback_prompt(self, **kwargs) -> str:
        """当Jinja2渲染失败时的回退Prompt构建"""
        skill_name = kwargs.get("skill_name", "Unknown")
        contract_metadata = kwargs.get("contract_metadata", "{}")
        chunks = kwargs.get("contract_chunks", [])
        rag_results = kwargs.get("rag_results", [])
        tool_results = kwargs.get("tool_results", "")

        lines = [
            f"## 当前执行Skill: {skill_name}",
            "",
            "你运行在本地llama.cpp端侧大模型。只输出JSON，禁止额外解释。",
            "",
            "### 合同结构化数据",
            str(contract_metadata),
            "",
            "### 合同条款",
        ]
        for i, chunk in enumerate(chunks):
            lines.append(f"--- 条款块 {i+1} ---")
            lines.append(str(chunk)[:2000])

        if rag_results:
            lines.append("\n### RAG检索结果")
            for r in rag_results:
                if isinstance(r, dict):
                    lines.append(f"- {r.get('document_name', '')}: {str(r.get('content', ''))[:500]}")

        if tool_results:
            lines.append(f"\n### 工具调用结果\n{str(tool_results)[:2000]}")

        lines.append("\n## 输出要求：严格JSON格式，禁止任何额外文字。")
        return "\n".join(lines)

    def _build_system_prompt(self, skill_id: str) -> str:
        """构建端侧大模型System Prompt（含幻觉抑制约束）"""
        guard = self.config.get("hallucination_guard", {})
        constraints = guard.get("mandatory_constraints", [])

        skill_cfg = self._get_skill_config(skill_id)
        skill_name = skill_cfg["name"] if skill_cfg else skill_id

        lines = [
            f"你是资深{skill_name}专家。",
            "",
            "## 硬性约束",
        ]
        for i, c in enumerate(constraints, 1):
            lines.append(f"{i}. {c}")

        return "\n".join(lines)

    def _call_llm(self, system_prompt: str, user_prompt: str,
                   max_tokens: int = 2048) -> Tuple[str, Dict[str, int]]:
        """
        调用端侧大模型（通过现有LLMService）。

        Args:
            system_prompt: 系统提示
            user_prompt: 用户提示
            max_tokens: 最大token数

        Returns:
            (content, usage_dict)
        """
        if self.llm is None:
            raise RuntimeError("LLM服务未初始化")

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        try:
            content, parsed = self.llm.chat(messages, overrides={
                "temperature": self.llm_temperature,
                "max_tokens": max_tokens,
                "enable_thinking": False,
                "_task_profile": "contract_audit_skill",
            })
            usage = parsed.get("usage", {})
            return content.strip(), {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            }
        except Exception as e:
            logger.error("LLM调用失败: %s", str(e))
            raise

    def _extract_json(self, text: str) -> Dict[str, Any]:
        """从端侧大模型输出中提取JSON对象"""
        if not text or not text.strip():
            return {}

        # 尝试直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 去除可能的markdown代码块标记
        cleaned = re.sub(r'^\s*```(?:json)?\s*', '', text, flags=re.IGNORECASE)
        cleaned = re.sub(r'\s*```\s*$', '', cleaned)

        # 提取第一个完整的JSON对象
        depth = 0
        start = cleaned.find('{')
        if start < 0:
            return {}

        for i in range(start, len(cleaned)):
            if cleaned[i] == '{':
                depth += 1
            elif cleaned[i] == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(cleaned[start:i + 1])
                    except json.JSONDecodeError:
                        pass
                    break

        return {}

    # ========================================================================
    # 交叉校验逻辑
    # ========================================================================

    def _cross_validate(self, skill_results: Dict[str, SkillResult]) -> List[Dict[str, Any]]:
        """
        交叉校验：同一风险点由2个关联Skill交叉验证。

        规则：
        - 2个及以上独立Skill同时标记同一风险点 → 正式高风险
        - 单一Skill发现的风险 → 自动降级为人工复核（pending）

        Returns:
            交叉校验结果列表
        """
        if not self.cross_validation_enabled:
            return []

        validations = []

        # 获取交叉校验配对配置
        all_skills = self.config.get("skills", [])
        cross_pairs = {}
        for skill in all_skills:
            cross_with = skill.get("cross_validate_with", [])
            if cross_with:
                cross_pairs[skill["id"]] = cross_with

        # 对于每对交叉校验Skill，对比其风险输出
        for skill_a, partners in cross_pairs.items():
            sr_a = skill_results.get(skill_a)
            if not sr_a or not sr_a.success:
                continue

            for skill_b in partners:
                sr_b = skill_results.get(skill_b)
                if not sr_b or not sr_b.success:
                    continue

                # 比较风险：基于风险类别和位置进行匹配
                for risk_a in sr_a.risks:
                    for risk_b in sr_b.risks:
                        similarity = self._risk_similarity(risk_a, risk_b)
                        if similarity > 0.5:
                            # 交叉确认
                            agreed_level = self._resolve_risk_level(
                                risk_a.get("risk_level", "low"),
                                risk_b.get("risk_level", "low"),
                            )
                            validations.append({
                                "risk_pattern": risk_a.get("issue", risk_b.get("issue", "")),
                                "agreement_count": 2,
                                "agreement_skills": [skill_a, skill_b],
                                "skill_a_level": risk_a.get("risk_level"),
                                "skill_b_level": risk_b.get("risk_level"),
                                "final_level": agreed_level,
                                "similarity": round(similarity, 3),
                            })

        # 去重校验结果
        seen = set()
        unique_validations = []
        for v in validations:
            key = f"{sorted(v['agreement_skills'])}|{v['risk_pattern'][:50]}"
            if key not in seen:
                seen.add(key)
                unique_validations.append(v)

        return unique_validations

    def _risk_similarity(self, risk_a: Dict, risk_b: Dict) -> float:
        """计算两个风险的相似度（简单基于位置+类别匹配）"""
        score = 0.0

        # 同一类别 +0.4
        if risk_a.get("risk_category") == risk_b.get("risk_category"):
            score += 0.4

        # 同一位置 +0.3
        loc_a = risk_a.get("clause_location", "")
        loc_b = risk_b.get("clause_location", "")
        if loc_a and loc_b and loc_a == loc_b:
            score += 0.3

        # 问题描述相似（简单Jaccard） +0.3
        issue_a = set(risk_a.get("issue", "").replace("，", ",").split(","))
        issue_b = set(risk_b.get("issue", "").replace("，", ",").split(","))
        if issue_a and issue_b:
            intersection = len(issue_a & issue_b)
            union = len(issue_a | issue_b)
            if union > 0:
                score += 0.3 * (intersection / union)

        return min(1.0, score)

    def _resolve_risk_level(self, level_a: str, level_b: str) -> str:
        """根据两个Skill的风险等级确定最终等级"""
        levels = {"high": 3, "medium": 2, "low": 1, "pending": 0}
        a_val = levels.get(level_a, 1)
        b_val = levels.get(level_b, 1)
        max_val = max(a_val, b_val)
        for k, v in levels.items():
            if v == max_val:
                return k
        return "medium"

    def _dedup_and_merge_risks(self, skill_results: Dict[str, SkillResult],
                                cross_validations: List[Dict]) -> List[Dict[str, Any]]:
        """
        去重合并所有上游Skill的风险输出。

        规则：
        1. 同一位置+同一类别的风险合并为一条
        2. 经交叉验证确认的保留原始等级
        3. 单一Skill发现的风险降级为pending（人工复核）
        """
        # 收集所有风险
        all_risks: List[Dict[str, Any]] = []
        for skill_id, sr in skill_results.items():
            if skill_id == "skill_risk_summary" or not sr.success:
                continue
            for risk in sr.risks:
                risk["source_skill"] = skill_id
                all_risks.append(risk)

        if not all_risks:
            return []

        # 构建交叉验证索引
        cross_confirmed_locations = set()
        for cv in cross_validations:
            # 标记交叉验证确认的风险模式
            cross_confirmed_locations.add(cv["risk_pattern"][:50])

        # 按位置+类别去重
        merged: Dict[str, Dict[str, Any]] = {}
        for risk in all_risks:
            key = f"{risk.get('clause_location', '')}|{risk.get('risk_category', '')}"
            if key in merged:
                # 合并：取最高等级
                existing = merged[key]
                levels = {"high": 3, "medium": 2, "low": 1, "pending": 0}
                if levels.get(risk.get("risk_level", "low"), 1) > levels.get(existing.get("risk_level", "low"), 1):
                    existing["risk_level"] = risk["risk_level"]
                # 合并来源Skill
                if risk.get("source_skill") not in existing.get("source_skills", []):
                    existing.setdefault("source_skills", []).append(risk["source_skill"])
            else:
                risk_copy = dict(risk)
                risk_copy["source_skills"] = [risk.get("source_skill", "")]
                risk_copy.pop("source_skill", None)

                # 检查是否经交叉验证确认
                is_crossed = any(
                    risk_copy.get("issue", "")[:50] in pattern
                    for pattern in cross_confirmed_locations
                )
                risk_copy["cross_validated"] = is_crossed

                # 单一Skill标记者降级
                if self.auto_downgrade_single and len(risk_copy.get("source_skills", [])) < 2:
                    original_level = risk_copy.get("risk_level", "low")
                    if original_level in ("high", "medium"):
                        risk_copy["risk_level"] = "pending"
                        risk_copy["downgrade_reason"] = "单一Skill发现，待人工复核"

                merged[key] = risk_copy

        # 按严重程度排序
        level_order = {"high": 0, "medium": 1, "low": 2, "pending": 3}
        sorted_risks = sorted(merged.values(),
                              key=lambda r: level_order.get(r.get("risk_level", "low"), 2))

        # 重新编号
        for i, risk in enumerate(sorted_risks):
            risk["final_risk_id"] = f"FINAL-{i+1:03d}"

        return sorted_risks

    def _build_report(self, report_id: str, metadata: ContractMetadata,
                       skill_results: Dict[str, SkillResult],
                       contract_type: str, skill_list: List[str],
                       audit_start: float) -> AuditReport:
        """从各Skill结果构建最终审计报告"""
        total_time_ms = (time.perf_counter() - audit_start) * 1000

        # 从Skill5汇总结果提取数据
        summary_sr = skill_results.get("skill_risk_summary")
        summary_parsed = summary_sr.parsed_json if summary_sr else {}

        # 统计风险
        risk_summary = {"final_high_risks": 0, "final_medium_risks": 0,
                        "final_low_risks": 0, "pending_review_risks": 0}
        all_risks = []
        if summary_sr and summary_sr.success:
            all_risks = summary_sr.risks
        else:
            # 如果汇总失败，直接使用上游结果
            for sr in skill_results.values():
                if sr.success and sr.skill_id != "skill_risk_summary":
                    all_risks.extend(sr.risks)

        for risk in all_risks:
            level = risk.get("risk_level", "low")
            if level in risk_summary:
                risk_summary[level] = risk_summary.get(level, 0) + 1

        # 综合风险等级
        if risk_summary.get("high", 0) > 0:
            overall_level = "high"
        elif risk_summary.get("medium", 0) > 2:
            overall_level = "medium"
        else:
            overall_level = "low"

        total_amount = metadata.total_amount.get("value", 0) if isinstance(metadata.total_amount, dict) else 0

        return AuditReport(
            report_id=report_id,
            contract_info={
                "contract_type": contract_type,
                "parties": metadata.parties,
                "total_amount": total_amount,
                "effective_date": metadata.effective_date,
                "expiry_date": metadata.expiry_date,
            },
            skill_results=skill_results,
            risk_summary=risk_summary,
            risk_items=all_risks,
            cross_validation_notes=summary_parsed.get("cross_validation_notes", []),
            overall_risk_level=overall_level,
            executive_summary=summary_parsed.get("executive_summary",
                                                  summary_parsed.get("summary", "")),
            audit_metadata={
                "report_id": report_id,
                "skills_executed": skill_list,
                "total_time_ms": total_time_ms,
                "total_token_usage": sum(
                    sr.token_usage.get("total_tokens", 0)
                    for sr in skill_results.values()
                ),
                "generated_at": datetime.now().isoformat(),
                "contract_type": contract_type,
                "cross_validation_enabled": self.cross_validation_enabled,
            },
        )

    def export_report(self, report: AuditReport, output_path: str,
                       fmt: str = "json") -> str:
        """
        导出审计报告到文件。

        Args:
            report: 审计报告
            output_path: 输出路径
            fmt: 格式 (json|markdown)

        Returns:
            输出文件路径
        """
        os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)

        if fmt == "json":
            report_dict = {
                "report_id": report.report_id,
                "contract_info": report.contract_info,
                "risk_summary": report.risk_summary,
                "overall_risk_level": report.overall_risk_level,
                "executive_summary": report.executive_summary,
                "risk_items": report.risk_items,
                "cross_validation_notes": report.cross_validation_notes,
                "audit_metadata": report.audit_metadata,
                "skill_details": {
                    sid: {
                        "name": sr.skill_name,
                        "success": sr.success,
                        "risks_count": len(sr.risks),
                        "execution_time_ms": sr.execution_time_ms,
                        "token_usage": sr.token_usage,
                    }
                    for sid, sr in report.skill_results.items()
                },
            }
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(report_dict, f, ensure_ascii=False, indent=2)

        elif fmt == "markdown":
            self._export_markdown(report, output_path)

        else:
            raise ValueError(f"不支持的导出格式: {fmt}")

        return os.path.abspath(output_path)

    def _export_markdown(self, report: AuditReport, output_path: str) -> None:
        """导出Markdown格式审计报告"""
        lines = [
            f"# 合同审计报告",
            f"**报告编号**: {report.report_id}",
            f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "## 合同基本信息",
            f"- 合同类型: {report.contract_info.get('contract_type', 'N/A')}",
            f"- 合同金额: {report.contract_info.get('total_amount', 'N/A')}",
            f"- 生效日期: {report.contract_info.get('effective_date', 'N/A')}",
            f"- 到期日期: {report.contract_info.get('expiry_date', 'N/A')}",
            "",
            "## 综合风险等级",
            f"**{report.overall_risk_level.upper()}**",
            "",
            "## 风险统计",
            f"| 等级 | 数量 |",
            f"|------|------|",
        ]
        for level in ["high", "medium", "low", "pending"]:
            lines.append(f"| {level} | {report.risk_summary.get(f'final_{level}_risks', report.risk_summary.get(level, 0))} |")

        lines.append("")
        if report.executive_summary:
            lines.append(f"## 审计摘要\n{report.executive_summary}\n")

        lines.append("## 风险详情")
        for item in report.risk_items:
            level_emoji = {"high": "!!!", "medium": "!!", "low": "!", "pending": "?"}.get(
                item.get("risk_level", "low"), "!")
            lines.append(f"\n### [{level_emoji} {item.get('risk_level', '').upper()}] {item.get('risk_category', '')}")
            lines.append(f"**问题**: {item.get('issue', '')}")
            lines.append(f"**位置**: {item.get('clause_location', '')}")
            lines.append(f"**原文**: > {item.get('original_clause_quote', '')}")
            lines.append(f"**建议**: {item.get('amendment_suggestion', item.get('detailed_suggestion', ''))}")
            if item.get("source_skills"):
                lines.append(f"**来源Skill**: {', '.join(item['source_skills'])}")
            if item.get("cross_validated"):
                lines.append(f"**交叉验证**: 已确认 ✓")
            lines.append("")

        lines.append("---")
        lines.append(f"*报告由合同审计Skill体系自动生成 | 底层引擎: llama.cpp*")

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))


# ============================================================================
# 便捷入口函数
# ============================================================================

def audit_contract_file(file_path: str, llm_service,
                         config_path: str = "",
                         embedder=None,
                         output_dir: str = "",
                         progress_cb=None,
                         **kwargs) -> Dict[str, Any]:
    """
    便捷入口：审计合同文件。

    完整链路：
    1. 提取合同文本
    2. Skill0 文本解析
    3. Skill 1-4 串行审计
    4. Skill5 汇总
    5. 导出报告

    Args:
        file_path: 合同文件路径
        llm_service: 现有LLMService实例
        config_path: skill_config.yaml路径
        embedder: embedding函数
        output_dir: 报告输出目录
        progress_cb: 进度回调
        **kwargs: 额外LLM参数

    Returns:
        审计报告完整字典
    """
    # 提取文本
    from app.contract_audit_skills.tools.registry import tool_ocr_text_extract
    extract_result = tool_ocr_text_extract(file_path)
    if not extract_result["success"]:
        raise RuntimeError(f"文本提取失败: {extract_result.get('error', '未知错误')}")
    contract_text = extract_result["text"]

    # 创建Orchestrator
    orchestrator = SkillOrchestrator(
        config_path=config_path,
        llm_service=llm_service,
        embedder=embedder,
    )

    # 执行审计
    report = orchestrator.audit(contract_text, file_path, progress_cb, **kwargs)

    # 导出报告
    if not output_dir:
        output_dir = os.path.join(os.path.dirname(__file__), "..", "output")

    os.makedirs(output_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(file_path))[0]
    json_path = os.path.join(output_dir, f"{base_name}_audit_report.json")
    md_path = os.path.join(output_dir, f"{base_name}_audit_report.md")

    orchestrator.export_report(report, json_path, fmt="json")
    orchestrator.export_report(report, md_path, fmt="markdown")

    return {
        "report_id": report.report_id,
        "contract_type": report.contract_info.get("contract_type"),
        "overall_risk_level": report.overall_risk_level,
        "risk_summary": report.risk_summary,
        "report_json": json_path,
        "report_md": md_path,
    }
