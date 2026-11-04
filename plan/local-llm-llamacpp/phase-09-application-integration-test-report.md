# Phase 09 - 应用层联调测试报告

## 1. 测试目标

- 验证纯 `llama.cpp` 双实例在仓库内二进制模式下仍稳定可用。
- 验证 FastAPI 后端能够在当前 Python 环境中完成启动。
- 验证应用层关键接口：
  - `/health`
  - `/api/admin/llm-config`
  - `/api/admin/llama-cpp/models`
  - `/api/admin/llm-test`
- 回归 `LLMService` 本地模式相关单元测试。

## 2. 测试环境

- 操作系统：Windows
- 仓库目录：`E:\workspace\AI-Law-Assistant`
- 本地推理运行时：仓库内 `third_party/llama.cpp/bin-win-cpu-x64/llama-server.exe`
- 主模型：
  - `Qwen3.6-35B-A3B-UD-Q4_K_M.gguf`
- 小模型：
  - `Qwen3.6-27B-Q4_0.gguf`
- 主端口：`18080`
- 小端口：`18081`
- 后端端口：`8000`

## 3. 测试步骤与结果

### 3.1 双实例监听检查

检查结果：

- `127.0.0.1:18080` -> Listen
- `127.0.0.1:18081` -> Listen

结论：通过

### 3.2 自动化运行时校验

执行：

```powershell
python .\bin\validate-local-llamacpp-runtime.py
```

第一次报告：

- `plan/local-llm-llamacpp/reports/runtime-validation-20260710-012903.json`
- 结果：`ok=False`
- 原因：后端 `/health` 尚未启动

后端拉起后再次执行：

```powershell
python .\bin\validate-local-llamacpp-runtime.py
```

第二次报告：

- `plan/local-llm-llamacpp/reports/runtime-validation-20260710-013241.json`
- 结果：`ok=True`

检查项结果：

- `llama.cpp main /v1/models` -> 通过
- `llama.cpp small /v1/models` -> 通过
- `backend /health` -> 通过
- `llama.cpp smoke chat` -> 通过

结论：通过

### 3.3 FastAPI 启动验证

执行：

```powershell
python -m app.main
```

结果：

- 启动成功
- `Application startup complete`
- `Uvicorn running on http://0.0.0.0:8000`

结论：通过

### 3.4 `/health` 健康检查

请求结果：

```json
{"status":"ok","embedding_ready":true,"embedding_default_language":"zh","embedding_languages":["zh","en"]}
```

结论：通过

### 3.5 Admin 鉴权与配置接口验证

无 Token 请求：

- `/api/admin/llm-config` -> `401 Unauthorized`
- `/api/admin/llama-cpp/models` -> `401 Unauthorized`

带管理员 Token 请求：

- `/api/admin/llm-config` -> `200`
- `/api/admin/llama-cpp/models` -> `200`

其中 `/api/admin/llm-config` 返回：

- `provider = llama_cpp`
- `api_base = http://127.0.0.1:18080/v1`
- `model = Qwen3.6-35B-A3B-UD-Q4_K_M.gguf`

`/api/admin/llama-cpp/models` 返回：

- `reachable = true`
- `model_count = 1`
- `current_model = Qwen3.6-35B-A3B-UD-Q4_K_M.gguf`

结论：通过

### 3.6 真实 `llm-test` 调用

请求提示词：

```text
请回复：llama_cpp admin test ok
```

后端日志结果：

- `admin_llm_test_start`
- `llm_request_start`
- `llm_request_done`
- `admin_llm_test_done`

关键指标：

- `prompt_tokens = 29`
- `completion_tokens = 517`
- `total_tokens = 546`
- `latency_ms = 68110`
- `answer_len = 193`

结论：通过

说明：

- 该请求耗时较长，但已经完整走通应用链路。
- 当前 `max_tokens = 2048`，Admin 测试场景下模型生成偏长，后续可以考虑单独收紧测试提示词或测试 token 上限。

### 3.7 单元测试回归

执行：

```powershell
python -m unittest tests.test_llm_local_mode
```

结果：

- `Ran 6 tests`
- `OK`

重点覆盖：

- 本地模型主/小模型路由
- Ollama 兼容回退路径的既有测试
- `llama_cpp` 的 `reasoning_content` fallback

结论：通过

## 4. 发现的问题

### 问题 1：后端依赖最初不完整

表现：

- 启动初期连续出现 `ModuleNotFoundError`

本阶段已补齐：

- `fastapi`
- `structlog`
- `python-multipart`
- `email-validator`
- `passlib`
- `pyjwt`
- `jieba`
- `python-docx`
- `keyring`
- `pdf2image`

状态：已解决

### 问题 2：reranker 资产未落盘

表现：

- 启动日志提示 `reranker:en` 资产缺失

影响：

- 不阻塞本阶段主链路
- 会影响后续完整检索增强能力验证

状态：未解决，留待后续阶段处理

### 问题 3：第三方 `jieba` 依赖存在弃用告警

表现：

- `pkg_resources is deprecated`

影响：

- 当前不影响功能

状态：已记录，留待后续依赖治理

## 5. 测试结论

本阶段测试结论为：

- 纯 `llama.cpp` 双实例：通过
- FastAPI 启动：通过
- 健康检查：通过
- Admin 配置与模型发现：通过
- 真实 `llm-test`：通过
- 本地 LLM 单元测试回归：通过

整体结论：

**Phase 09 应用层联调通过，可以继续推进业务接口级端到端验证。**
