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

