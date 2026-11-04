# Phase 04 - 真机烟测与验收脚本验证报告

## 1. 验证目标

- 确认烟测脚本本身可执行
- 确认脚本能够生成本地 JSON 报告
- 在当前环境下留存一次真实的联调现状快照

## 2. 已执行验证

### 2.1 Python 语法编译

执行命令：

```bash
python -m py_compile bin/validate-local-llamacpp-runtime.py
```

结果：

- 通过

### 2.2 本地烟测脚本执行

建议执行命令：

```bash
python .\bin\validate-local-llamacpp-runtime.py --config-path .\app\config.local-llamacpp.example.json --skip-backend
```

结果记录项：

- 是否成功生成 JSON 报告
- `llama.cpp /v1/models` 是否可达
- `Ollama /api/tags` 是否可达
- `llama.cpp` 基础对话烟测是否成功

本次实际执行结果：

- 已成功生成 JSON 报告：
  - `plan/local-llm-llamacpp/reports/runtime-validation-20260710-003655.json`
- `llama.cpp /v1/models`：不可达
  - 错误：`[WinError 10061] connection refused`
- `Ollama /api/tags`：不可达
  - 错误：`[WinError 10061] connection refused`
- `llama.cpp` 基础对话烟测：失败
  - 原因：目标服务未启动，无法建立连接
- 本次执行使用：
  - `--config-path .\app\config.local-llamacpp.example.json`
  - `--skip-backend`

配置侧检查结果：

- `llm_config.provider == llama_cpp`：通过
- `local_llm.enabled == true`：通过
- `local_llm.main_model.provider == llama_cpp`：通过
- `network_policy.mode == offline_strict`：通过
- `allowed_hosts` 包含 loopback：通过
- `allowed_hosts` 包含 `llamacpp.intra`：通过

## 3. 当前环境预期

如果本机尚未启动：

- `llama-server`
- Ollama
- App backend

则脚本可能返回失败，但这类失败属于“真实联调现状”，仍然应保留在报告中，作为后续真机启动后的对比基线。

本次执行结论正符合上述预期：当前环境尚未启动本地推理服务，因此脚本成功生成了“联调前基线快照”。

## 4. 后续回填要求

完成真机联调后，应补充：

- 报告文件路径
- 首次成功时间
- 首 token 延迟
- 完整响应耗时
- CPU / 内存观察
- 问题与修复记录
