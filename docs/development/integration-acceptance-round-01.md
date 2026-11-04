# 本轮集成验收说明

## 验收范围

- 第 1 阶段：token 预算治理与准入熔断
- 第 2 阶段：分段审计核心模块
- 第 3 阶段：汇总去重与冲突处理
- 第 4 阶段：可靠性复核与异常重试
- 第 5 阶段：最终报告生成与全流程联调交接

## 本轮目标

- 验证五阶段主链路已具备“最小可运行闭环”。
- 验证多轮分段审计场景下：
  - token 超预算可切多轮
  - chunk 结果可聚合
  - 异常 chunk 可定向重试
  - 最终报告可由结构化中间结果生成
- 验证编排入口与导出入口已能消费统一报告结构。

## 涉及核心模块

- `app/core/token_utils.py`
- `app/services/audit_token_policy.py`
- `app/services/contract_audit.py`
- `app/services/risk_rule_engine.py`
- `app/services/result_aggregator.py`
- `app/services/review_retry.py`
- `app/services/final_report_service.py`
- `app/services/audit_orchestrator.py`
- `app/api/routers/contracts.py`
- `app/core/llm.py`
- `app/core/llm_router.py`

## 验收执行

### 测试命令

```bash
python -m pytest tests/test_final_report_service.py tests/test_review_retry.py tests/test_result_aggregator.py tests/test_audit_token_policy.py tests/test_audit_utils.py tests/test_contract_audit.py tests/test_llm_router.py tests/test_llm_local_mode.py
```

### 测试结果

- 结果：`42 passed`
- 结论：本轮新增的 5 阶段主链路改动未破坏已覆盖的本地路由与合同审计关键逻辑。

## 关键验收结论

### 1. 第 1 阶段

- 已支持统一 token 估算与预算策略。
- 已支持超预算自动切换到多轮模式，不再直接硬截断送模。
- 已支持将预算触发原因写入 trace 和元数据。

### 2. 第 2 阶段

- 已支持基于条款分组的多轮 chunk 审计。
- 每个 chunk 已具备统一输出结构：
  - `chunk_id`
  - `clause_range`
  - `risk_items`
  - `evidence_links`
  - `confidence_score`
  - `parse_failed_flag`
  - `truncated_flag`

### 3. 第 3 阶段

- 已支持相似风险去重。
- 已支持跨条款缺失型风险抑制。
- 已支持基础法规引用归并。
- 已支持聚合导出载荷：
  - `json`
  - `csv`

### 4. 第 4 阶段

- 已支持复核计划生成。
- 已支持仅对异常 chunk 定向重试。
- 已支持复核 trace 与重试日志输出。
- 已支持可靠性等级输出：
  - `high`
  - `medium`
  - `low`

### 5. 第 5 阶段

- 已支持基于结构化中间结果生成统一 `final_report`。
- 已支持编排入口透出：
  - `final_report`
  - `pipeline_summary`
  - `reliability_summary`
- 已支持导出接口优先复用 `final_report`，并兼容旧数据回退。

## 产物清单

- 阶段文档：
  - `docs/development/phase-01-token-budget-and-admission.md`
  - `docs/development/phase-02-chunk-audit-core.md`
  - `docs/development/phase-03-aggregation-and-conflict-handling.md`
  - `docs/development/phase-04-reliability-review-and-retry.md`
  - `docs/development/phase-05-final-report-and-e2e-handoff.md`
- 本轮集成验收文档：
  - `docs/development/integration-acceptance-round-01.md`

## 当前完成度评估

- 第 1 阶段：`90%`
- 第 2 阶段：`75%`
- 第 3 阶段：`70%`
- 第 4 阶段：`70%`
- 第 5 阶段：`60%`

## 已知未完成项

- 尚未完成模型原生 tokenizer 精确对齐。
- 尚未完成 PDF/Word/TXT 结构解析增强和超长条款二次修正。
- 尚未完成行业模板、`.xlsx` 原生导出和深度漏项复核。
- 尚未完成 memory 路径下的同类复核与重试。
- 尚未完成全量压测、UAT、灰度发布、回滚预案与监控告警文档。

## 已知风险

- `trace` 与部分 cleanup 逻辑仍存在 `datetime.utcnow()` 弃用告警。
- `docx` 导出当前仍主要沿用既有渲染器，对新增 `pipeline_summary / reliability_summary` 字段的展示还不完整。
- 超 10 万字合同场景尚未完成专项压测。

## 验收结论

- 本轮改动已满足“先形成五阶段最小可运行闭环，再进入增强与集成验收”的目标。
- 当前代码状态适合：
  - 进入一轮提交整理
  - 进入下一轮增强开发
  - 或进入专项压测与 UAT 准备
