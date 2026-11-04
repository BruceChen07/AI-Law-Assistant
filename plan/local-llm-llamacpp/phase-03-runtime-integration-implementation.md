# Phase 03 - 运行时集成与文档收敛实施记录

## 1. 阶段目标

- 收紧 `llama.cpp` 在运行时中的本地化约束
- 补齐标准配置样例
- 新增针对 `llama.cpp` 主模型切换的本地运行手册
- 清理旧文档中与云端回退不一致的表述

## 2. 本阶段代码与文档变更

### 2.1 `LLMService` 本地运行约束收紧

文件：`app/core/llm.py`

本阶段新增：

- `_provider_uses_local_runtime(provider)`

行为调整：

- 当 `provider in {"ollama", "llama_cpp"}` 时，运行时不再从：
  - 配置文件
  - 安全存储
  - 环境变量
  读取 API Key
- 避免本地 `llama.cpp` 服务误带 `Authorization` 头

目的：

- 保持本地端侧服务链路纯净
- 降低因为环境变量残留导致的配置歧义
- 与“本地模型不依赖云端凭证”的要求保持一致

### 2.2 配置样例扩展

文件：`app/config.example.json`

新增/调整：

- `network_policy.allowed_hosts` 增加 `llamacpp.intra`
- 新增 `local_llama_cpp_profile`

用途：

- 为主模型切换到 `llama.cpp` 提供可直接参考的配置样板
- 明确推荐组合为：
  - 主模型：`llama_cpp`
  - 小模型：`ollama`

### 2.3 中文说明补充

文件：`README.zh-CN.md`

新增：

- `端侧 llama.cpp 本地替换说明（2026-07 更新）`

补充内容：

- 标准入口脚本
- 推荐模型映射
- 本地部署约束
- 关联阶段文档路径

### 2.4 旧 runbook 标记为过期

文件：`plan/edge-llm-cpu-local-deployment-runbook.md`

调整：

- 在文档顶部增加 2026-07 更新说明
- 明确该文档已不再作为主模型切换的现行 runbook
- 指向新的 `phase-03-runtime-cutover-runbook.md`

### 2.5 新增 llama.cpp 本地切换手册

文件：`plan/local-llm-llamacpp/phase-03-runtime-cutover-runbook.md`

覆盖内容：

- 前置条件
- 启动步骤
- 配置核对
- Admin 验证
- 回归建议
- 性能观察项
- 本地回退方案

## 3. 技术选型调整

本阶段没有改变主架构方向，进一步确认如下：

- `llama.cpp` 仍作为主模型运行时
- 小模型迁移期仍可使用 Ollama
- 回退仅允许本地 Ollama，不允许回到云端 provider

## 4. 风险与限制

- 当前阶段仍未进行 GGUF 真机加载验证
- 性能参数仍需在真实目标机器上记录
- 旧文档仍保留在仓库中，但已通过顶部标记说明其不再是当前生效版本
