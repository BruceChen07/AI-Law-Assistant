# Phase 01 - llama.cpp 本地运行底座实施记录

## 1. 阶段目标

- 为 `feature/local-llm-llamacpp` 分支建立可落地的端侧 `llama.cpp` 启动与配置入口。
- 保持现有 Ollama 路径可回退，支持“主模型走 `llama.cpp`、小模型暂留 Ollama”的过渡方案。
- 明确保证仅接入端侧本地大模型，不引入任何云端模型路由。

## 2. 本阶段代码变更

### 2.1 新增本地启动脚本

文件：`bin/start-local-llamacpp-server.py`

实现内容：

- 新增 `llama-server` 启动脚本，默认目标地址为 `http://127.0.0.1:18080`
- 默认目标模型为 `Qwen3.6-35B-A3B-UD-Q4_K_M.gguf`
- 启动前校验：
  - `llama-server` 可执行文件是否存在
  - GGUF 文件是否已落盘
  - 服务地址是否仍为 loopback，仅允许 `127.0.0.1 / localhost / ::1`
- 启动参数支持：
  - `--ctx-size`
  - `--threads`
  - `--threads-http`
  - `--batch-size`
  - `--ubatch-size`
  - `--gpu-layers`
  - `--parallel`
  - `--no-mmap`
- 启动日志输出至 `logs/local-llm/llama-cpp-server.log`

### 2.2 扩展本地配置应用脚本

文件：`bin/apply-local-llm-config.py`

实现内容：

- 从“固定 Ollama 模式”扩展为“按 provider 生成本地配置”
- 新增参数：
  - `--provider`
  - `--small-provider`
  - `--ollama-host`
  - `--llama-cpp-host`
  - `--main-model`
  - `--small-model`
- 默认策略：
  - 主模型：`llama_cpp`
  - 小模型：`ollama`
- 写入配置时同步更新：
  - `llm_config`
  - `local_llm`
  - `network_policy`
- `network_policy.allowed_hosts` 新增 `llamacpp.intra`

### 2.3 新增配置模板

文件：`app/config.local-llamacpp.example.json`

用途：

- 提供一份最小可用的 `llama.cpp + Ollama` 混合本地配置参考
- 明确 `llm_config` 与 `local_llm.main_model` 均指向 `http://127.0.0.1:18080/v1`
- 保留 `local_llm.small_model` 指向 `http://127.0.0.1:11434/v1`

## 3. 技术选型调整

本阶段确认以下技术路线：

- 主模型服务形态：`llama.cpp / llama-server`
- 接口协议：OpenAI-compatible `/v1`
- 模型格式：GGUF
- 过渡阶段小模型：继续保留 Ollama
- 网络策略：`offline_strict`

未采用方案：

- 不接入任何云端 OpenAI/Qwen/Wenxin 路由
- 不将 `llama.cpp` 直接嵌入业务进程内调用
- 不改坏既有 `bin/start-local-llm-servers.py` 的 Ollama 启动路径

## 4. 端侧本地模型保证

本阶段通过以下措施确保仅使用端侧本地大模型：

- `start-local-llamacpp-server.py` 强制 loopback 地址，不允许云端 URL
- `apply-local-llm-config.py` 仅生成本地 `127.0.0.1`/内网地址配置
- `network_policy.mode = offline_strict`
- 未新增任何公网域名白名单

## 5. 风险与后续

- 当前脚本假定环境中已具备 `llama-server` 可执行文件与 GGUF 模型文件
- 下一阶段需补齐 Admin 管理面与模型发现、错误提示、模型切换能力
