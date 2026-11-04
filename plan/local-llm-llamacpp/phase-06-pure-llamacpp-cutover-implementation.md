# Phase 06 - 纯 llama.cpp 切换实施记录

## 1. 阶段目标

- 将 `E:\models` 下的 GGUF 大模型文件完整迁移到当前项目内
- 将项目实际运行配置从 Ollama 切换为双 `llama.cpp`
- 将运行时启动、配置下发、联调验证工具调整为不依赖 Ollama
- 约束 Admin 配置入口，避免继续选择云端或 Ollama 作为当前主路径

## 2. 模型资产迁移

### 2.1 源目录

- `E:\models`

### 2.2 迁移目标目录

- `E:\workspace\AI-Law-Assistant\models\llm`

### 2.3 已复制文件

- `Qwen3.6-35B-A3B-UD-Q4_K_M.gguf`
- `Qwen3.6-27B-Q4_0.gguf`
- `gemma-4-26B-A4B-it-UD-Q4_K_M.gguf`

### 2.4 迁移说明

本次采用完整复制而非链接方式，保证当前项目目录具备独立的本地模型资产副本。

## 3. 本阶段代码改动

### 3.1 双 llama.cpp 配置下发

文件：`bin/apply-local-llm-config.py`

调整内容：

- `small_provider` 默认由 `ollama` 改为 `llama_cpp`
- 新增小模型独立 host 参数：
  - `--small-llama-cpp-host`
- 默认小模型切为：
  - `Qwen3.6-27B-Q4_0.gguf`
- 下发后的默认运行拓扑：
  - 主模型：`http://127.0.0.1:18080/v1`
  - 小模型：`http://127.0.0.1:18081/v1`

### 3.2 新增双实例启动脚本

文件：`bin/start-local-llamacpp-stack.py`

能力：

- 串行启动主模型与小模型两个 `llama.cpp` 实例
- 不依赖 Ollama
- 默认加载项目内 `models/llm/` 下的两个 GGUF

默认模型：

- 主模型：`Qwen3.6-35B-A3B-UD-Q4_K_M.gguf`
- 小模型：`Qwen3.6-27B-Q4_0.gguf`

### 3.3 纯 llama.cpp 运行校验

文件：`bin/validate-local-llamacpp-runtime.py`

调整内容：

- 配置检查新增：
  - `local_llm.small_model.provider == llama_cpp`
- 服务探活从：
  - `llama.cpp + Ollama`
  调整为：
  - `llama.cpp main + llama.cpp small`

### 3.4 示例配置切换

文件：

- `app/config.example.json`
- `app/config.local-llamacpp.example.json`

调整内容：

- 小模型示例不再指向 Ollama
- 全部改为 `llama_cpp`
- 小模型默认端口改为 `18081`

### 3.5 Admin 管理页约束

文件：`web/Admin.jsx`

调整内容：

- 模型页不再自动加载 Ollama 模型列表
- 默认 provider 回落改为 `llama_cpp`
- 模型 provider 下拉仅保留 `llama_cpp`
- 旧 Ollama 管理区块默认隐藏

## 4. 实际运行配置切换

已对真实项目配置执行：

```bash
python .\bin\apply-local-llm-config.py --provider llama_cpp --small-provider llama_cpp --main-model Qwen3.6-35B-A3B-UD-Q4_K_M.gguf --small-model Qwen3.6-27B-Q4_0.gguf --llama-cpp-host http://127.0.0.1:18080 --small-llama-cpp-host http://127.0.0.1:18081
```

执行后：

- `app/config.json` 主模型已切为 `llama_cpp`
- `local_llm.main_model` 已切为 `llama_cpp`
- `local_llm.small_model` 已切为 `llama_cpp`

## 5. 本阶段结论

当前仓库在“代码配置层面”已完成纯 `llama.cpp` 切换。

当前唯一阻塞真机功能测试的外部条件是：

- 本机尚无可执行的 `llama-server` 二进制路径可用

因此，当前未通过的功能测试属于环境阻塞，而非代码仍依赖 Ollama。
