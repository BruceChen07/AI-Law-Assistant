# AI Law Assistant

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.8+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-0.115.0-blue.svg" alt="FastAPI">
  <img src="https://img.shields.io/badge/React-18.2-brightgreen.svg" alt="React">
  <img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License">
</p>

一个基于混合检索（BM25 + 语义向量 + 交叉编码重排）的法律法规智能检索系统。支持中英文法律条款语义搜索与两阶段排序，适用于企业合规、法务审计等场景。

## 功能特性

- **两阶段检索**：召回（BM25 + 向量）+ 交叉编码重排，提升精度
- **多语言支持**：支持中文（zh）和英文（en）法规检索
- **法规管理**：支持 doc/docx/pdf 文件导入并自动解析条款
- **向量语义**：BGE-small 向量模型支持语义检索
- **鉴权与管理**：内置用户登录与管理端 API
- **RESTful API**：提供完整 API 便于系统集成
- **Web UI**：React + Vite 前端，支持中英文切换

## 技术架构

```text
Frontend (React + Vite)
        │ HTTP
Backend (FastAPI + Uvicorn)
  ├─ API Routes (auth, regulations, embedding, admin)
  ├─ Services (search, importer, crud)
  └─ Core (config, database, embedding, reranker, logger)
        │
Data Layer (SQLite + FTS5 + Files)
        │
Models (Embedding + Reranker)
```

### 技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | FastAPI, Uvicorn |
| 数据库 | SQLite + FTS5 |
| 向量/重排 | ONNX Runtime, Transformers, Torch |
| 模型 | BGE-small-zh/en, BGE-reranker |
| 前端框架 | React 18, Vite 5 |
| 鉴权 | PyJWT, bcrypt, passlib |
| 文件解析 | python-docx, pypdf |

## 快速开始

### 环境要求

- Python 3.8+
- Node.js 18+
- Windows / Linux / macOS

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
cp app/config.example.json app/config.json
```

根据需要修改 `app/config.json` 中的配置。

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

### 6. 访问系统

打开浏览器访问 http://localhost:5173 即可使用。

## 使用指南

### 法规导入

1. 在「法规导入」表单中填写法规信息
2. 上传 docx/doc/pdf 格式的法规文件
3. 点击「上传」按钮
4. 可通过「查询任务」查看导入状态

支持的字段：
- **标题 (Title)**：法规文件标题
- **Tag**：法规文号/标签
- **发布机构 (Issuer)**：发布该法规的机构
- **类型 (Type)**：法规类型
- **生效日期 / 失效日期**：法规有效期
- **地区 (Region)**：适用地区
- **Sub-Tag**：行业分类

### 法规检索

1. 在「检索」区域输入查询关键词
2. 可选筛选条件：日期、地区、Sub-Tag
3. 配置检索权重：
   - **Semantic Weight (0-1)**：语义检索权重
   - **BM25 Weight (0-1)**：关键词匹配权重
   - **Candidates**：候选文档数量
4. 点击「搜索」查看结果

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

| 方法 | 路径 | 描述 |
|------|------|------|
| GET | `/health` | 健康检查 |
| GET | `/docs` | API 文档 |
| POST | `/regulations/import` | 导入法规 |
| GET | `/regulations/import/{job_id}` | 查询导入任务 |
| GET | `/regulations` | 获取法规列表 |
| GET | `/regulations/{id}/articles` | 获取法规条款 |
| POST | `/regulations/search` | 检索法规 |
| POST | `/embedding/compute` | 计算向量 |

## 配置说明

### 配置文件 (app/config.json)

```json
{
    "data_dir": "../data",
    "db_path": "../data/app.db",
    "files_dir": "../data/files",
    "static_dir": "../web/dist",
    "default_language": "zh",
    "reranker_enabled": true,
    "reranker_model_path": "../models/reranker/zh",
    "reranker_profiles": {
        "zh": "../models/reranker/zh",
        "en": "../models/reranker/en"
    },
    "rerank_batch_size": 8,
    "rerank_max_len": 512,
    "rerank_fallback": true,
    "rerank_score_threshold": 0.0,
    "embedding_profiles": {
        "zh": {
            "embedding_model": "../models/embedding/zh/model.onnx",
            "embedding_tokenizer_dir": "../models/embedding/zh",
            "embedding_model_id": "BAAI/bge-small-zh-v1.5",
            "embedding_max_seq_len": 512,
            "embedding_pooling": "cls"
        },
        "en": {
            "embedding_model": "../models/embedding/en/model.onnx",
            "embedding_tokenizer_dir": "../models/embedding/en",
            "embedding_model_id": "BAAI/bge-small-en-v1.5",
            "embedding_max_seq_len": 512,
            "embedding_pooling": "cls"
        }
    },
    "log_level": "INFO",
    "log_dir": "../data/logs",
    "log_max_bytes": 5242880,
    "log_backup_count": 3,
    "cors_allow_origins": ["http://localhost:5173"]
}
```

### 配置项说明

| 配置项 | 类型 | 说明 |
|--------|------|------|
| `data_dir` | string | 数据目录路径 |
| `db_path` | string | SQLite 数据库路径 |
| `files_dir` | string | 上传文件存储目录 |
| `static_dir` | string | 前端静态资源目录 |
| `default_language` | string | 默认语言 (zh/en) |
| `reranker_enabled` | boolean | 是否启用重排 |
| `reranker_model_path` | string | 默认重排模型路径 |
| `reranker_profiles` | object | 重排模型多语言路径 |
| `rerank_batch_size` | int | 重排 batch 大小 |
| `rerank_max_len` | int | 重排最大长度 |
| `rerank_fallback` | boolean | 重排失败是否回退 |
| `rerank_score_threshold` | float | 重排分数阈值 |
| `embedding_profiles` | object | 向量模型配置 |
| `log_dir` | string | 日志目录 |
| `log_max_bytes` | int | 单文件最大大小 |
| `log_backup_count` | int | 日志备份数量 |
| `log_level` | string | 日志级别 |
| `cors_allow_origins` | array | 允许的跨域来源 |

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `APP_PORT` | 8000 | 服务端口 |
| `APP_CONFIG` | config.json | 配置文件路径 |
| `APP_PORT_AUTO_SWITCH` | 1 | 端口冲突时自动漂移 |



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

## 贡献指南

欢迎提交 Issue 和 Pull Request！

## 致谢

- [BGE](https://github.com/FlagOpen/FlagEmbedding) - 向量模型
- [FastAPI](https://fastapi.tiangolo.com/) - Web 框架
- [React](https://react.dev/) - UI 框架