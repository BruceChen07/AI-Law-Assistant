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

## 提交前建议（避免把运行产物提交到仓库）

当前工作区除了代码外，还包含运行产物与环境文件（例如 `data/app.db`、`web/node_modules/*`、`web/dist/*`、测试导入的合同文件等）。建议分两步提交：

1. 先只提交代码与配置文档变更  
2. 再检查是否有需要保留的产物文件（通常不建议提交）

可参考以下命令快速检查：

```bash
git status --short
git diff --name-only
```

如果你要仅提交本轮核心改动，可按文件显式添加（示例）：

```bash
git add app/config.json app/config.example.json app/core/llm.py README.md
```

然后确认提交内容：

```bash
git diff --cached --name-only
```
