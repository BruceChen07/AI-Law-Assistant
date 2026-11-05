# 异步审计队列（llamacpp 线）

在 `feature/local-llm-llamacpp` 上以**开关方式**移植的异步审计能力。默认关闭，行为与原有同步审计完全一致。

## 开关

```json
{
  "audit_async_enabled": false,
  "audit_queue_resume_on_startup": true
}
```

- `audit_async_enabled=false`：请求内同步跑完，返回完整结果（原行为）；
- `audit_async_enabled=true`：提交立即返回 `202`，审计在后台单 worker 队列里执行。

## 为什么是开关

llamacpp 线原来是同步审计，没有进度表/队列。移植时把异步内核做成**纯新增**：
新表、新模块、新端点、新前端轮询，通过配置开关切换，避免影响正在使用的同步链路。

## 组成

| 位置 | 作用 |
|---|---|
| `app/core/migrations/_12_audit_queue.py` | `contract_audit_progress` + `audit_queue` 两张表 |
| `app/services/audit_task_runner.py` | 单 worker FIFO、入队/快照/取消/重启恢复、摘要优先 |
| `app/services/crud.py` | 进度与队列的持久化读写 |
| `app/api/routers/contracts/queue.py` | `task` / `queue` / `cancel` 端点 |
| `app/api/routers/contracts/audit.py` | 异步分支 + 进度端点优先读库 |
| `web/App.jsx` | 提交后轮询任务、队列面板、取消排队 |

## 行为

- **串行**：同一时刻只跑一个审计；
- **摘要优先**：任务开始先生成合同摘要（`stage=summarizing`），再进审计流水线；失败不影响审计；
- **重启恢复**：入队即落库，启动时按入队顺序恢复（`audit_queue_resume_on_startup`）；
- **取消**：仅排队中的任务可取消，运行中返回 409；
- **进度**：`GET /contracts/audit/{id}/progress` 优先读库（`durable=true`）。

## 实测

在 `audit_async_enabled=true` 下用 DOCX 合同验证：

```
POST /contracts/audit → 202 {"mode":"async","queued":true,"queue_position":1}   # 285 ms
GET  /contracts/audit/queue → running: [该任务]
GET  /contracts/audit/{id}/task → stage=summarizing → auditing → done
队列随后清空；同步模式（开关关闭）回归测试 15 项通过。
```

## 已知限制

- 异步模式下 PDF 的数字化步骤仍在请求内完成（约 80 秒），之后才入队；
- 运行中的审计不可取消（需要流水线协作式中断点）；
- 摘要优先阶段会占用唯一的 llama.cpp 推理槽位，摘要完成前审计不开始。
