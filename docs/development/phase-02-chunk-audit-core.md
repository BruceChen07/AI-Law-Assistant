# 第2阶段开发记录：分段审计核心模块

## 阶段目标

- 将第 1 阶段的多轮审计骨架升级为“可联调的分段审计结果对象”。
- 为后续第 3 阶段汇总去重、第 4 阶段可靠性复核提供统一 chunk 数据结构。

## 当前进度

- 状态：进行中
- 完成度：第一批核心结构已落地，可进入联调和后续聚合开发

## 本阶段已完成内容

- 在多轮 classic 审计路径中，为每个 round 补齐标准化 chunk 审计结果：
  - `chunk_id`
  - `round_index`
  - `clause_range`
  - `risk_items`
  - `risk_count`
  - `evidence_links`
  - `confidence_score`
  - `parse_failed_flag`
  - `truncated_flag`
  - `completion_tokens`
  - `max_output_tokens`
  - `summary`
- 每个 chunk 审计完成后写入 round trace，便于后续异常重试与复核。
- 将 chunk 审计结果挂到多轮 raw 输出：
  - `raw.chunk_audit_results`

## 本阶段涉及文件

- `app/services/contract_audit.py`
- `tests/test_audit_token_policy.py`

## 当前实现说明

### 1. 分组策略

- 仍然基于现有合同条款拆分结果 `build_preview_clauses()`。
- 以“条款”为基本单元进行分组，而不是按固定字符粗切。
- 当前分组控制维度：
  - `clause_group_target_tokens`
  - `max_clauses_per_group`
  - `max_rounds`

### 2. Chunk 输出结构

- `clause_range` 当前已包含：
  - 起止条款 ID
  - 条款 ID 列表
  - 条款路径列表
  - 页码范围
  - 起止段号
- `evidence_links` 当前来自 chunk 级引用法规去重结果。
- `confidence_score` 当前按风险分数均值计算，后续可升级为更稳健的置信度聚合策略。

### 3. Trace 能力

- 每个 chunk 完成后写 `write_round_trace()`：
  - `chunk_id`
  - `clause_ids`
  - `risk_count`
  - `confidence_score`
  - `parse_failed_flag`
  - `truncated_flag`

## 测试结果

### 执行命令

```bash
python -m pytest tests/test_audit_token_policy.py tests/test_audit_utils.py tests/test_contract_audit.py tests/test_llm_router.py tests/test_llm_local_mode.py
```

### 结果摘要

- 结果：`34 passed`
- 新增验证点：
  - `raw.chunk_audit_results` 数量正确
  - `chunk_id` 生成正确
  - `clause_range.start_clause_id` 生成正确
  - `risk_count / parse_failed_flag / truncated_flag` 输出正确

## 当前未完成项

- 尚未实现“按 PDF/Word/TXT 结构差异做更强的章节级智能聚合”。
- 尚未实现 chunk 级独立服务模块拆分，目前仍集成在 `contract_audit.py` 内。
- 尚未实现分段结果的正式汇总去重与冲突合并，这属于第 3 阶段。
- 尚未实现高风险缺法规引用、解析失败、输出触顶的自动复核与局部重试，这属于第 4 阶段。

## 下一步计划

- 将 `chunk_audit_results` 作为第 3 阶段输入。
- 开始开发：
  - 同类风险去重
  - 跨条款冲突识别
  - 法规引用归并
  - 漏项复核骨架
