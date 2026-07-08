# AI Law Assistant

- [中文文档](README.zh-CN.md)
- [English Docs](README.en-US.md)

## 本次提交变更整理

### 1) 安全配置（P0）

- `app/config.json` 已移除明文 `llm_config.api_key`
- LLM 调用支持从环境变量读取密钥，优先级如下：
  - `LLM_API_KEY`
  - `OPENAI_API_KEY`
  - `DASHSCOPE_API_KEY`

启动前示例：

```bash
export DASHSCOPE_API_KEY=your_new_key
python -m app.main
```

### 2) 模型配置与资产对齐（P1）

- 当前按本地已有资产对齐为：
  - `embedding_profiles`: 仅 `zh`
  - `reranker_profiles`: 仅 `en`
- `reranker_model_path` 置空，避免默认模型路径与本地资产不一致

### 3) OCR 插件路径修复（P1）

- `app/config.json` 中 `ocr_engines.mineru.module` 已统一为：
  - `app.core.mineru_ocr`

### 4) 路径风格与示例配置（P2）

- `app/config.json` 模型路径统一为 Unix 风格 `/`
- `app/config.example.json` 中 embedding 示例 onnx 路径统一为：
  - `../models/embedding/zh/model.onnx`
  - `../models/embedding/en/model.onnx`

### 5) 端侧大模型 CPU 本地部署支持（Phase 0-4 骨架）

- **新增 LLM 路由层** (`app/core/llm_router.py`)：支持按 `task_profile` 自动选择主模型（main）/ 侧车模型（small）/ 云端兜底（cloud_fallback），预置 Qwen3.6-27B 主模型与 Llama 3.2-3B 侧车模型路由
- **统一失败升级策略** (`app/services/local_llm_runtime.py`)：本地模型异常或无效结果时自动回退云端，高风险税务匹配支持强制复核
- **JSON 输出容错** (`app/services/json_guard.py`)：自动修复 fenced JSON / 尾逗号 / 包裹文本等端侧模型常见脏输出
- **业务链路接入**：`tax_contract_parser`、`tax_matcher`、`tax_risk`、`contract_audit`、`memory_pipeline callbacks` 均已接入任务画像路由与 fallback 策略
- **并发收敛**：税务链路支持按本地执行配置收紧 worker 上限（`tax_match_max_workers` / `tax_risk_max_workers`）
- **回归入口** (`bin/run-edge-llm-regression.ps1`)：一键执行端侧全链路回归（53 项测试通过）
- **配套文档**：
  - `plan/edge-llm-cpu-local-deployment-detailed-design.md` — 详细设计文档（含阶段进度与测试记录）
  - `plan/edge-llm-cpu-local-deployment-runbook.md` — 部署与联调手册
  - `plan/edge-llm-cpu-local-known-issues.md` — 已知问题清单

