# Phase 05 - Ollama 替代必要性、可行性与基准测试方案

## 1. 为什么当前还需要启动 Ollama

### 1.1 现阶段 Ollama 的核心作用

在当前仓库中，Ollama 并不只是“另一个推理入口”，它承担了三类职责：

1. 推理运行时职责
   - `app/core/llm.py` 中当 `provider == "ollama"` 时，走原生 `/api/chat`
   - 负责承接当前默认主模型或小模型的推理请求

2. 模型托管与发现职责
   - `app/api/routers/admin.py` 提供 `/api/admin/ollama/models`
   - `bin/start-local-llm-servers.py` 负责：
     - 启动 `ollama serve`
     - 查询 `/api/tags`
     - 检查本地模型是否已安装
     - 可选执行 `ollama pull`

3. 迁移期小模型职责
   - `app/core/llm_router.py` 和 `bin/apply-local-llm-config.py` 当前仍允许：
     - 主模型切到 `llama_cpp`
     - 小模型继续留在 Ollama
   - 这是为了降低一次性替换全部路由的风险

### 1.2 当前依赖 Ollama 的核心场景

结合代码，Ollama 仍直接服务于以下场景：

- Admin 后台模型发现与切换
- 本地模型安装与缺失模型拉取
- 小模型路由：
  - `tax_match_small`
  - `entity_extract_small`
  - `memory_flush`
- 迁移阶段的主模型回退路径

### 1.3 过去依赖 Ollama 的原因

原有方案依赖 Ollama，主要因为它在工程侧已经把“运行时 + 模型管理 + 安装体验”封装好了：

- 启动简单：`ollama serve`
- 拉模型简单：`ollama pull`
- 模型发现现成：`/api/tags`
- 与业务系统对接成本低

因此，过去启动 Ollama 的必要性，本质上是“用一个现成运行时解决模型托管与调用问题”，而不是业务逻辑本身只能依赖 Ollama。

## 2. llama.cpp 全功能替代 Ollama 的可行性分析

## 2.1 总体结论

结论：**可以替代，但不是“零改动替代”**。

更准确地说：

- 推理主链路：可替代
- 模型发现：可替代
- Admin 测试与切换：可替代
- 模型安装/拉取：不能 1:1 替代，需要改为“本地资产准备 + 启动脚本”
- 小模型双栈：可逐步收敛到 `llama.cpp`

## 2.2 功能覆盖度对比

| 能力项 | Ollama | llama.cpp | 替代状态 |
|---|---|---|---|
| 本地推理 | 支持 | 支持 | 可替代 |
| OpenAI-compatible `/v1` | 支持 | 支持 | 可替代 |
| 原生 `/api/chat` | 支持 | 不支持 | 需业务改走 `/v1` |
| 模型发现 | `/api/tags` | `/v1/models` | 可替代 |
| 模型拉取/安装 | `ollama pull` | 不提供统一拉取层 | 需自建资产管理 |
| GGUF 原生加载 | 间接支持 | 原生支持 | `llama.cpp` 更强 |
| 细粒度线程/批大小控制 | 一般 | 强 | `llama.cpp` 更强 |
| `n-gpu-layers` 调优 | 间接 | 原生 | `llama.cpp` 更强 |
| 推理连续批处理 | 有限 | 更灵活 | `llama.cpp` 更强 |
| 多模型托管体验 | 更完整 | 需自管 | Ollama 更强 |

## 2.3 模型兼容性分析

### 可以直接接管的前提

若满足以下条件，`llama.cpp` 可直接替代主模型运行时：

- 目标模型存在可用 GGUF
- 对应聊天模板与 tokenizer 行为可对齐
- 上下文窗口、rope 参数、stop token 已匹配

### 当前项目的实际约束

根据当前迁移要求，本次从 ModelScope 下载的是专门适配 `llama.cpp` 的模型资产，
它与现有 Ollama 生态中的模型文件格式、运行逻辑不兼容，不能视为可通用模型文件。

因此本项目的正式基准测试，不再以“同一 GGUF 同时跑两套运行时”为前提，而是改为：

1. 以当前线上/历史 Ollama 方案作为旧基线
2. 以当前目标 `llama.cpp` 模型作为新方案
3. 在相同硬件、相同业务样本、相同请求规模下进行对比

这意味着：

- 对比结果可以用于迁移决策
- 但不能被解释为“同一二进制权重在两个 runtime 的纯运行时差”

### 不兼容风险

- 不同聊天模板导致回答风格或停止条件不同
- 不同量化版本导致性能与准确率差异
- 不同上下文设置导致长文本行为不一致
- Ollama 默认推理参数与 `llama.cpp` 显式参数不一致

## 2.4 性能提升空间的量化评估

### 理论原因

`llama.cpp` 在性能上可能优于 Ollama，核心原因通常不是“模型变了”，而是：

- 更低的运行时封装开销
- 更直接的 GGUF 加载链路
- 可显式控制：
  - `--threads`
  - `--batch-size`
  - `--ubatch-size`
  - `--parallel`
  - `--n-gpu-layers`
  - `--ctx-size`

### 量化预估区间

在相同硬件、相同 GGUF、相近推理参数下，保守预估：

- 冷启动 ready 时间：**改善 10% - 35%**
- 首次请求响应时间：**改善 5% - 25%**
- 令牌生成速度：**改善 10% - 40%**
- GPU 利用率：通常更稳定，峰值更高
- 内存占用：取决于 mmap、gpu layers、ctx size，可能下降 **5% - 20%**，也可能因参数更激进而上升

说明：

- 以上为迁移前的工程级预估区间，不等同于真实实测值
- 真实值必须以 phase 05 基准脚本输出为准

## 3. 完整迁移实施方案

## 3.1 目标架构

### 迁移前

- 主模型：Ollama
- 小模型：Ollama
- 模型安装：`ollama pull`
- 模型发现：`/api/tags`

### 迁移后目标

- 主模型：`llama.cpp / llama-server`
- 小模型：优先逐步切换到 `llama.cpp`
- 模型资产：本地 GGUF 文件
- 模型发现：`/v1/models`
- 模型安装：离线文件投放，不依赖 `ollama pull`

## 3.2 模型格式转换与适配步骤

### 路径 A：已有 GGUF

适用于当前目标模型：

- `Qwen3.6-35B-A3B-UD-Q4_K_M.gguf`

步骤：

1. 将 GGUF 放入：
   - `models/llm/`
2. 用 `llama.cpp` 直接加载
3. 若需要公平对比 Ollama：
   - 使用 `assets/ollama-from-gguf.Modelfile.example`
   - 执行 `ollama create <name> -f <Modelfile>`

### 路径 B：只有 Hugging Face safetensors

步骤：

1. 准备原始权重
2. 使用 `llama.cpp` 官方转换脚本转为 GGUF
3. 根据目标硬件执行量化
4. 校验 tokenizer/chat template/rope 参数

### 路径 C：只有 Ollama 已安装模型，没有原始 GGUF

建议：

- 不建议把 Ollama 私有封装产物当作长期迁移源
- 应回到原始模型来源，重新准备可追溯的 GGUF

原因：

- 可追溯性更强
- 参数更透明
- 便于长期维护和复现实验

## 3.3 部署架构调整步骤

1. 准备本地 `llama-server`
2. 准备 GGUF 到 `models/llm/`
3. 启动：
   - `python .\bin\start-local-llamacpp-server.py`
4. 应用配置：
   - `python .\bin\apply-local-llm-config.py --provider llama_cpp --small-provider ollama`
5. 在 Admin 中验证：
   - `llama.cpp` 模型发现
   - 模型切换
   - `llm-test`
6. 稳定后将小模型也迁至 `llama.cpp`

## 3.4 推理流程重构指南

### 当前流程

- `resolve_llm_route()` 选择 `main_model` / `small_model`
- `LLMService.chat()` 按 provider 分流：
  - `ollama` -> `/api/chat`
  - 其他 provider -> `/v1/chat/completions`

### 最终建议流程

- 所有本地模型统一走 OpenAI-compatible `/v1/chat/completions`
- `provider == llama_cpp` 作为主路径
- `provider == ollama` 仅保留为临时回退，最终移除

### 需要完成的收尾动作

1. 将 `small_model.provider` 逐步改为 `llama_cpp`
2. 停止依赖 `/api/admin/ollama/models`
3. 下线 `bin/start-local-llm-servers.py`
4. 下线 `ollama pull` 相关运维流程

## 4. 基准测试环境设计

## 4.1 已新增测试工具

新增脚本：

- `bin/benchmark-ollama-vs-llamacpp.py`

支持对比：

- 冷启动 ready 时间
- 平均延迟
- P95 延迟
- 令牌生成速度
- 进程内存峰值
- GPU 利用率峰值
- GPU 显存占用峰值

## 4.2 公平测试原则

必须同时满足：

1. 相同硬件
2. 相同业务样本与请求参数
3. 相同 prompt
4. 相同 `max_tokens`
5. 尽量对齐上下文窗口、温度、stop token、并发度

说明：

- 由于本次 `llama.cpp` 目标模型与 Ollama 旧方案模型资产不兼容，无法再要求“同一 GGUF”
- 因此测试结论用于迁移评估，而不是用于抽象 runtime 微基准论文式结论

## 4.3 建议测试流程

### 启动 llama.cpp

```bash
python .\bin\start-local-llamacpp-server.py ^
  --model-path .\models\llm\Qwen3.6-35B-A3B-UD-Q4_K_M.gguf ^
  --alias Qwen3.6-35B-A3B-UD-Q4_K_M.gguf ^
  --ctx-size 8192 ^
  --threads 20 ^
  --batch-size 1024 ^
  --ubatch-size 512 ^
  --parallel 1
```

### 运行基准测试

```bash
python .\bin\benchmark-ollama-vs-llamacpp.py ^
  --ollama-model deepseek-r1:14b ^
  --llama-cpp-model Qwen3.6-35B-A3B-UD-Q4_K_M.gguf ^
  --rounds 3 ^
  --warmup-rounds 1 ^
  --max-tokens 256
```

### 如果需要测冷启动

```bash
python .\bin\benchmark-ollama-vs-llamacpp.py ^
  --ollama-model deepseek-r1:14b ^
  --llama-cpp-model Qwen3.6-35B-A3B-UD-Q4_K_M.gguf ^
  --ollama-start-command "ollama serve" ^
  --llama-cpp-start-command "python .\bin\start-local-llamacpp-server.py --alias Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
```

## 4.4 当前环境现状

当前仓库环境检查结果：

- `ollama --version` 可执行，但本机未连接到运行中的 Ollama 服务
- `llama-server` 当前不在 PATH 中
- `nvidia-smi` 可用，检测到：
  - `NVIDIA GeForce RTX 3060`
  - 当前采样：
    - GPU util: `7%`
    - 显存占用：`1552 / 12288 MB`
- 工作区内尚未发现 GGUF 主模型文件

结论：

- 基准测试环境脚本已搭建完成
- 但当前机器仍不具备“立即开始正式对比测试”的全部前置条件

## 5. 迁移过程中的问题排查与修复方案

## 5.1 可能的功能缺失

### 问题 1：没有 `ollama pull`

影响：

- 无法像之前一样一条命令下载模型

修复方案：

- 建立离线模型资产目录
- 将模型准备纳入运维手册和版本记录
- 使用校验和文件记录资产版本

### 问题 2：`/api/tags` 不再存在

影响：

- Admin 侧模型发现逻辑会失效

修复方案：

- 已新增 `/api/admin/llama-cpp/models`
- 最终阶段将 UI 完全切向 `llama.cpp`

### 问题 3：聊天模板不一致

影响：

- 输出格式、停止条件、JSON 结构可能漂移

修复方案：

- 固定 chat template
- 固定 stop token
- 对关键业务提示词重新做一次回归

### 问题 4：小模型双栈导致维护复杂度高

影响：

- 运维仍需同时保留两个运行时

修复方案：

- 在主模型稳定后，补齐 `small_model` 的 `llama_cpp` 配置与回归
- 最终去掉 Ollama small route

### 问题 5：性能测试出现结果偏差

影响：

- 无法证明速度提升来自 runtime，而可能来自模型版本差异

修复方案：

- 必须固定业务样本和请求参数
- 必须记录全部运行参数
- 测试报告中必须写明：
  - Ollama 基线模型名
  - llama.cpp 模型文件名
  - quant 规格
  - ctx size
  - threads
  - gpu layers

## 6. 最终建议

### 短期建议

- 直接将主模型和小模型都切向 `llama.cpp`
- 将 Ollama 仅保留为旧方案性能基线
- 用新增 benchmark 脚本先拿到“旧基线 vs 新方案”的真实对比报告

### 中期建议

- 下线 `/api/tags` 与 `ollama pull` 运维流程
- 形成统一的 GGUF 资产管理体系
- 清理 Admin 与运维脚本中的 Ollama 兼容入口

### 长期建议

- 完成 Ollama 完全退场
- 所有本地模型统一通过 `llama.cpp` 管理
- 保留一份基准脚本作为版本升级回归基线
