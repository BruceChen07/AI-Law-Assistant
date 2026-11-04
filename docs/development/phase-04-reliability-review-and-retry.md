# 第4阶段开发记录：可靠性复核与异常重试

## 阶段目标

- 构建“只重跑异常 chunk”的可靠性增强闭环。
- 将第 3 阶段聚合产生的 `review_items` 转换为可执行的复核计划。
- 为后续第 5 阶段最终报告生成提供可靠性等级与复核日志。

## 本阶段已完成内容

- 新增可靠性策略与复核计划模块：
  - `app/services/review_retry.py`
- 为多轮 classic 审计接入：
  - 复核计划生成
  - 异常 chunk 映射
  - 定向重试
  - 重试后重新聚合
  - 可靠性等级输出
  - `app/services/contract_audit.py`
- 新增可靠性配置样板：
  - `app/config.example.json`
- 新增测试：
  - `tests/test_review_retry.py`

## 当前落地能力

### 1. 复核计划

- 复核计划基于以下输入生成：
  - `legal_validation.issues`
  - `removed_conflicts`
  - `chunk_audit_results`
- 当前可识别并映射到 chunk 的复核原因：
  - `parse_failed`
  - `truncated`
  - `high_risk_missing_citation`
  - `conflict_detected`

### 2. 精准重试

- 当前只增强 `multi-pass classic` 链路，不影响 memory 审计主路径。
- 仅对被复核计划命中的 chunk 进行重跑。
- 默认限制：
  - 单 chunk 最多重试 `1` 次
  - 单次审计最多重试 `6` 个 chunk

### 3. 复核日志

- 当前复核日志保存到：
  - `raw.review_plan`
  - `raw.review_retry_logs`
- 同时写入审计 trace：
  - `audit_review_plan`
  - `audit_review_retry_done`
- 每条重试日志记录：
  - `chunk_id`
  - `retry_index`
  - `retry_reasons`
  - `before_parse_failed / after_parse_failed`
  - `before_truncated / after_truncated`
  - `before_risk_count / after_risk_count`

### 4. 可靠性等级

- 当前输出字段：
  - `review_item_count`
  - `review_retry_count`
  - `unresolved_review_item_count`
  - `reliability_level`
- 当前等级规则：
  - 无 review item：`high`
  - 有 review item 且经重试清空：`medium`
  - 重试后仍有 unresolved issue：`low`

## 当前测试结果

### 执行命令

```bash
python -m pytest tests/test_review_retry.py tests/test_result_aggregator.py tests/test_audit_token_policy.py tests/test_audit_utils.py tests/test_contract_audit.py tests/test_llm_router.py tests/test_llm_local_mode.py
```

### 结果摘要

- 结果：`40 passed`
- 新增覆盖：
  - review item 与冲突项映射到 chunk
  - 可靠性等级计算
  - 仅重跑异常 chunk，不重跑正常 chunk

## 当前完成度判断

- 已完成第 4 阶段“最小可运行闭环”：
  - review item -> 复核计划
  - 只重跑异常 chunk
  - 重试前后日志留存
  - 可靠性等级输出
- 尚未完成第 4 阶段全部增强项：
  - 更细粒度的重试提示词优化
  - memory 审计路径的同类复核
  - 更丰富的可靠性评分模型
  - 复核日志持久化到独立数据库表

## 已知限制

- 当前 reliability 等级规则仍是保守版本，偏简单。
- 当前复核只覆盖 `multi-pass classic`，未覆盖 memory 模式。
- 当前 trace 存在 `datetime.utcnow()` 弃用告警，后续应统一切换到 timezone-aware 时间。

## 下一阶段输入

- 第 5 阶段可直接消费：
  - `audit`
  - `meta.reliability_level`
  - `raw.review_plan`
  - `raw.review_retry_logs`
  - `raw.aggregated_exports`
