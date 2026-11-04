# AI Law Assistant 高层架构说明

本系统采用“体验层 -> 应用层 -> 领域服务层 -> AI/数据平台层 -> 本地运行时与外部依赖”五层结构。最上层由 React/Vite 前端承载登录、合同审计、法规管理和税审工作台；前端通过 `web/api/*.js` 统一访问 FastAPI 接口，并复用鉴权、状态管理和基础 URL 回退能力。

应用层由 `app_factory.py` 组装配置、数据库、Embedding、LLM 与全部路由，`auth`、`contracts`、`regulations`、`tax_audit`、`admin`、`health` 等接口共同构成系统控制面。该层只负责 HTTP 边界、权限校验与请求分发，不承载重业务算法。

领域服务层按业务拆分为四组：合同审计负责文档解析、证据检索、规则或记忆审计、报告导出；法规服务负责上传、草稿解析、条文切分、索引迁移与检索；税审流水线负责法规匹配、问题生成与运行态会话；能力与 Agent 模块负责技能、规则包、记忆晋升及配置编排。

AI/数据平台层是系统核心支撑：`llm.py` 和 `llm_router.py` 管理本地模型路由与失败回退；Embedding、Reranker、OCR、Translation 提供认知能力；Memory System 与 RAG 检索协调法规库、向量库和审计记忆；SQLite `app.db`、FTS5、`rag_zh/en.db` 与向量存储共同承担持久化。

关键交互规则包括：一，所有业务入口统一经过 FastAPI 路由与鉴权；二，AI 推理优先走本地 `llama.cpp`，`Ollama` 为可选兼容路径；三，法规导入与合同审计都会回写 SQLite/向量层，形成可检索资产；四，部署、配置、健康检查、日志与 trace 由脚本和管理接口统一控制，服务于离线原生部署与运维巡检。
