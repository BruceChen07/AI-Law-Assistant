# Phase 02 - Admin 管理面与后端接入实施记录

## 1. 阶段目标

- 将 `llama.cpp` 纳入现有 Admin 管理能力，支持模型发现、配置保存、模型测试与本地切换。
- 保持与 Ollama 并存，支撑迁移过渡期双栈运行。
- 优先复用当前 `LLMService` 的 OpenAI-compatible 路径，不重写业务调用链。

## 2. 本阶段代码变更

### 2.1 后端 Admin 接口扩展

文件：`app/api/routers/admin.py`

新增能力：

- 新增 `DEFAULT_LLAMACPP_HOST = "http://127.0.0.1:18080"`
- 新增 `LLAMACPP_CACHE_TTL_SEC` 与 `_LLAMACPP_MODELS_CACHE`
- 新增响应模型：
  - `LlamaCppModelItem`
  - `LlamaCppModelListResponse`
- 新增工具函数：
  - `_normalize_openai_host(...)`
  - `_extract_llamacpp_models(...)`
  - `_get_cached_llamacpp_models(...)`
  - `_friendly_llm_test_error(...)`
- 新增接口：
  - `GET /api/admin/llama-cpp/models`

接口行为：

- 从 `http://127.0.0.1:18080/v1/models` 发现模型
- 支持缓存、强制刷新、过期降级缓存
- 返回当前模型与推荐模型信息
- `provider == "llama_cpp"` 时，`api_key` 自动置空

### 2.2 LLM 测试友好错误

文件：`app/api/routers/admin.py`

增强内容：

- `POST /api/admin/llm-test` 新增 `llama_cpp` 友好错误提示：
  - 服务不可达
  - 目标模型未加载
  - 响应超时
- 同时保留 Ollama 友好提示
- 错误信息强调“请确认当前配置指向本地端侧模型服务”

### 2.3 前端 API 封装

文件：`web/api/admin.js`

新增：

- `adminGetLlamaCppModels(params)`

### 2.4 Admin 前端管理面

文件：`web/Admin.jsx`

新增/调整：

- 新增 `llama.cpp` 模型列表状态、缓存元信息、搜索关键字
- 新增本地记忆：
  - `admin.selectedLlamaCppModel`
- 模型页初始化时同时加载：
  - `loadOllamaModels()`
  - `loadLlamaCppModels()`
- 支持从模型发现结果中直接切换到 `llama_cpp`
- Provider 下拉增加 `llama_cpp`
- `provider` 为 `ollama` / `llama_cpp` 时禁用 API Key 输入

## 3. 技术决策说明

### 3.1 为什么复用 OpenAI-compatible 路径

`app/core/llm.py` 已具备：

- `provider == "ollama"` 走 Ollama 原生接口
- 其他 provider 走 OpenAI-compatible `/v1/chat/completions`

因此本阶段不新增新的业务调用分支，而是让 `llama_cpp` 直接复用现有 OpenAI-compatible 通道，降低改造面与回归风险。

### 3.2 为什么保留 Ollama 双栈

原因：

- 小模型路由当前已在业务侧稳定使用
- 迁移阶段可保持主模型优先切换，降低一次性替换风险
- 便于做性能、稳定性与兼容性对比

## 4. BUG 修复记录

### 4.1 Admin 无法识别本地 llama.cpp 模型

处理：

- 新增 `/api/admin/llama-cpp/models`
- 增加缓存与刷新逻辑

### 4.2 `llm-test` 对 llama.cpp 缺少友好错误

处理：

- 新增 `_friendly_llm_test_error(...)`
- 对不可达、超时、模型未加载分别给出面向运维的提示

### 4.3 前端仅偏向 Ollama 管理

处理：

- 在 `web/Admin.jsx` 增加 `llama.cpp` 独立模型面板与切换入口

## 5. 与端侧本地部署要求的关系

本阶段所有新增能力均围绕本地端侧模型：

- `llama.cpp` 仅指向本地 `127.0.0.1:18080`
- 不新增云端 provider 的默认切换逻辑
- 不要求任何云端密钥
- 新提示文案引导运维优先检查本地 `llama-server`
