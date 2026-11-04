# 企业内网严格离线部署方案

## 1. 目标

- 所有推理、检索、OCR、翻译能力仅允许访问企业内网或本机地址。
- 禁止任何公网模型服务、公网模型仓库、公网对象存储、公网镜像站调用。
- 所有模型权重、词表、OCR 资产、前后端依赖均通过企业制品库或离线介质导入。
- 应用在模型缺失或路由失败时必须失败关闭，不得自动切换至公网能力。

## 2. 适用边界

- 适用于具备网络隔离要求的政企、金融、制造、能源等内网环境。
- 默认假设企业已具备以下内网基础设施之一：
  - 内网 LLM 网关，提供 OpenAI-compatible 接口。
  - 本机或内网节点部署的 Ollama / vLLM / TGI 推理服务。
  - 企业制品库，用于托管 embedding、reranker、translation、OCR 相关模型文件。

## 3. 方案总览

```text
用户浏览器
    |
企业内网 Web 入口
    |
AI-Law-Assistant (FastAPI)
    |-- SQLite / 本地文件
    |-- Embedding ONNX / Reranker / Translation / OCR 本地模型目录
    |-- 主模型路由 -> 内网主推理服务
    `-- 轻量模型路由 -> 内网轻量推理服务
```

## 4. 核心设计原则

### 4.1 失败关闭

- 小模型失败仅允许升级到内网主模型。
- 主模型失败直接返回错误，并记录审计日志。
- 禁止云端兜底、禁止按风险等级升级到公网复核。

### 4.2 网络策略守卫

- 新增 `network_policy` 配置段。
- `mode=offline_strict` 时，只允许访问：
  - `127.0.0.1` / `localhost`
  - RFC1918 私网地址
  - 企业显式白名单域名
  - 企业显式白名单域名后缀
- 若目标地址不满足规则，请求在发送前直接阻断。

### 4.3 资产本地化

- embedding、reranker、translation、MinerU 模型只认本地目录。
- `ensure_local_models.py` 仅做校验，不再承担公网下载职责。
- `download_embedding_model.py` 改为从内网制品目录复制模型资产。
- `download-local-llm-models.py` 改为验证本地模型是否已导入，不再触发远端拉取。

## 5. 配置基线

### 5.1 LLM

- `llm_config` 指向内网主推理服务，例如 `http://127.0.0.1:18081/v1`
- `local_llm.main_model` 指向主模型服务
- `local_llm.small_model` 指向轻量模型服务
- `allow_small_to_main_fallback=true`
- 不再存在 `cloud_fallback_*` 系列配置

### 5.2 OCR

- `mineru.model_source=local_files`
- OCR 所需权重须由运维提前放置到约定目录

### 5.3 Retrieval

- `embedding_source=local_registry`
- `translation_config.enabled=false` 作为默认安全基线
- 若企业需要跨语种召回，须显式指定 `model_dir`

## 6. 依赖基线

- 移除 `openai`
- 移除 `modelscope`
- 保留 `httpx` 作为通用 HTTP 客户端
- 保留 `transformers` / `torch` / `onnxruntime` 用于本地推理和模型加载

## 7. 验收标准

- 代码、配置、依赖清单中不存在以下内容：
  - 公网大模型域名
  - 公网模型仓库 SDK
  - 云端兜底逻辑
  - 公网 API Key 环境变量命名
- 使用 `python .\bin\validate_offline_compliance.py` 输出 `PASS`
- 使用 `python .\bin\ensure_local_models.py --check-only --include-optional` 完成本地资产校验

## 8. 推荐交付物

- `app/config.example.json` 与 `app/config.json`
- `docs/Configuration Reference.md`
- `plan/enterprise-offline-private-deployment-runbook.md`
- `bin/validate_offline_compliance.py`
