# 审计队列（排队执行）

## 为什么需要

单份合同的审计在纯 CPU 上要 8–12 分钟。原实现是**每个审计起一个后台线程**，多个任务同时跑会争抢同一个 llama.cpp 推理槽位，结果都变慢、还互相拖累超时。

队列的目标：
1. 同一时刻只跑一个审计，其余按提交顺序等待；
2. 用户在等待期间可以继续上传合同，先把队排上；
3. 已完成的结果随时可以回看，不受后续任务影响。

## 设计

### 状态机

```
提交 → queued（排队） → running（执行） → done / failed
                     ↘ cancelled（用户取消）
```

- 队列在内存中（FIFO），由唯一的 `audit-queue-worker` 线程消费；
- 每个状态同时写入 `contract_audit_progress` 表，刷新页面仍能看到进度；
- 运行中的任务不允许取消（只能等它结束），排队中的可以取消。

### 后端

| 位置 | 作用 |
|---|---|
| `app/services/audit_task_runner.py` | `enqueue()` 入队、单 worker 串行消费、`queue_snapshot()`、`cancel()` |
| `POST /contracts/audit` | 上传后入队，返回 `queue_position`（含正在执行的那一个） |
| `POST /contracts/{id}/pipeline/run` | 重新审计同样入队 |
| `GET /contracts/audit/queue` | `{running: [...], queued: [{position, ...}]}` |
| `DELETE /contracts/audit/queue/{audit_id}` | 取消排队中的任务；运行中返回 409 |
| `GET /contracts/audit/{id}/task` | 原有接口，`status` 现在会出现 `queued` |

### 前端

- 提交后若前面还有任务，进度区提示「已加入队列，前面还有 N 个任务」；
- 页面新增「审计队列」面板，每 5 秒刷新，列出执行中与排队中的合同，排队项可一键取消；
- 已完成的任务通过原有结果区/历史记录查看，不占用队列。

## 行为细节

- **串行**：worker 线程常驻，`_pending` 为空时阻塞等待；同一时刻 `_running` 最多一条；
- **摘要优先**：任务开始后先生成合同摘要（`stage=summarizing`，约 1–2 分钟），完成后再进入审计流水线；摘要结果写入缓存，接口可秒回，摘要失败不影响审计；
- **位次**：`ahead` = 排队序号 + （是否有任务在执行）；前端显示 `ahead + 1`；
- **持久化**：进度写库，页面刷新/切标签仍可恢复；
- **重启**：内存队列不跨进程保留，重启后排队中的任务会丢失（进度行停留在 `queued`，可由既有 stale 清理标记为中断），需要用户重新提交——这是当前版本的已知限制。

## 验收

```
# 连续提交两个合同
POST /contracts/audit  → queue_position=1
POST /contracts/audit  → queue_position=2   # 前面还有 1 个

GET /contracts/audit/queue
→ {"running":[{...A}], "queued":[{...B,"position":1}], ...}

DELETE /contracts/audit/queue/{B}
→ {"status":"cancelled"}
```

实测：A 进入 running、B 停在 queued，取消 B 后队列清空，A 正常完成；运行中取消返回 409。
