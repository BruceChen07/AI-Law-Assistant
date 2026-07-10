# Phase 08 - 双 llama.cpp 真机推理联调实施记录

## 1. 阶段目标

- 基于仓库内 `llama.cpp` 二进制拉起双实例
- 验证主模型与小模型的 `/v1/models` 和最小对话请求
- 验证真实项目配置已指向双 `llama.cpp`
- 修复真机联调中暴露出的 `reasoning_content` 兼容问题

## 2. 启动结果

本次成功拉起两个本地 `llama.cpp` 实例：

- 主模型：
  - 端口：`18080`
  - 模型：`Qwen3.6-35B-A3B-UD-Q4_K_M.gguf`
  - `ctx_size`: `4096`
- 小模型：
  - 端口：`18081`
  - 模型：`Qwen3.6-27B-Q4_0.gguf`
  - `ctx_size`: `2048`

启动日志确认：

- `llama_server: listening on http://127.0.0.1:18080`
- `llama_server: listening on http://127.0.0.1:18081`

## 3. 运行时联调结果

### 3.1 运行时校验脚本

执行：

```bash
python .\bin\validate-local-llamacpp-runtime.py --config-path .\app\config.json
```

生成报告：

- `plan/local-llm-llamacpp/reports/runtime-validation-20260710-010647.json`

结果：

- `llama.cpp main /v1/models`：通过
- `llama.cpp small /v1/models`：通过
- `llama.cpp smoke chat`：通过
- `backend /health`：未通过

说明：

- 后端未通过不是 `llama.cpp` 问题
- 根因是本机 Python 环境缺少 `fastapi`

### 3.2 真实推理耗时

直接打到双实例的真实请求结果：

- 主模型请求耗时：
  - 约 `17.44s`
- 小模型请求耗时：
  - 约 `24.04s`

再次复测得到：

- 主模型：
  - 约 `10.36s`
- 小模型：
  - 约 `21.16s`

说明：

- 当前 CPU 模式下两路实例已具备稳定推理能力
- 小模型在这组参数下并不比主模型更快，后续仍需继续调参

## 4. 真机联调中发现的问题

### 4.1 现象

`llama.cpp` 返回 HTTP 200，但 `message.content` 为空字符串。

同时响应中存在：

- `message.reasoning_content`

### 4.2 根因

当前 `Qwen3.6 + llama.cpp` 组合在现有模板/参数下，会把主要输出放到
`reasoning_content`，而项目的 OpenAI-compatible 路径原先只读取 `content`。

因此业务侧会把一次成功推理误判为“空响应”。

### 4.3 本次修复

文件：`app/core/llm.py`

修复内容：

- 在 `_chat_via_openai_compatible()` 中：
  - 读取 `reasoning_content`
  - 当 `provider == llama_cpp` 且 `content` 为空时
  - 自动回退使用 `reasoning_content` 作为返回内容
- 同时保留：
  - `message.reasoning_content`
  - `_used_reasoning_content_fallback`

### 4.4 当前修复后的效果

通过本地调用 `LLMService` 验证，已确认：

- 不再返回空字符串
- `raw['_used_reasoning_content_fallback'] == true`

## 5. 本阶段结论

本阶段已经完成：

1. 双 `llama.cpp` 实例真机拉起
2. 主/小模型本地服务探活通过
3. 最小推理请求通过
4. 项目链路中“空 content”问题已补兼容修复

当前仍未打通的部分：

- 后端 FastAPI 进程启动
- 基于完整应用接口的端到端联调

根因属于环境依赖缺失，而不是 `llama.cpp` 运行链路失败。
