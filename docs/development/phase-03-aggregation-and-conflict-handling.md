# 第3阶段开发记录：汇总去重与冲突处理

## 阶段目标

- 将多轮分段审计结果聚合为统一风险列表。
- 引入可配置的规则引擎，支撑去重、冲突处理与基础复核项输出。
- 为第 4 阶段“可靠性复核与异常重试”提供结构化输入。

## 本阶段已完成内容

- 新增风险规则引擎：
  - `app/services/risk_rule_engine.py`
- 新增聚合器：
  - `app/services/result_aggregator.py`
- 将多轮审计聚合逻辑从 `contract_audit.py` 内部散装合并，升级为：
  - chunk 结果去重
  - 跨条款缺失型风险抑制
  - 法规引用归并
  - review item 生成
  - JSON/CSV 导出载荷
- 将聚合器接入多轮 classic 审计主链路：
  - `app/services/contract_audit.py`
- 补充规则引擎默认配置：
  - `app/config.example.json`

## 规则引擎当前能力

### 1. 风险去重

- 基于以下维度进行重复风险判定：
  - `issue`
  - `suggestion`
  - `law_ref`
  - `clause_id`
- 当前默认策略：
  - 相似度阈值 `0.9`
  - 默认要求同条款去重
  - 重复时优先保留高置信度项

### 2. 冲突处理

- 复用现有 `risk_suppression` 能力，对“缺失型风险”执行跨条款抑制。
- 当前已支持：
  - 发票类型
  - 开票时点
  - 税率
  - 纳税义务
  - 代扣代缴

### 3. 聚合输出

- 聚合输出包含：
  - 统一 `audit` 结果
  - `duplicate_items_removed`
  - `conflict_items_removed`
  - `review_item_count`
  - `removed_conflicts`
  - `duplicate_items`
- 导出载荷当前提供：
  - `json`
  - `csv`

## 当前测试结果

### 执行命令

```bash
python -m pytest tests/test_result_aggregator.py tests/test_audit_token_policy.py tests/test_audit_utils.py tests/test_contract_audit.py tests/test_llm_router.py tests/test_llm_local_mode.py
```

### 结果摘要

- 结果：`37 passed`
- 新增覆盖：
  - 相似风险去重并保留高置信度项
  - 缺失型风险跨条款抑制
  - 聚合导出载荷与 review item 生成

## 阶段当前完成度判断

- 已完成第 3 阶段“最小可用闭环”：
  - 多轮结果能汇总为统一风险列表
  - 已有可配置规则引擎雏形
  - 已有导出载荷雏形
- 尚未完成第 3 阶段的全部高级要求：
  - 行业差异化规则模板
  - Excel 原生导出文件
  - 漏项复核的深度规则化

## 已知限制

- 当前导出以 `JSON/CSV` 为主，尚未生成 `.xlsx` 文件。
- 去重规则仍以通用相似度为主，尚未引入行业模板、风险类型词典和更细粒度阈值矩阵。
- review item 已生成，但“异常重试”尚未启动，这属于第 4 阶段。

## 下一阶段输入

- 第 4 阶段将直接消费：
  - `review_items`
  - `parse_failed_flag`
  - `truncated_flag`
  - 高风险缺法规引用标记
  - 聚合后的冲突项记录
