# Phase 05 - Ollama vs llama.cpp 基准测试报告

## 1. 测试目标

- 在相同硬件条件下，对比 Ollama 与 `llama.cpp` 的核心性能指标
- 验证 `llama.cpp` 替代方案是否带来可量化的速度提升
- 为最终移除 Ollama 提供数据依据

## 2. 当前测试环境现状

本次检查到的环境状态：

- `ollama --version`：
  - 可执行
  - 版本：`0.30.10`
  - 当前未连接到运行中的 Ollama 服务
- `llama-server --version`：
  - 当前不可执行
  - 原因：不在 PATH 中
- `nvidia-smi`：
  - 可执行
  - GPU：`NVIDIA GeForce RTX 3060`
  - 当前采样：`7% GPU util, 1552 / 12288 MB`
- 工作区内未发现目标 GGUF 主模型文件

## 3. 已完成的基准环境搭建

已新增：

- `bin/benchmark-ollama-vs-llamacpp.py`

脚本支持：

- 冷启动 ready 时间
- 平均延迟 / P95 延迟
- 令牌生成速度
- 内存峰值
- GPU 利用率与显存占用峰值

## 4. 当前无法完成正式对比测试的原因

阻塞项如下：

1. 本机未启动 Ollama 服务
2. 本机未就绪 `llama-server`
3. 工作区未放置目标 GGUF 文件
4. 因此无法保证“同一硬件、同一模型”的正式对比条件

## 5. 正式测试执行指令

在前置条件满足后，执行：

```bash
python .\bin\benchmark-ollama-vs-llamacpp.py ^
  --ollama-model qwen36-gguf-bench ^
  --llama-cpp-model qwen36-gguf-bench ^
  --rounds 3 ^
  --warmup-rounds 1 ^
  --max-tokens 256
```

如需对比冷启动：

```bash
python .\bin\benchmark-ollama-vs-llamacpp.py ^
  --ollama-model qwen36-gguf-bench ^
  --llama-cpp-model qwen36-gguf-bench ^
  --ollama-start-command "ollama serve" ^
  --llama-cpp-start-command "python .\bin\start-local-llamacpp-server.py --alias qwen36-gguf-bench"
```

## 6. 报告回填模板

### 6.1 测试条件

- CPU：
- GPU：
- 内存：
- 模型文件：
- quant：
- ctx size：
- threads：
- gpu layers：
- rounds：
- prompt 长度：

### 6.2 测试结果

| 指标 | Ollama | llama.cpp | 提升幅度 |
|---|---:|---:|---:|
| 冷启动 ready 时间 (s) | 待测 | 待测 | 待测 |
| 平均延迟 (s) | 待测 | 待测 | 待测 |
| P95 延迟 (s) | 待测 | 待测 | 待测 |
| 令牌生成速度 (tok/s) | 待测 | 待测 | 待测 |
| 峰值内存 (MB) | 待测 | 待测 | 待测 |
| 峰值 GPU 利用率 (%) | 待测 | 待测 | 待测 |
| 峰值 GPU 显存 (MB) | 待测 | 待测 | 待测 |

### 6.3 结论

- 是否确认 `llama.cpp` 速度更快：
- 是否确认资源利用更优：
- 是否允许进入下一步“完全移除 Ollama”：

## 7. 风险说明

由于本次 `llama.cpp` 目标模型来自 ModelScope，且与 Ollama 生态模型文件不兼容，
本报告不再要求“同一 GGUF”作为前提。

本报告的定位是：

- 旧 Ollama 方案基线 vs 新 `llama.cpp` 方案
- 用于迁移决策
- 不用于证明两个 runtime 在同一二进制权重下的纯微基准差异

## 8. 本次实际执行记录

### 8.1 实际执行命令

```bash
python .\bin\benchmark-ollama-vs-llamacpp.py --ollama-model qwen3.6:27b --llama-cpp-model Qwen3.6-35B-A3B-UD-Q4_K_M.gguf --rounds 1 --warmup-rounds 0 --max-tokens 64 --timeout 15
```

### 8.2 生成报告

- `plan/local-llm-llamacpp/reports/benchmark-ollama-vs-llamacpp-20260710-004401.json`

### 8.3 实际结果

- 总体结果：`ok = false`
- 原因：当前两套运行时都未就绪，无法进入正式对比轮次

具体表现：

- Ollama:
  - `http://127.0.0.1:11434/api/version` 不可达
  - `http://127.0.0.1:11434/v1/models` 不可达
  - 脚本摘要：`ollama endpoint is not reachable`
- llama.cpp:
  - `http://127.0.0.1:18080/health` 不可达
  - `http://127.0.0.1:18080/v1/models` 不可达
  - 脚本摘要：`llama_cpp endpoint is not reachable`

### 8.4 当前结论

- 基准测试脚本本身可执行
- JSON 报告已成功生成
- 当前仅完成“测试环境就绪性验证”
- 尚未完成正式性能对比

### 8.5 进入正式对比测试前的必备条件

1. 启动 Ollama 服务
2. 准备并启动 `llama-server`
3. 放置同源 GGUF 主模型文件
4. 固定业务样本、请求参数与硬件条件，确保基线可重复
