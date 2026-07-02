# 记忆功能临时禁用实施记录

## 1. 背景与触发依据

本次实施的直接触发依据来自 `logs/2026-07-03/main.log` 中的记忆审计链路日志。以 `2026-07-03 02:04:12` 起的一段审计为例，可观察到：

- 先触发 `memory_audit_start clauses=52 compacted=24 short_limit=1600 top_k=3`
- 随后在同一次合同审计中连续触发多轮 `memory_round_done`
- 每一轮又伴随 `task_profile=contract_clause_audit` 的本地大模型调用
- 同一时间窗口内 `gemma4:12b` 多次处理 `input_tokens_est=1331/1541/1798/1697/1766`
- 单轮耗时在 `3.4s` 到 `20.0s` 之间波动，且该过程会持续叠加

这说明当前“记忆审计”会把一次合同审计拆成多轮条款级推理，并不断向本地模型发送增大的上下文。对于端侧模型而言，这类多轮累计上下文和多次推理最容易放大超时与资源占用问题，因此需要先提供一套可随时启停的临时总闸。

## 2. 记忆功能架构梳理

### 2.1 初始化与触发条件

记忆功能并非服务启动即持续运行，而是在合同审计执行时按需触发：

1. 合同上传或 API 调用进入 `app/api/routers/contracts.py`
2. 路由层调用 `app/services/contract_audit.py` 中的 `audit_contract()`
3. `audit_contract()` 完成文本抽取、条款切分、法规证据召回后，进入记忆路径判定
4. 只有在记忆开关允许时，才会调用 `execute_memory_audit()`

### 2.2 核心调用链路

主入口与编排层：

- `app/services/contract_audit.py`
  - `audit_contract()`
  - `_get_memory_runtime_config()`
  - `_build_classic_audit()`

记忆主流程：

- `app/services/contract_audit_modules/memory_pipeline/__init__.py`
- `app/services/contract_audit_modules/memory_pipeline/audit_loop.py`

记忆底层能力：

- `app/memory_system/manager.py`
- `app/memory_system/search.py`
- `app/memory_system/indexer.py`
- `app/memory_system/experience_repo.py`

LLM 调用链：

- `app/core/llm.py`
- task profile `contract_clause_audit`

管理接口与前端配置：

- `app/api/routers/admin.py`
- `web/Admin.jsx`
- `web/i18n/adminI18n.js`

### 2.3 启用执行流程

默认执行流程如下：

1. `audit_contract()` 读取 `memory_runtime_config`
2. 若 `memory_module_enabled=true`，则进入记忆编排
3. `execute_memory_audit()` 在 `audit_loop.py` 内创建 `HybridSearcher`
4. `HybridSearcher` 结合记忆索引与 SQLite/BM25 混合检索能力，为每轮条款审计构造补充上下文
5. `MemoryManager` 分轮推进，产生 `memory_round_done`
6. 每轮通过 `llm.chat()` 触发 `contract_clause_audit`
7. 完成后输出审计结果，并通过 `save_audit_episode()` 将经验写回记忆仓

### 2.4 依赖配置参数

记忆链路主要依赖以下配置：

- `memory_runtime_config.memory_module_enabled`
- `memory_runtime_config.memory_mode_when_disabled`
- `memory_runtime_config.memory_disable_fallback_on_error`
- `memory_runtime_config.memory_token_guard_enabled`
- `memory_runtime_config.memory_max_llm_calls_per_audit`
- `memory_runtime_config.memory_max_prompt_chars_per_clause`
- `memory_dir`
- `data_dir`
- `local_llm.routing.task_profiles.contract_audit_memory`
- `local_llm.routing.task_profiles.contract_clause_audit`
- `local_llm.execution.memory_clause_force_main_for_priority`
- `local_llm.execution.memory_flush_force_main`

### 2.5 生效节点总览

记忆功能会在以下节点生效：

- 合同审计编排入口：决定是否进入记忆模式
- 记忆审计主循环：按条款轮次执行
- 记忆检索：混合搜索与候选裁剪
- LLM 调用：每轮条款级推理
- 经验沉淀：将结果写回记忆仓
- 管理后台：查看和调整记忆相关运行参数

## 3. 本次临时禁用方案

### 3.1 设计原则

本次禁用方案遵循以下约束：

- 不重构 `memory_pipeline` 和 `memory_system` 核心业务代码
- 在编排层新增独立总开关，最小化侵入
- 保留原始 `memory_runtime_config`，便于后续恢复
- 所有禁用状态必须可配置、可观测、可回滚

### 3.2 新增独立开关

新增配置项：

```json
{
  "memory_temporary_disable": {
    "enabled": true,
    "fallback_mode": "classic",
    "reason": "edge_llm_context_limit",
    "trigger_source": "ops_temporary_disable_2026-07-03"
  }
}
```

语义说明：

- `enabled`: 是否启用临时全局禁用
- `fallback_mode`: 禁用后强制回退模式，当前固定为 `classic`
- `reason`: 禁用原因，写入日志与审计元数据
- `trigger_source`: 禁用触发源，记录是哪个运维动作或配置变更触发

### 3.3 生效逻辑

在 `audit_contract()` 内部新增优先级判断：

1. 先读取 `memory_runtime_config`
2. 再读取 `memory_temporary_disable`
3. 若 `memory_temporary_disable.enabled=true`，则无条件短路记忆路径
4. 直接走 `_build_classic_audit()`
5. 不触发 `execute_memory_audit()`
6. 不产生新的记忆轮次推理链路

### 3.4 与原有开关的关系

- `memory_module_enabled`：属于记忆模块原生开关
- `memory_temporary_disable.enabled`：属于新增的运维止损总闸

优先级：

- `memory_temporary_disable.enabled=true` 时，优先级高于 `memory_module_enabled`
- 即使 `memory_module_enabled=true`，系统仍会强制走 `classic`

## 4. 日志与可观测性补充

新增日志事件：

- `memory_temporarily_disabled`

日志字段包含：

- 时间戳：由主日志系统自动记录
- 服务节点：`service_node`
- 审计标识：`audit_id`
- 文件路径：`file`
- 触发源：`trigger_source`
- 禁用原因：`disable_reason`
- 回退模式：`fallback_mode`
- 运行时原始开关：`runtime_memory_enabled`
- 条款数：`clauses`

此外，审计返回元数据也会附带：

- `memory_temporarily_disabled`
- `memory_temporary_disable_reason`
- `memory_temporary_disable_trigger_source`
- `memory_temporary_disable_fallback_mode`
- `memory_runtime_module_enabled`

## 5. 代码变更清单

本次实施涉及：

- `app/services/contract_audit.py`
- `app/api/routers/admin.py`
- `app/config.json`
- `app/config.example.json`
- `web/Admin.jsx`
- `web/i18n/adminI18n.js`
- `tests/test_contract_audit_memory_mode.py`
- `tests/test_admin_memory_config.py`
- `README.zh-CN.md`
- `plan/edge-llm-cpu-local-deployment-runbook.md`

## 6. 测试报告

### 6.1 验证目标

重点验证两项指标：

1. 临时禁用后，记忆链路不再继续放大端侧模型上下文与轮次调用
2. 除记忆功能外，其余核心业务配置与页面流程保持正常

### 6.2 已完成验证

后端单元测试：

- `tests/test_contract_audit_memory_mode.py -k "temporary_disable or memory_module_disabled or memory_llm_call_budget_limit"`
  - 结果：通过
  - 覆盖点：
    - 原生记忆关闭仍能走 classic
    - 新的临时总闸开启后不再调用记忆主流程
    - 记忆预算守卫原有能力不受影响

后台配置接口测试：

- `tests/test_admin_memory_config.py`
  - 结果：通过
  - 覆盖点：
    - Admin 读取新配置字段
    - Admin 更新并持久化临时禁用开关与元数据

前端构建验证：

- `web` 执行 `npm run build`
  - 结果：通过
  - 说明：Admin 新增配置项未引入前端构建错误

### 6.3 超时问题验证结论

基于本次改动，新的合同审计在临时禁用记忆功能时：

- 不会再进入 `memory_audit_start`
- 不会再产生连续的 `memory_round_done`
- 不会再触发多轮 `contract_clause_audit` 本地推理
- 因此可显著降低上下文堆叠和端侧模型超时风险

说明：

- 本次仓内验证已证明调用链被切断
- 若需生产环境最终确认，应在部署后再次观察一轮真实审计日志，确认只走 `classic`

## 7. 运维启停说明

### 7.1 临时禁用

在 `app/config.json` 或 Admin → 模型配置 → 记忆审计配置 中设置：

```json
{
  "memory_temporary_disable": {
    "enabled": true,
    "fallback_mode": "classic",
    "reason": "edge_llm_context_limit",
    "trigger_source": "ops_temporary_disable_2026-07-03"
  }
}
```

### 7.2 恢复启用

恢复时仅需：

```json
{
  "memory_temporary_disable": {
    "enabled": false
  }
}
```

无需重构记忆模块核心代码。

### 7.3 恢复前置条件

建议满足以下条件后再恢复：

- 端侧模型对当前合同长度的推理耗时稳定
- 已完成上下文裁剪或分批推理能力建设
- 日志中不再频繁出现长轮次 `memory_round_done`
- 至少一轮真实合同审计在启用记忆模式下不再持续超时

## 8. 中长期优化方案

后续建议按以下方向推进，根治上下文超限问题：

### 8.1 RAG + BM25 混合搜索

- 用规则引擎先筛选候选法规与记忆片段
- 再通过 BM25 + 向量检索混合召回
- 最后只把高相关证据与条款上下文送入大模型

### 8.2 分批推理机制

- 为每次任务设置上下文预算
- 当总上下文超限时自动拆分为多批
- 每批独立推理并生成中间结果
- 最后通过合并器汇总所有批次结论并返回用户

### 8.3 调度与观测增强

- 为记忆路径增加批次级 Token 预算与超时预算
- 对 `memory_round_done`、批次耗时、上下文长度做统计看板
- 建立模型版本、任务长度、超时率之间的关联分析

## 9. 当前结论

本次实施已完成：

- 记忆功能全生命周期入口梳理
- 独立临时总闸配置落地
- 禁用日志埋点补充
- 回退链路验证
- 文档与运维说明同步

当前状态：

- 记忆功能处于“可恢复的临时禁用”状态
- 恢复时只需关闭 `memory_temporary_disable.enabled`
