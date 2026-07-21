# 第1阶段开发记录：Token 预算与准入

## 阶段目标

- 建立合同审计在 LLM 调用前的统一 token 预算能力。
- 禁止超预算请求直接硬截断后进入单轮审计。
- 在超预算或 prompt 已发生截断风险时，自动切换到多轮审计骨架。

## 本阶段完成内容

- 新增公共 token 估算工具：
  - `app/core/token_utils.py`
- 新增合同审计 token 策略模块：
  - `app/services/audit_token_policy.py`
- 将 `LLMService._estimate_input_tokens()` 收敛到统一口径：
  - `app/core/llm.py`
- 在合同审计主链路接入：
  - classic prompt 构造与 prompt 元数据提取
  - 预算评估
  - 超预算触发多轮模式
  - token 预算 trace 落盘
  - `app/services/contract_audit.py`
- 新增配置样板：
  - `app/config.example.json`

## 设计落地说明

### 1. 统一预算策略

- 新增 `audit_token_policy` 配置段，支持：
  - `context_window_tokens`
  - `input_soft_limit_tokens`
  - `input_hard_limit_tokens`
  - `reserve_output_tokens`
  - `safety_margin_tokens`
  - `force_multi_pass_when_exceed`
  - `force_multi_pass_when_prompt_truncated`

### 2. 不再依赖“字符数近似”直接放行

- 对 classic 审计 prompt 进行显式构造。
- 对以下输入分量进行预估：
  - 合同全文上下文
  - 结构化条款
  - 法规证据
  - system/user prompt
- 输出预算使用 `classic_audit_max_tokens` 或模型默认 `max_tokens`。

### 3. 超预算熔断逻辑

- 出现以下任一情况时，进入多轮模式：
  - prompt 输入超过 soft limit
  - prompt 输入超过 hard limit
  - 预计总 token 超过上下文窗口
  - prompt 构造过程中已经发生：
    - 合同全文截断
    - 条款省略
    - 证据省略
    - 条款正文截断
    - 证据正文截断

## 关键交付物

- `app/core/token_utils.py`
- `app/services/audit_token_policy.py`
- `app/services/contract_audit.py`
- `app/config.example.json`
- `tests/test_audit_token_policy.py`

## 测试结果

### 执行命令

```bash
python -m pytest tests/test_audit_token_policy.py tests/test_audit_utils.py tests/test_contract_audit.py tests/test_llm_router.py tests/test_llm_local_mode.py
```

### 结果摘要

- 结果：`34 passed`
- 新增验证覆盖：
  - token 策略默认值
  - prompt 截断触发多轮
  - 条款分组计划
  - 多轮 classic 审计骨架输出
- 回归验证覆盖：
  - 合同审计既有单元测试
  - `llm_router` 本地优先路由逻辑
  - 本地 `ollama/llama.cpp` 路由能力

## 阶段准出判断

- 已达到本阶段“可运行、可测试、可追踪”的最小闭环。
- 尚未完成“精确 tokenizer 对齐到模型原生分词器”的增强版能力，目前仍采用统一近似估算。

## 已知限制

- 当前多轮模式仍是第 1 阶段骨架版本，尚未完成分段结果的完整汇总去重。
- 估算器仍为通用近似算法，不是 Qwen/llama.cpp 原生 tokenizer 精确计数。
- trace 模块仍有 `datetime.utcnow()` 的弃用告警，后续应统一改为 timezone-aware 时间。

## 下一阶段输入

- 第 2 阶段将基于本阶段的预算决策结果，继续沉淀分段审计统一输出结构。
