# Debug Session: ollama-local-llm

Status: OPEN

## Symptom
- 本地大模型测试无响应
- 目标是接入本地 Ollama 已下载模型的自动识别与用户可选模型能力

## Initial Hypotheses
- H1: Ollama 服务未启动，或监听端口与当前配置不一致，导致测试请求未真正发出到本地推理服务。
- H2: 后端当前仍按 OpenAI-compatible 固定地址调用，未正确适配 Ollama 原生接口或模型名，导致请求卡死或异常被前端吞掉。
- H3: 管理页“测试配置”接口在写入/读取本地文件或目录时命中了错误路径权限，截图中的 `Access is denied: 'E:\workspace\data'` 可能是测试失败的直接原因，而不是模型本身无响应。
- H4: 前端管理页缺少对 Ollama 模型列表的拉取与错误态展示逻辑，导致用户看到的是“无响应”而不是明确错误。
- H5: 依赖版本或超时配置不兼容，导致 Ollama 请求建立后没有被正确解析或在超时前未返回可见结果。

## Evidence Plan
- 检查本地 Ollama 进程、监听端口和 API 可达性。
- 检查后端 LLM 配置、测试接口、Ollama 适配代码路径。
- 复现管理页测试请求，抓取前后端日志和异常栈。
- 验证当前项目是否已有 Ollama 专用配置入口与模型列表接口。

## Notes
- 在获得运行证据前，不修改业务逻辑。

## Checkpoint 2026-07-02
- 继续沿用 `ollama-local-llm` 调试会话，优先补齐真实运行态验证与测试冲突定位。
- 新增待验证假设：
  - H3a: `tests/test_admin_ollama_config.py` 中重复登录生成相同 JWT，触发 `sessions.token` 唯一约束，影响后续联调与自动化回归。
  - H4a: 前端已经具备模型识别与切换 UI，但需要通过真实接口验证错误提示、缓存状态与默认模型记忆是否完整闭环。
  - H5a: 当前 `POST /api/admin/llm-test` 在 Ollama 正常场景下可能已恢复，但仍需通过真实服务确认响应稳定性。
- 下一步：
  - 复查认证/会话逻辑与测试代码，确认 `sessions.token` 冲突根因。
  - 启动最新后端并验证 `/api/admin/ollama/models`、`/api/admin/llm-test`。
  - 根据证据决定是否需要最小代码修复。
