# Phase 03 - llama.cpp 本地切换运行手册

## 1. 目标

本手册用于指导将本项目主模型从 Ollama 运行时切换到端侧本地 `llama.cpp / llama-server`。

约束：

- 仅允许端侧本地大模型
- 不使用云端 OpenAI / Qwen / Wenxin 作为默认路径或回退路径
- 所有推理服务必须落在本机或企业内网

## 2. 目标拓扑

- 主模型：`http://127.0.0.1:18080/v1`
- 主模型 provider：`llama_cpp`
- 推荐主模型：`Qwen3.6-35B-A3B-UD-Q4_K_M.gguf`
- 小模型：`http://127.0.0.1:11434/v1`
- 小模型 provider：`ollama`
- 推荐小模型：`qwen3:4b`
- 后端：`http://127.0.0.1:8000`
- 前端：`http://127.0.0.1:5173`

## 3. 前置条件

### 3.1 硬件建议

- CPU：16 物理核及以上更稳妥
- 内存：64GB 起步，推荐 96GB 或以上
- 存储：NVMe SSD

### 3.2 软件前置

- 已具备本仓库 Python 运行环境
- 已具备前端 Node.js 环境
- 已在本机编译或放置 `llama-server`
- 已在本机落盘 GGUF 模型文件
- 迁移期如需保留小模型，需同时安装 Ollama

### 3.3 模型资产

推荐目录：

```text
models/
  llm/
    Qwen3.6-35B-A3B-UD-Q4_K_M.gguf
```

## 4. 启动步骤

### 4.1 启动 llama.cpp 主模型

执行：

```bash
python .\bin\start-local-llamacpp-server.py
```

常用参数示例：

```bash
python .\bin\start-local-llamacpp-server.py ^
  --model-path .\models\llm\Qwen3.6-35B-A3B-UD-Q4_K_M.gguf ^
  --alias Qwen3.6-35B-A3B-UD-Q4_K_M.gguf ^
  --ctx-size 8192 ^
  --threads 20 ^
  --threads-http 2 ^
  --batch-size 1024 ^
  --ubatch-size 512 ^
  --parallel 1
```

日志文件：

- `logs/local-llm/llama-cpp-server.log`

### 4.2 如需保留小模型，启动 Ollama

执行：

```bash
python .\bin\start-local-llm-servers.py
```

### 4.3 应用本地切换配置

执行：

```bash
python .\bin\apply-local-llm-config.py --provider llama_cpp --small-provider ollama
```

如仅验证主模型切换，可加自定义模型名：

```bash
python .\bin\apply-local-llm-config.py ^
  --provider llama_cpp ^
  --main-model Qwen3.6-35B-A3B-UD-Q4_K_M.gguf ^
  --small-provider ollama ^
  --small-model qwen3:4b
```

## 5. 配置核对

核对 `app/config.json` 至少满足：

```json
{
  "llm_config": {
    "provider": "llama_cpp",
    "api_base": "http://127.0.0.1:18080/v1",
    "api_key": "",
    "model": "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
  },
  "local_llm": {
    "enabled": true,
    "main_model": {
      "provider": "llama_cpp",
      "api_base": "http://127.0.0.1:18080/v1",
      "api_key": "",
      "model": "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
    },
    "small_model": {
      "provider": "ollama",
      "api_base": "http://127.0.0.1:11434/v1",
      "api_key": "",
      "model": "qwen3:4b"
    }
  },
  "network_policy": {
    "enabled": true,
    "mode": "offline_strict"
  }
}
```

核对点：

- `llm_config.api_key` 必须为空
- `local_llm.main_model.provider` 必须为 `llama_cpp`
- `network_policy.allowed_hosts` 包含 `127.0.0.1`、`localhost`、`llamacpp.intra`、`ollama.intra`

## 6. 管理后台验证

进入 Admin -> 模型配置，执行以下检查：

1. 刷新 `llama.cpp` 模型列表
2. 确认 `/v1/models` 可返回本地模型别名
3. 应用目标主模型
4. 执行 `llm-test`
5. 确认错误提示符合以下场景：
   - 服务不可达
   - 模型未加载
   - 响应超时

## 7. 回归与验证

### 7.1 当前仓库内已完成的最低验证

- Python 文件语法编译通过
- Admin 前端构建通过

### 7.2 环境补齐后建议执行

```bash
python -m pytest tests/test_admin_ollama_config.py -q
python -m pytest tests/test_llm_local_mode.py -q
```

### 7.3 真机烟测

建议至少执行：

- Admin `llm-test`
- 首页合同审计一次
- 含较长上下文的合同审计一次
- 检查日志中无云端 URL、无公网主机名

## 8. 性能观察项

重点记录：

- 模型加载耗时
- 首 token 延迟
- 整体响应时长
- CPU 占用
- 内存峰值
- 长文档场景下的超时次数

建议将结果追加到阶段测试报告中。

## 9. 回退方案

如主模型切换后不稳定，可保守回退到本地 Ollama 主模型：

```bash
python .\bin\apply-local-llm-config.py --provider ollama --small-provider ollama
```

说明：

- 回退目标仍为端侧本地模型
- 不允许回退到云端 provider

## 10. 关联文档

- `plan/local-llm-llamacpp/phase-01-bootstrap-implementation.md`
- `plan/local-llm-llamacpp/phase-02-admin-integration-implementation.md`
- `plan/local-llm-llamacpp/phase-01-02-test-report.md`
