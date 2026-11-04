# T11 执行记录

## 输入与依赖

- 设计版本：1.0
- 依赖任务：T10 DONE
- 实现：v1覆盖保护、v2完整消息预算、长单元拆分和块重规划

## 修改

- `plan_audit` 支持限定来源范围和显式 `plan_version`。
- 新增 `replan_incomplete_chunk`，仅替换指定未完成块。
- 子块记录 `parent_chunk_id`，并强制与原块来源区间等价。
- 补充UT06，证明已完成块不进入重规划。

## 验证

| 测试ID | 场景 | 结果 |
|---|---|---|
| UT04 | 797碎片完整覆盖和完整预算 | PASS |
| UT05 | 超长单元、max_chunks和非法策略 | PASS |
| UT06 | 仅重规划未完成块、plan_version递增 | PASS |
| UT08 | 非精确tokenizer拒绝 | PASS |
| v1保护 | 超过max_rounds明确失败，不吞并尾部 | PASS |

预算规划定向回归：19 passed、0 failed。

## 风险与未完成项

- 重规划目前是计划层能力，尚未接入v2运行编排器；由T12完成。
- 未对真实llama.cpp `/apply-template` 与 `/tokenize` 做能力探测。

## 下一步

- T12：接入运行编排器、记录计划/覆盖，并用隔离fake模型验证。
