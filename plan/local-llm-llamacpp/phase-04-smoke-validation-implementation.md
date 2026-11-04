# Phase 04 - 真机烟测与验收脚本实施记录

## 1. 阶段目标

- 为 `llama.cpp` 主模型切换提供可执行的本地烟测脚本
- 将配置检查、服务探活、模型发现、基础对话调用统一收敛为一份 JSON 报告
- 让后续真机联调不再依赖人工逐项点检

## 2. 本阶段实现

### 2.1 新增本地烟测脚本

文件：`bin/validate-local-llamacpp-runtime.py`

脚本能力：

- 读取指定配置文件
- 核对以下关键配置：
  - `llm_config.provider == llama_cpp`
  - `local_llm.enabled == true`
  - `local_llm.main_model.provider == llama_cpp`
  - `network_policy.mode == offline_strict`
  - `allowed_hosts` 包含 loopback 与 `llamacpp.intra`
- 探测以下服务：
  - `llama.cpp /v1/models`
  - `Ollama /api/tags`
  - `Backend /health`（可跳过）
- 对 `llama.cpp` 执行一次最小对话烟测：
  - `/v1/chat/completions`
  - prompt: `Reply with exactly: OK`
- 输出结构化 JSON 报告到：
  - `plan/local-llm-llamacpp/reports/`

### 2.2 报告结构

输出报告包含：

- 生成时间
- 使用的配置文件路径
- 边缘本地策略标记
- 配置摘要
- 配置检查结果
- 服务检查结果
- 已发现模型列表
- 基础对话烟测结果
- 总体 `ok` 状态

## 3. 技术设计说明

### 3.1 为什么只用标准库

原因：

- 当前环境缺少部分 Python 依赖
- 烟测脚本应尽量独立于业务运行环境
- 只要系统自带 Python 可运行，就能先完成最基本的本地联调验证

### 3.2 为什么保留 Ollama 探测

原因：

- 迁移阶段小模型仍可能保留在 Ollama
- 需要同时确认主模型与小模型双栈状态
- 有助于定位是主模型不可达还是辅助模型不可达

## 4. 与端侧约束的关系

本阶段脚本明确输出：

- `edge_only = true`
- `cloud_fallback_allowed = false`

用于保证验收记录明确表述当前方案仅面向端侧本地大模型。
