# AI Law Assistant 服务运行与使用手册

## 1. 文档适用范围

本文档基于当前代码仓库、`app/config.json`、启动脚本、接口定义和本机资产盘点整理，目标是回答三件事：

1. 当前服务栈实际使用了哪些模型，这些模型分别做什么。
2. 如何从零启动完整服务，包括依赖、步骤、参数和排障。
3. 服务上线后如何使用、运维和定位故障。

## 2. 当前运行态结论

截至本次核查，本机**未发现业务服务处于运行状态**：

- 未发现 FastAPI/Uvicorn 监听 `8000`
- 未发现 `llama-server` 监听 `18080` / `18081`
- 未发现前端开发服务监听 `5173`

因此，下面内容分为两层：

1. **运行态事实**：当前没有业务服务在跑。
2. **配置态/代码态事实**：系统设计上应运行的完整服务栈、模型和调用链路。

## 3. 目标服务拓扑

标准离线部署形态如下：

1. `llama.cpp main runtime`
   - 端口：`18080`
   - 作用：主模型推理，承担高质量审计与高风险判定
2. `llama.cpp small runtime`
   - 端口：`18081`
   - 作用：小模型推理，承担轻量任务与局部路由任务
3. `FastAPI backend`
   - 默认端口：`8000`，若占用可自动顺延
   - 作用：统一 API、业务编排、模型路由、鉴权、追踪、导出
4. `React + Vite frontend`
   - 默认端口：`5173`
   - 作用：用户界面、Admin 管理页、合同审计页、登录页

## 4. 模型总览

### 4.1 已配置且应作为主路径使用的模型

| 模型类型 | 模型名 | 本地路径 | 规模/大小 | 当前状态 | 主要用途 |
| --- | --- | --- | --- | --- | --- |
| 主 LLM | `Qwen3.6-35B-A3B-UD-Q4_K_M.gguf` | `models/llm/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf` | 约 `21109.1 MB` | 已落盘，配置为主模型 | 合同审计主分析、复杂推理、高风险税审判定 |
| 小 LLM | `Qwen3.6-27B-Q4_0.gguf` | `models/llm/Qwen3.6-27B-Q4_0.gguf` | 约 `15312.6 MB` | 已落盘，配置为小模型 | 轻量分类、匹配、实体抽取、部分回退前置 |
| 中文 Embedding | `BAAI/bge-small-zh-v1.5` | `models/embedding/zh/model.onnx` | 约 `91.5 MB` | 已落盘 | 中文法规/合同语义向量化 |
| 英文 Embedding | `BAAI/bge-small-en-v1.5` | `models/embedding/en/model.onnx` | 约 `127.5 MB` | 已落盘 | 英文查询或跨语场景的语义向量化 |

### 4.2 已存在资产但未接入当前主路径的模型

| 模型类型 | 模型名 | 本地路径 | 大小 | 状态 | 说明 |
| --- | --- | --- | --- | --- | --- |
| 备用 LLM | `gemma-4-26B-A4B-it-UD-Q4_K_M.gguf` | `models/llm/gemma-4-26B-A4B-it-UD-Q4_K_M.gguf` | 约 `16162.4 MB` | 仅落盘，未在配置中启用 | 可作为后续替换或 AB 测试候选 |

### 4.3 已配置但当前环境未就绪的模型

| 模型类型 | 配置项 | 目标路径 | 当前状态 | 风险 |
| --- | --- | --- | --- | --- |
| Reranker | `reranker_profiles.en` | `models/reranker/en` | 路径不存在 | 英文重排不可用，语义检索会退化为召回排序 |
| Translation | `translation_config.model_dir` | `models/translation/tencent__HY-MT1.5-1.8B` | 路径不存在，且配置为 `enabled=false` | 翻译增强检索当前不可启用 |

补充说明：

- `models/reranker/zh` 目录存在，但当前盘点未发现有效模型文件；同时 `app/config.json` 也没有为 `zh` 配置 reranker profile。
- 因此，**当前环境不应宣称 reranker/translation 已就绪**。

## 5. 模型用途、调用逻辑与业务场景

### 5.1 主模型：`Qwen3.6-35B-A3B-UD-Q4_K_M.gguf`

**用途**

- 合同审计主推理
- 合同条款风险判定
- 税审中的高风险生成与最终判断
- 小模型失败或结果不合格时的回退目标

**配置参数**

- `provider`: `llama_cpp`
- `api_base`: `http://127.0.0.1:18080/v1`
- `model`: `Qwen3.6-35B-A3B-UD-Q4_K_M.gguf`
- `temperature`: `0.2`
- `max_tokens`: `2048`
- `timeout`: `600`

**调用逻辑**

- `llm_config` 默认即指向主模型。
- `local_llm.main_model` 也指向该模型。
- `task_profile` 为下列值时会优先路由到主模型：
  - `default`
  - `contract_audit_main`
  - `contract_audit_memory`
  - `contract_clause_audit`
  - `tax_risk_main`
- 当小模型路由失败且 `allow_small_to_main_fallback=true` 时，会自动回退到主模型。

**业务场景**

- 合同上传后，合同正文抽取、法规证据拼接完成后，最终风险识别由主模型承担。
- 税审流程中，风险问题生成属于主模型任务。
- Memory 审计路径开启时，复杂条款或高优先级场景仍走主模型。

### 5.2 小模型：`Qwen3.6-27B-Q4_0.gguf`

**用途**

- 轻量化分类与匹配
- 税审合同条款与规则初配
- 实体抽取
- 低成本辅助推理

**配置参数**

- `provider`: `llama_cpp`
- `api_base`: `http://127.0.0.1:18081/v1`
- `model`: `Qwen3.6-27B-Q4_0.gguf`
- `temperature`: `0.1`
- `max_tokens`: `1024`
- `timeout`: `120`

**调用逻辑**

- 以下 `task_profile` 默认指向小模型：
  - `memory_flush`
  - `tax_match_small`
  - `entity_extract_small`
- 若返回异常、无效 JSON、或小模型配置缺失，可按 `local_llm.execution` 中策略回退主模型。

**业务场景**

- 税审规则匹配初筛
- 实体抽取
- 记忆冲刷、低成本辅助任务

### 5.3 中文 Embedding：`BAAI/bge-small-zh-v1.5`

**用途**

- 中文法规文本向量化
- 中文合同片段向量化
- RAG 召回

**配置参数**

- `embedding_model`: `../models/embedding/zh/model.onnx`
- `embedding_tokenizer_dir`: `../models/embedding/zh`
- `embedding_model_id`: `BAAI/bge-small-zh-v1.5`
- `embedding_max_seq_len`: `512`
- `embedding_pooling`: `cls`
- `embedding_threads`: `2`

**调用逻辑**

- 启动时由 `EmbeddingService.load_embedders()` 预加载。
- 查询向量化时，如果 `is_query=true`，会为 query 拼接 query instruction。
- 当前运行在 `onnxruntime` + `CPUExecutionProvider`。

**业务场景**

- `/regulations/search`
- 合同审计 RAG 检索
- 税审中的法规召回

### 5.4 英文 Embedding：`BAAI/bge-small-en-v1.5`

**用途**

- 英文文本向量化
- 跨语检索基础能力

**配置参数**

- `embedding_model`: `../models/embedding/en/model.onnx`
- `embedding_tokenizer_dir`: `../models/embedding/en`
- `embedding_model_id`: `BAAI/bge-small-en-v1.5`
- `embedding_max_seq_len`: `512`
- `embedding_pooling`: `cls`
- `embedding_threads`: `2`

**业务场景**

- 英文查询直接检索
- 若后续开启翻译增强，可与中文法规检索配合使用

### 5.5 Reranker 模型

**当前状态**

- 配置层声明：`reranker_enabled=true`
- 当前唯一已配置路径：`models/reranker/en`
- 资产状态：**缺失**

**理论用途**

- 对召回候选进行二次排序
- 让 `candidate_size` 大于 `top_k` 时，优先保留更相关证据

**当前结论**

- 代码支持，但当前环境不应依赖该能力作为稳定主链路。

### 5.6 Translation 模型：`tencent/HY-MT1.5-1.8B`

**当前状态**

- `translation_config.enabled=false`
- `model_dir` 缺失

**理论用途**

- 英文查询翻译到中文法规检索语言
- 支持 `dual` 或 `translate_only` 检索模式

**当前结论**

- 当前环境下翻译服务属于“已保留接口、未启用能力”。

## 6. 业务流程中的模型使用链路

### 6.1 合同审计主链路

1. 用户上传 `docx/pdf`
2. 后端提取文本
3. 若文本不足，进入 OCR 回退
4. 文本按块切分后发起法规检索
5. 检索阶段使用 embedding，必要时使用 reranker
6. 汇总法规证据和合同上下文
7. 调用主模型执行合同审计
8. 产出风险列表、证据引用、导出结果

当前注意点：

- `memory_temporary_disable.enabled=true`
- 因此合同审计当前更接近 `classic` / `classic_fallback` 路径，而非完整 memory 增强路径

### 6.2 税审链路

1. 导入法规文档
2. 解析法规条款与规则
3. 导入合同/票据等材料
4. 提取条款、实体和结构信息
5. 小模型执行规则匹配初筛
6. 主模型执行风险问题生成与高风险复核
7. 生成报告、审阅结论与导出文件

### 6.3 检索链路

1. 构造 `SearchQuery`
2. 根据语言选择 `zh/en` embedding
3. 生成 query embedding
4. 执行 BM25 + 语义召回
5. 若 reranker 可用且请求开启，则执行重排
6. 返回法规证据候选

## 7. 服务启动前置依赖

### 7.1 系统与运行环境

- Python：建议 `3.12+`
- Node.js：建议 `22+`
- npm：随 Node.js 安装
- 操作系统：当前仓库已在 Windows 环境验证

### 7.2 Python 依赖

后端主要依赖包括：

- `fastapi`
- `uvicorn`
- `httpx`
- `onnxruntime`
- `transformers`
- `torch`
- `python-docx`
- `pypdf`
- `pytesseract`
- `pdf2image`
- `mineru`
- `pyjwt`
- `passlib`
- `python-multipart`
- `chromadb`（可选）

安装命令：

```bash
cd app
pip install -r requirements.txt
```

### 7.3 前端依赖

前端基于：

- `react 18.2.0`
- `vite 5.1.4`
- `recharts`
- `vitest`
- `storybook`

安装命令：

```bash
cd web
npm install
```

### 7.4 模型与运行时依赖

必须提前准备：

- `models/llm/` 下 GGUF 模型文件
- `models/embedding/zh`、`models/embedding/en` 下 ONNX 与 tokenizer 目录
- `third_party/llama.cpp/bin-win-cpu-x64/` 下 `llama-server.exe` 及相关 DLL

### 7.5 OCR 依赖

若要处理扫描件或图片类税审材料，需要：

- Tesseract
- Poppler
- MinerU CLI/模块

## 8. 完整启动操作指南

### 8.1 步骤一：初始化数据库与目录

```bash
python -m app.main --init
```

用途：

- 创建数据库
- 初始化必要表结构
- 补齐 embedding / article 相关字段

### 8.2 步骤二：校验本地模型资产

推荐命令：

```bash
python bin/ensure_local_models.py --check-only --types all
```

如需连可选模型一起检查：

```bash
python bin/ensure_local_models.py --check-only --types all --include-optional
```

期望结果：

- embedding 至少应全部通过
- 当前环境下 reranker / translation 大概率会提示未就绪

### 8.3 步骤三：写入双 llama.cpp 本地配置

```bash
python .\bin\apply-local-llm-config.py --provider llama_cpp --small-provider llama_cpp
```

常用参数：

- `--llama-cpp-host`：主模型地址，默认 `http://127.0.0.1:18080`
- `--small-llama-cpp-host`：小模型地址，默认 `http://127.0.0.1:18081`
- `--main-model`：覆盖主模型别名
- `--small-model`：覆盖小模型别名
- `--disable`：禁用 `local_llm`
- `--dry-run`：只预览，不写文件

### 8.4 步骤四：启动双 llama.cpp 运行时

推荐使用一键脚本：

```bash
python .\bin\start-local-llamacpp-stack.py
```

默认行为：

- 主模型：`Qwen3.6-35B-A3B-UD-Q4_K_M.gguf`
- 小模型：`Qwen3.6-27B-Q4_0.gguf`
- 主端口：`18080`
- 小端口：`18081`
- 主上下文：`8192`
- 小上下文：`4096`

常用参数：

- `--server-exe`：显式指定 `llama-server.exe`
- `--models-dir`：模型目录
- `--main-port` / `--small-port`
- `--main-ctx-size` / `--small-ctx-size`
- `--main-threads` / `--small-threads`
- `--main-gpu-layers` / `--small-gpu-layers`
- `--wait-seconds`

### 8.5 步骤五：单实例调试启动方式

若只调试主模型，可使用：

```bash
python .\bin\start-local-llamacpp-server.py --host-url http://127.0.0.1:18080 --model-path .\models\llm\Qwen3.6-35B-A3B-UD-Q4_K_M.gguf --alias Qwen3.6-35B-A3B-UD-Q4_K_M.gguf
```

重要参数说明：

- `--ctx-size`：上下文窗口
- `--threads`：推理线程数
- `--threads-http`：HTTP 工作线程
- `--batch-size`：批处理大小
- `--ubatch-size`：微批大小
- `--gpu-layers`：卸载到 GPU 的层数
- `--parallel`：并行请求槽位
- `--no-mmap`：磁盘或映射不稳定时禁用 mmap

### 8.6 步骤六：启动后端

```bash
python -m app.main
```

环境变量：

- `APP_PORT`：默认 `8000`
- `APP_PORT_AUTO_SWITCH`：默认开启；若 `8000` 被占用，会自动向后找空闲端口
- `JWT_SECRET_KEY`：生产环境必须显式设置

### 8.7 步骤七：启动前端

```bash
cd web
npm run dev
```

默认端口：

- `5173`

### 8.8 步骤八：启动后验收

建议按顺序验证：

1. `GET /health`
2. `GET /api/admin/llama-cpp/models`
3. `POST /api/admin/llm-test`
4. `GET /embeddings/info`
5. 登录前端并进入 Admin 页面

## 9. 启动参数说明

### 9.1 `start-local-llamacpp-stack.py`

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--main-model` | `Qwen3.6-35B-A3B-UD-Q4_K_M.gguf` | 主模型文件名 |
| `--small-model` | `Qwen3.6-27B-Q4_0.gguf` | 小模型文件名 |
| `--main-port` | `18080` | 主模型 API 端口 |
| `--small-port` | `18081` | 小模型 API 端口 |
| `--main-ctx-size` | `8192` | 主模型上下文 |
| `--small-ctx-size` | `4096` | 小模型上下文 |
| `--main-threads` | `0` | `0` 表示沿用单实例启动器默认值 |
| `--small-threads` | `0` | 同上 |
| `--main-gpu-layers` | `0` | CPU 模式为 0 |
| `--small-gpu-layers` | `0` | CPU 模式为 0 |
| `--wait-seconds` | `240` | 每个 runtime 的就绪等待时间 |

### 9.2 `start-local-llamacpp-server.py`

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--host-url` | `http://127.0.0.1:18080` | 监听地址 |
| `--alias` | 模型文件名 | `/v1/models` 返回的模型名，必须与配置一致 |
| `--ctx-size` | `8192` | 上下文长度 |
| `--threads` | `max(4, cpu_count-2)` | 推理线程 |
| `--threads-http` | `2` | HTTP 线程 |
| `--batch-size` | `1024` | 批大小 |
| `--ubatch-size` | `512` | 微批大小 |
| `--gpu-layers` | `0` | GPU 卸载层数 |
| `--parallel` | `1` | 并行槽位 |
| `--wait-seconds` | `180` | 加载等待时间 |

## 10. 常见启动问题与排查

### 10.1 `llama-server executable was not found`

原因：

- `third_party/llama.cpp/bin-win-cpu-x64/` 不完整
- 未传入 `--server-exe`

处理：

1. 确认 `llama-server.exe` 存在
2. 确认相关 DLL 一并存在
3. 必要时显式传 `--server-exe`

### 10.2 GGUF 模型文件不存在

原因：

- `models/llm/` 未放置模型
- 模型名与脚本默认值不一致

处理：

1. 核对文件名
2. 使用 `--main-model` / `--small-model` 覆盖默认值

### 10.3 `/api/admin/llama-cpp/models` 无法访问

原因：

- `llama.cpp` 未启动
- 端口不一致
- `--alias` 与配置不匹配

处理：

1. 先访问 `http://127.0.0.1:18080/v1/models`
2. 核对 `app/config.json` 中 `api_base`
3. 核对 `model` 与 runtime `--alias`

### 10.4 后端启动时报模型预检失败

原因：

- 预检发现 embedding / reranker / translation 缺失

处理：

1. 当前主链路至少保证 embedding 完整
2. 若仅临时运行主流程，不要启用 translation
3. 对 reranker 缺失保持知情，避免把其当成必需能力

### 10.5 登录后接口统一返回 `401`

原因：

- 未带 `Authorization: Bearer <token>`
- token 过期
- `JWT_SECRET_KEY` 更换导致旧 token 失效

处理：

1. 重新登录获取 token
2. 检查请求头
3. 生产环境固定 `JWT_SECRET_KEY`

### 10.6 合同审计报超时

原因：

- 主模型上下文过大
- CPU 线程设置不合理
- 模型推理耗时过长

处理：

1. 适当降低 `ctx-size`
2. 调整 `threads`
3. 延长 `timeout`
4. 观察 `logs/local-llm/llama-cpp-server.log`

### 10.7 OCR 失败

原因：

- Tesseract / Poppler / MinerU 缺依赖
- `torchvision` / `mineru` 环境不完整

处理：

1. 运行 `python bin/verify_ocr_env.py --pdf <sample.pdf> --output <report.json>`
2. 检查系统 PATH
3. 检查 MinerU CLI 是否可执行

## 11. 服务使用手册

### 11.1 核心功能

系统当前核心能力包括：

1. 用户注册、登录、鉴权
2. 合同上传与合同审计
3. 合同预览与报告导出
4. 法规导入、解析、检索
5. 税审材料导入、条款分析、规则匹配、问题生成、审阅与导出
6. Admin 模型配置、向量库切换、Trace 查询、Token 统计

### 11.2 接口调用规范

**协议与风格**

- 协议：HTTP/JSON
- 文件上传：`multipart/form-data`
- 鉴权：`Authorization: Bearer <access_token>`

**基础地址**

- 后端：`http://localhost:8000`
- Swagger：`http://localhost:8000/docs`

### 11.3 认证接口

#### 注册

`POST /api/auth/register`

请求：

```json
{
  "username": "alice",
  "email": "alice@example.com",
  "password": "your-password"
}
```

返回：

```json
{
  "id": "user-id",
  "username": "alice",
  "email": "alice@example.com",
  "role": "user"
}
```

#### 登录

`POST /api/auth/login`

请求：

```json
{
  "username": "alice",
  "password": "your-password"
}
```

返回：

```json
{
  "access_token": "jwt-token",
  "token_type": "bearer",
  "user": {
    "id": "user-id",
    "username": "alice",
    "email": "alice@example.com",
    "role": "user"
  }
}
```

### 11.4 健康与基础能力接口

#### 健康检查

`GET /health`

返回字段：

- `status`
- `embedding_ready`
- `embedding_default_language`
- `embedding_languages`

#### Embedding 信息

`GET /embeddings/info`

返回：

- `ready`
- `default_language`
- `models.<lang>.model_id`
- `models.<lang>.model_path`
- `models.<lang>.tokenizer_dir`

#### 向量编码

`POST /embeddings/encode`

请求：

```json
{
  "text": "增值税专用发票风险",
  "is_query": true,
  "language": "zh"
}
```

返回：

- `dim`
- `is_query`
- `language`
- `model_id`
- `vector`

### 11.5 法规管理接口

#### 导入法规

`POST /regulations/import`

表单字段：

- `file`
- `title`
- `doc_no`
- `issuer`
- `reg_type`
- `status`
- `effective_date`
- `expiry_date`
- `region`
- `industry`
- `regulation_id`
- `language`

#### 查询导入任务

`GET /regulations/import/{job_id}`

#### 列出法规

`GET /regulations`

#### 法规检索

`POST /regulations/search`

请求体核心字段：

- `query`
- `language`
- `top_k`
- `use_semantic`
- `semantic_weight`
- `bm25_weight`
- `candidate_size`
- `rerank_enabled`
- `rerank_top_n`
- `rerank_mode`

### 11.6 合同审计接口

#### 上传并审计合同

`POST /contracts/audit`

表单字段核心项：

- `file`
- `title`
- `language`
- `audit_mode`
- `risk_detection_mode`
- `region`
- `date`
- `industry`
- `tax_focus`
- `use_semantic`
- `semantic_weight`
- `bm25_weight`
- `candidate_size`
- `rerank_enabled`
- `rerank_top_n`
- `rerank_mode`

返回核心字段：

- `audit_id`
- `document_id`
- `result`
- `meta`
- `risk_summary`

#### 查询进度

`GET /contracts/audit/{audit_id}/progress`

#### 重跑管线

`POST /contracts/{document_id}/pipeline/run`

#### 合同预览

- `GET /contracts/{document_id}/preview-manifest`
- `GET /contracts/{document_id}/preview/pages/{page_no}/image`
- `GET /contracts/{document_id}/preview`

#### 导出报告

`POST /contracts/{document_id}/report/export`

请求体：

```json
{
  "export_format": "json",
  "template_version": "v1.0",
  "locale": "zh-CN",
  "brand": ""
}
```

`export_format` 支持：

- `json`
- `docx`

### 11.7 税审接口

#### 导入法规与合同

- `POST /tax-audit/regulations/import`
- `POST /tax-audit/contracts/import`

支持扩展名：

- `pdf`
- `doc`
- `docx`
- `xls`
- `xlsx`
- `png`
- `jpg`
- `jpeg`
- `tif`
- `tiff`

#### 税审流程主接口

- `POST /tax-audit/regulations/{document_id}/parse`
- `POST /tax-audit/contracts/{contract_id}/analyze`
- `GET /tax-audit/contracts/{contract_id}/clauses`
- `POST /tax-audit/contracts/{contract_id}/match`
- `GET /tax-audit/contracts/{contract_id}/matches`
- `POST /tax-audit/contracts/{contract_id}/issues/generate`
- `GET /tax-audit/contracts/{contract_id}/issues`
- `POST /tax-audit/contracts/{contract_id}/pipeline/run`

#### 审阅与追踪

- `POST /tax-audit/issues/{issue_id}/review`
- `GET /tax-audit/issues/{issue_id}/trace`
- `GET /tax-audit/contracts/{contract_id}/trace`

#### 报告

- `GET /tax-audit/contracts/{contract_id}/report`
- `POST /tax-audit/contracts/{contract_id}/report`
- `POST /tax-audit/contracts/{contract_id}/report/export`
- `GET /tax-audit/exports/{export_id}`
- `GET /tax-audit/exports/{export_id}/download`

#### 清理归档

- `POST /tax-audit/cleanup/run`
- `POST /tax-audit/cleanup/jobs/{job_id}/retry`
- `GET /tax-audit/cleanup/jobs`
- `GET /tax-audit/archive/records`

### 11.8 Admin 接口

仅管理员可访问：

- `GET/PUT /api/admin/llm-config`
- `GET /api/admin/ollama/models`
- `GET /api/admin/llama-cpp/models`
- `POST /api/admin/llm-test`
- `GET/PUT /api/admin/memory-config`
- `GET /api/admin/token-usage`
- `GET /api/admin/token-usage/csv`
- `GET /api/admin/llm-traces`
- `GET /api/admin/llm-traces/{span_id}`
- `GET /api/admin/llm-traces/stats/summary`
- `DELETE /api/admin/llm-traces`
- `GET/PUT /api/admin/vector-store/config`
- `POST /api/admin/vector-store/cleanup`

## 12. 输入输出格式说明

### 12.1 认证

- 输入：JSON
- 输出：JSON

### 12.2 文件类接口

- 输入：`multipart/form-data`
- 文件字段通常为 `file`
- 输出：JSON，返回 `document_id` / `contract_id` / `job_id`

### 12.3 检索与分析类接口

- 输入：JSON
- 输出：JSON
- 大多数列表接口返回 `total + items`

### 12.4 导出类接口

- 提交导出：返回导出任务状态 JSON
- 下载导出：返回文件流

## 13. 权限配置要求

### 13.1 普通用户

可访问：

- 登录后业务接口
- 自己的合同审计、税审流程、预览与导出

### 13.2 管理员

额外可访问：

- 用户管理
- 文档总览
- 模型配置
- 向量库配置
- memory 配置
- token usage 统计
- LLM trace 查询与清理

### 13.3 安全要求

- 生产环境必须设置 `JWT_SECRET_KEY`
- 本地/内网部署下禁止将外网 LLM endpoint 配入主路径
- 对 `ollama` / `llama_cpp` 本地 provider，代码会剥离 API key 依赖

## 14. 日常运维操作流程

### 14.1 每日巡检

建议巡检项：

1. 检查 `18080/18081/8000/5173` 端口状态
2. 检查 `/health`
3. 检查 `/api/admin/llama-cpp/models`
4. 检查最近 24h `token-usage`
5. 检查 `logs/local-llm/llama-cpp-server.log`

### 14.2 模型运行状态确认

确认点：

1. `/v1/models` 返回的 `id` 是否与配置中的 `model` 一致
2. 主模型与小模型端口是否串线
3. 超时是否异常增大

### 14.3 配置变更流程

推荐顺序：

1. 先执行 `apply-local-llm-config.py --dry-run`
2. 确认模型别名、端口、provider
3. 再实际写入配置
4. 重启后端使运行态完全一致

### 14.4 日志与追踪

主要观测面：

- 应用日志：`logs/`
- LLM Trace：`data/memory/llm_interactions` 与 `data/llm_traces_full`
- RAG Trace：`data/memory/debug/rag_retrieval`

### 14.5 清理与归档

建议定期执行：

- 税审归档清理接口
- LLM trace 清理接口
- 无用导出文件清理

## 15. 故障排查指引

### 15.1 判断故障属于哪一层

1. `llama.cpp` 层：模型没启动、端口不通、alias 不对
2. `backend` 层：配置错误、鉴权失败、业务异常
3. `retrieval` 层：embedding 缺失、reranker 不可用、检索退化
4. `ocr` 层：扫描件提取失败
5. `frontend` 层：接口地址、登录态、跨域

### 15.2 最短排查路径

1. 看端口
2. 看 `/health`
3. 看 `/api/admin/llama-cpp/models`
4. 看 `/api/admin/llm-test`
5. 看具体业务接口报错
6. 看 trace 和日志

### 15.3 当前环境特别需要注意的风险

1. 当前机器没有业务服务在运行，任何“服务异常”都要先排除其实是“服务未启动”。
2. `reranker` 和 `translation` 当前都不是完整可用状态，出现检索效果退化时不要误判为主模型故障。
3. `memory_temporary_disable.enabled=true`，当前结果表现会更偏向 classic 路径，而不是完整 memory 增强路径。

## 16. 推荐启动顺序

建议固定为：

1. `python -m app.main --init`
2. `python bin/ensure_local_models.py --check-only --types all`
3. `python .\bin\apply-local-llm-config.py --provider llama_cpp --small-provider llama_cpp`
4. `python .\bin\start-local-llamacpp-stack.py`
5. `python -m app.main`
6. `cd web && npm run dev`
7. 访问 `/health`、`/api/admin/llama-cpp/models`、`/api/admin/llm-test`

## 17. 当前结论摘要

当前仓库的真实主运行架构已经是：

- **双 `llama.cpp` 本地离线推理**
- **FastAPI 后端编排**
- **React/Vite 前端**
- **ONNX embedding 本地检索**

其中：

- 主模型、小模型、双语 embedding 已具备落地条件
- reranker 和 translation 仍处于“代码支持、环境未就绪”状态
- 当前机器运行态为空，需按本文档步骤手动启动完整栈
