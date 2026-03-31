# AI Law Assistant

- [English (英文版)](README.en-US.md)
- [简体中文 (Simplified Chinese)](README.zh-CN.md)

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.8+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-0.115.0-blue.svg" alt="FastAPI">
  <img src="https://img.shields.io/badge/React-18.2-brightgreen.svg" alt="React">
  <img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License">
</p>

一个面向财税与法务的合同审计系统，包含合同上传审计、法规导入与检索、OCR增强解析，以及可配置的大语言模型调用。

## 功能特性

- **合同审计**：上传合同（docx/pdf/扫描 pdf），输出风险清单、摘要与证据引用，并支持导出带精准批注的 Word 文档
- **OCR 增强**：PDF 文本不足时自动回退 OCR，支持多引擎与按文件类型路由
- **法规管理**：Admin 支持法规上传、条款解析、混合检索与任务追踪
- **混合检索与跨语种召回**：BM25 + 向量召回 + 可选重排，支持翻译增强查询（translation_config）
- **记忆式审计管线**：支持条款级轮次审计、引用白名单映射、未验证风险过滤与调试追踪
- **安全配置**：LLM 密钥优先走安全存储/环境变量，避免在配置文件中明文持久化
- **模型与界面配置**：集中式 LLM 参数、UI 开关、运行时热更新
- **鉴权与管理**：用户登录、角色权限、管理员配置接口
- **Web UI**：React + Vite 前端，中英文切换

## 技术架构

```text
Frontend (React + Vite)
        │ HTTP
Backend (FastAPI + Uvicorn)
  ├─ API Routes (auth, regulations, contracts, admin)
  ├─ Services (search, importer, contract_audit, crud)
  └─ Core (config, database, embedding, reranker, llm, ocr)
        │
Data Layer (SQLite + FTS5 + Files)
        │
Models (Embedding + Reranker + LLM)
```

### 技术栈

| 层级    | 技术                                |
| ----- | --------------------------------- |
| 后端框架  | FastAPI, Uvicorn                  |
| 数据库   | SQLite + FTS5, 可选 ChromaDB        |
| 向量/重排 | ONNX Runtime, Transformers, Torch |
| 模型    | BGE-small-zh/en, BGE-reranker     |
| 前端框架  | React 18, Vite 5                  |
| 鉴权    | PyJWT, bcrypt, passlib            |
| 文件解析  | python-docx, pypdf                |

## 快速开始

### 环境要求

- Python 3.12+
- Node.js 22+
- Windows / Linux / macOS
- OCR 依赖：tesseract、poppler, mineru（用于扫描 PDF）
- 向量数据库：默认使用 SQLite 内存计算。如果是生产环境（百万级以上向量），建议安装 `chromadb` 并在后台配置切换。

### 1. 克隆项目

```bash
git clone https://github.com/your-repo/AI-Law-Assistant.git
cd AI-Law-Assistant
```

### 2. 安装后端依赖

```bash
cd app
pip install -r requirements.txt
```

### 3. 配置环境

复制配置示例文件：

```bash
# Linux / macOS
cp app/config.example.json app/config.json

# Windows PowerShell
Copy-Item app/config.example.json app/config.json
```

根据需要修改 `app/config.json`。建议将 LLM 密钥通过环境变量或安全存储管理，不在配置文件中写入明文。

### 4. 初始化数据库

```bash
# 方式一：使用 Python
python -m app.main --init

# 方式二：使用 PowerShell 脚本（Windows）
.\app\init.ps1
```

### 5. 启动服务

启动后端服务：

```bash
python -m app.main
```

后端默认运行在 `http://localhost:8000`

启动前端开发服务：

```bash
cd web
npm install
npm run dev
```

前端默认运行在 `http://localhost:5173`

前端质量与测试命令：

```bash
cd web
npm run lint
npm run test
npm run storybook
```

### 6. 访问系统

打开浏览器访问 <http://localhost:5173> 即可使用。

## 使用指南

### 合同审计（主界面）

1. 上传合同 docx/pdf/扫描 pdf
2. 点击审计生成摘要与风险清单
3. 结果中可查看 OCR 与模型元信息
4. 风险项下展示法规证据，格式为《法规文档名称》第X条（摘要：……）
5. 支持一键下载**带批注的 Word 版本**，所有检测到的风险点都会精准定位并作为批注附着在对应条款正文旁。

审计结果中的 citations 字段包含 citation\_id、law\_title、article\_no、excerpt，前端会优先展示这些字段并保留证据溯源能力。

### 法规导入与检索（Admin 页面）

1. 进入 Admin → 「法规上传」
2. 上传法规文档（docx/pdf），填写法规元数据
3. 通过「查询任务」查看导入状态
4. 在「检索」区域输入关键词并调整权重

支持字段：

- **标题 (Title)**：法规文件标题
- **Tag**：法规文号/标签
- **发布机构 (Issuer)**：发布该法规的机构
- **生效日期 / 失效日期**：法规有效期
- **地区 (Region)**：适用地区
- **Sub-Tag**：行业分类

### 模型配置（Admin 页面）

1. 进入 Admin → 「模型配置」
2. 填写 API 端点、模型、温度、最大 Token 等
3. 保存后立即生效

### OCR 验证

```bash
python bin/verify_ocr_env.py --pdf /path/to/sample.pdf --output reports/ocr_report.json
```

### 重排（Reranker）使用指南

- **启用方式**：在 `app/config.json` 中设置 `reranker_enabled: true`
- **模型路径**：
  - 单模型：`reranker_model_path`
  - 多语言：`reranker_profiles`（如 `zh`/`en`）
- **模型格式**：需为 Transformers 的 SequenceClassification 模型目录（包含 `config.json`、`tokenizer*`、`pytorch_model.bin` 或 `model.safetensors`）
- **请求级控制**：
  - `rerank_enabled`: 单次请求启用/禁用
  - `rerank_mode`: `on`/`off`/`ab`（A/B 自动分流）
  - `rerank_top_n`: 参与重排的候选数量（建议不超过 `candidate_size`）
- **异常回退**：`rerank_fallback: true` 时重排失败会回退为召回排序

示例请求：

```json
{
  "query": "增值税 税率",
  "language": "zh",
  "top_k": 10,
  "use_semantic": true,
  "semantic_weight": 0.6,
  "bm25_weight": 0.4,
  "candidate_size": 50,
  "rerank_mode": "on",
  "rerank_top_n": 50
}
```

### API 接口

| 方法   | 路径                             | 描述        |
| ---- | ------------------------------ | --------- |
| GET  | `/health`                      | 健康检查      |
| GET  | `/docs`                        | API 文档    |
| POST | `/contracts/audit`             | 合同审计      |
| POST | `/regulations/import`          | 导入法规      |
| GET  | `/regulations/import/{job_id}` | 查询导入任务    |
| GET  | `/regulations`                 | 获取法规列表    |
| GET  | `/regulations/{id}/articles`   | 获取法规条款    |
| POST | `/regulations/search`          | 检索法规      |
| POST | `/embedding/compute`           | 计算向量      |
| GET  | `/api/admin/llm-config`        | 获取 LLM 配置 |
| PUT  | `/api/admin/llm-config`        | 更新 LLM 配置 |

## 配置说明

### 配置文件 (app/config.json)

```json
{
    "data_dir": "../data",
    "db_path": "../data/app.db",
    "files_dir": "../data/files",
    "static_dir": "../web/dist",
    "default_language": "zh",
    "ocr_enabled": true,
    "ocr_languages": "chi_sim+eng",
    "ocr_min_text_length": 200,
    "ocr_dpi": 220,
    "ocr_engine": "auto",
    "ocr_engine_order": ["tesseract", "mineru"],
    "llm_config": {
        "provider": "openai_compatible",
        "api_base": "https://api.openai.com/v1",
        "api_key": "",
        "model": "gpt-4o-mini",
        "temperature": 0.2,
        "max_tokens": 2048,
        "timeout": 60,
        "headers": {}
    },
    "reranker_enabled": true,
    "reranker_model_path": "../models/reranker/zh",
    "reranker_profiles": {
        "zh": "../models/reranker/zh",
        "en": "../models/reranker/en"
    },
    "embedding_profiles": {
        "zh": {
            "embedding_model": "../models/embedding/zh/model.onnx",
            "embedding_tokenizer_dir": "../models/embedding/zh",
            "embedding_model_id": "BAAI/bge-small-zh-v1.5"
        }
    },
    "cors_allow_origins": ["http://localhost:5173"]
}
```

### 配置项说明

| 配置项                      | 类型      | 说明                        |
| ------------------------ | ------- | ------------------------- |
| `data_dir`               | string  | 数据目录路径                    |
| `db_path`                | string  | SQLite 数据库路径              |
| `files_dir`              | string  | 上传文件存储目录                  |
| `static_dir`             | string  | 前端静态资源目录                  |
| `default_language`       | string  | 默认语言 (zh/en)              |
| `ocr_enabled`            | boolean | 是否启用 OCR 回退               |
| `ocr_languages`          | string  | OCR 语言包配置                 |
| `ocr_min_text_length`    | int     | 触发 OCR 的文本长度阈值            |
| `ocr_dpi`                | int     | OCR 转换 DPI                |
| `ocr_engine`             | string  | OCR 引擎（auto/tesseract/其他） |
| `ocr_engine_order`       | array   | OCR 引擎优先级                 |
| `llm_config`             | object  | LLM 参数配置                  |
| `reranker_enabled`       | boolean | 是否启用重排                    |
| `reranker_model_path`    | string  | 默认重排模型路径                  |
| `reranker_profiles`      | object  | 重排模型多语言路径                 |
| `rerank_batch_size`      | int     | 重排 batch 大小               |
| `rerank_max_len`         | int     | 重排最大长度                    |
| `rerank_fallback`        | boolean | 重排失败是否回退                  |
| `rerank_score_threshold` | float   | 重排分数阈值                    |
| `embedding_profiles`     | object  | 向量模型配置                    |
| `log_dir`                | string  | 日志目录                      |
| `log_max_bytes`          | int     | 单文件最大大小                   |
| `log_backup_count`       | int     | 日志备份数量                    |
| `log_level`              | string  | 日志级别                      |
| `cors_allow_origins`     | array   | 允许的跨域来源                   |

### 环境变量

| 变量                     | 默认值         | 说明        |
| ---------------------- | ----------- | --------- |
| `APP_PORT`             | 8000        | 服务端口      |
| `APP_CONFIG`           | config.json | 配置文件路径    |
| `APP_PORT_AUTO_SWITCH` | 1           | 端口冲突时自动漂移 |

### 运行测试

```bash
# 安装测试依赖
pip install pytest pytest-asyncio

# 运行测试
pytest tests/
```

### 下载向量子模型

如果需要使用其他向量子模型，可以运行：

```bash
python app/download_embedding_model.py
```

或将 ONNX 模型文件放入 `models/embedding/zh/` 目录。

## 许可证

MIT License
