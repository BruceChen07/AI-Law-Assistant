# Phase 09 - 应用层联调实施记录

## 1. 阶段目标

- 在纯 `llama.cpp` 双实例已经就绪的基础上，继续打通 FastAPI 应用层。
- 补齐当前 Python 运行环境中阻塞后端启动的缺失依赖。
- 验证后端 `/health`、Admin 配置接口、`llama.cpp` 模型发现接口以及真实的 `llm-test` 调用链路。
- 将联调结果沉淀为本地可追溯记录，作为后续合同审计端到端验证的基线。

## 2. 本阶段实施内容

### 2.1 清理历史残留进程并重新拉起仓库内双实例

联调开始时，系统内仍存在两个历史 `llama-server.exe` 进程：

- `E:\Tool\llama-b9940-bin-win-cpu-x64\llama-server.exe --port 8080`
- `E:\Tool\llama-b9940-bin-win-cpu-x64\llama-server.exe --port 8167`

它们并不是当前仓库约定的 `18080/18081` 双实例，且目标端口未实际监听，容易污染联调结论。因此本阶段先执行：

1. 终止历史残留 `llama-server.exe`
2. 使用仓库内二进制重新拉起双实例

执行命令：

```powershell
python .\bin\start-local-llamacpp-stack.py `
  --server-exe .\third_party\llama.cpp\bin-win-cpu-x64\llama-server.exe `
  --main-threads 14 `
  --small-threads 14 `
  --main-ctx-size 8192 `
  --small-ctx-size 4096 `
  --wait-seconds 240
```

结果：

- 主模型实例监听 `127.0.0.1:18080`
- 小模型实例监听 `127.0.0.1:18081`

### 2.2 运行时校验回归

在应用层联调前，重新执行运行时校验脚本：

```powershell
python .\bin\validate-local-llamacpp-runtime.py
```

第一次结果：

- `llama.cpp main /v1/models`: 通过
- `llama.cpp small /v1/models`: 通过
- `llama.cpp smoke chat`: 通过
- `backend /health`: 未通过

这一步确认问题已从运行时层面收敛到应用层依赖环境，而非 `llama.cpp` 服务本身异常。

### 2.3 补齐后端启动依赖

后端第一次启动时，沿 import 链先后暴露出以下缺失依赖：

- `fastapi`
- `structlog`
- `python-multipart`
- `email-validator`
- `passlib`
- `pyjwt`
- `jieba`
- `python-docx`

后续为了补齐安全存储和预览链路，又补装：

- `keyring`
- `pdf2image`

本阶段实际执行的安装动作包括：

```powershell
python -m pip install fastapi==0.115.0 structlog python-multipart==0.0.6 email-validator==2.1.0.post1 passlib==1.7.4 pyjwt==2.8.0
python -m pip install jieba==0.42.1
python -m pip install "python-docx>=1.2.0,<2" "pypdf>=5.6.0"
python -m pip install "keyring>=25.6.0" "pdf2image==1.17.0"
```

### 2.4 启动 FastAPI 应用

执行：

```powershell
python -m app.main
```

最终后端成功启动，关键信号如下：

- `Application startup complete.`
- `Uvicorn running on http://0.0.0.0:8000`

启动期仍保留一项非阻塞告警：

- `reranker:en` 资产未落盘，仅影响可选 reranker 路径，不阻塞当前主链路联调。

### 2.5 验证 Admin 鉴权接口与 llama.cpp 配置链路

由于 Admin 接口需要 Bearer Token，本阶段使用本地数据库中的管理员用户生成测试 JWT，仅用于本机联调验证。

验证结果：

- 未带 Token 访问 `/api/admin/llm-config` -> `401 Unauthorized`
- 带管理员 Token 后：
  - `/api/admin/llm-config` -> `200`
  - `/api/admin/llama-cpp/models` -> `200`

说明：

- Admin 鉴权链路正常
- 后端已正确读取当前 `llama_cpp` 配置
- `llama.cpp` 模型发现接口已能够通过应用层正常访问本地 `18080` 实例

### 2.6 验证真实 `llm-test` 调用链

本阶段对 `/api/admin/llm-test` 发起真实调用，请求提示词为：

```text
请回复：llama_cpp admin test ok
```

后端日志记录到：

- `admin_llm_test_start`
- `llm_request_start`
- `llm_request_done`
- `admin_llm_test_done`

关键指标：

- 模型：`Qwen3.6-35B-A3B-UD-Q4_K_M.gguf`
- 输入 token 估算：`14`
- `prompt_tokens`: `29`
- `completion_tokens`: `517`
- `total_tokens`: `546`
- `latency_ms`: `68110`
- `answer_len`: `193`

这表明应用层已经能够完整穿透：

`Admin API -> LLMService -> llama.cpp(OpenAI-compatible) -> 返回结果 -> 日志落盘`

## 3. 本阶段输出

- 成功启动 FastAPI 后端
- 新增运行时通过报告：
  - `plan/local-llm-llamacpp/reports/runtime-validation-20260710-013241.json`
- 生成应用层联调日志：
  - `logs/2026-07-10/main.log`

## 4. 当前剩余问题

### 4.1 reranker 资产未落盘

当前启动日志仍提示：

```text
reranker assets must be staged from internal artifact repository
```

状态判断：

- 非主链路阻塞项
- 不影响本阶段 `llama.cpp` 双实例与应用层主链路联调
- 会影响后续涉及 reranker 的完整检索增强路径

### 4.2 `jieba` 的 `pkg_resources` 弃用告警

当前仅为第三方库告警，不影响系统功能，但后续需要结合依赖版本规划处理。

## 5. 阶段结论

本阶段已经完成从“运行时联调通过”到“应用层联调通过”的推进，确认以下事项：

1. 仓库内双 `llama.cpp` 实例可稳定提供本地服务
2. 后端已可在当前环境中成功启动
3. `/health`、Admin 配置接口、`llama.cpp` 模型发现接口均可正常访问
4. 真实 `llm-test` 已能穿透完整应用调用链

下一步可以继续进入：

- 合同审计接口的最小端到端验证
- reranker 资产补齐与检索增强链路回归
- 纯 `llama.cpp` 路径下的正式性能基线与业务场景 benchmark
