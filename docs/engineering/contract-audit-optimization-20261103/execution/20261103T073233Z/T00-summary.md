# T00 执行记录

## 输入与依赖

- 设计版本：1.0
- Git：`feature/contract-audit-optimization-v2` / `ca8da9da042e362a624ec1157f3113cc325cfe53`
- 启动时工作树：35项已跟踪或未跟踪变更，全部保留

## 修改

- 新增本运行证据目录及 `baseline.json`。
- 未修改生产配置、业务数据库、模型或服务进程。

## 验证

| 检查 | 结果 | 证据 |
|---|---|---|
| 模型配置 | `llama_cpp`，`127.0.0.1:18080/v1` | `baseline.json` |
| 端口 | 18080/8000未监听；独立Ollama 11434监听 | `baseline.json` |
| 关键哈希 | 配置、数据库、GGUF、运行时已记录 | `baseline.json` |
| 工具版本 | Python/pytest/Node/npm/Git已记录 | `baseline.json` |

## 风险与未完成项

- `llama-server.exe` 观察到仅9216字节，P6必须校验完整运行包和DLL，不能据此认定可部署。
- 当前服务未启动，本任务未执行真实模型能力探测。
- Ollama正在运行，但项目配置不连接11434。

## 下一步

- T01：验证测试配置和数据隔离。
- 不得执行生产服务重启、生产库迁移或模型删除。
