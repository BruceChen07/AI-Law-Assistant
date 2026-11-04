# 审计任务解耦与容错（P0 → P1 → P2 → P3）

- 实施日期：2026-11-04
- 触发问题：后台仍在跑，前台进度条已结束、且页面没有任何“服务在运行”的信号（见前一份 `audit-resume-and-review-20261104` 记录的现象）。
- 说明：本文件的 P0/P1/P2/P3 与上一份文档的 P1/P2/P3 **不是同一组编号**，后者指“chunk 落库 / 进度持久化 / 复盘重审”。

---

## 1. 根因（核实结论）

| 编号 | 根因 | 证据 |
| --- | --- | --- |
| R-01 | 提交与执行耦合在一个 HTTP 请求里，前端 90 分钟上限一到就按“失败”处理 | `run_in_threadpool` 阻塞到整轮结束；`API_CONTRACT_AUDIT_TIMEOUT_MS = 90min` |
| R-02 | 后端不感知客户端断开，也不存在请求级超时 | `app_factory` 无 `is_disconnected`/`timeout`；`app/main.py` 为裸 `uvicorn.run` |
| R-03 | 失败分支清空 `documentId`/结果，进度卡仅在 loading/failed 时渲染 | `App.jsx` 旧 `catch` 与 `showAuditProgress` |
| R-04 | 进程被杀/重启后 `running` 行无人收尾（实测出现永久 running） | `contract_audit_progress` 无过期回收 |
| R-05 | 网络错误时前端会换 base 重放同一 POST，可能重复发起审计 | `requestWithFallback` 旧实现 |

## 2. P0：提交与执行解耦

- 新增 `app/services/audit_task_runner.py`：`submit()` 起守护线程执行 `run_contract_pipeline_bundle`，线程内把进度写入 `contract_audit_progress`，结束时写 `contract_audit(done/failed)` 并把进度置终态；进程内 `_running` 注册表提供 `is_audit_running()` / `running_audit_ids()`。
- `POST /contracts/audit` 与 `POST /contracts/{document_id}/pipeline/run` 现在**只做登记 + 202 立即返回**：

```json
{ "audit_id": "...", "document_id": "...", "status": "running", "stage": "auditing", "progress": 20, "mode": "async" }
```

- 结果不再由 POST 返回，一律通过 `GET /contracts/audit/{id}/task`（`has_result` / `result`）获取。
- 错误文案从路由层抽到 `app/services/audit_error_messages.py`，后台线程与 HTTP 层共用同一套映射（`COVERAGE_INCOMPLETE` → 容量类文案等）。

## 3. P1：前端容错

- `web/api/base.js` 新增 `isTransportError()`：`RequestTimeoutError` / `AbortError` / 标记了 `transport` 的网络错误都视为“传输层失败”，**不再等同于审计失败**。
- `App.jsx` 提交后不再等待结果：拿到 202 即 `trackBackgroundAudit(audit_id)`，由 `/progress` + `/task` 驱动 UI；`activeAuditId` 让**进度卡常驻**（不再只在 loading/failed 时出现）。
- 传输层失败 → 保留任务跟踪并提示“请求已中断，任务可能仍在后台运行”，**不清空 documentId/结果**；只有服务端明确 4xx/5xx 才进入失败态并复位。
- 已有 401 提示与 `localStorage` 恢复继续生效；恢复路径会把 `activeAuditId` 置回，终态时清除。

## 4. P2：可见性与自愈

- `mark_stale_contract_audit_progress(cfg, stale_after_minutes)`：把心跳超时的 `running` 行标记为 `interrupted`（保留 chunk 结果，提示可复用续跑）。
- `create_app` / `init_only` 启动时执行该回收，并记录 `audit_recovery_marked_interrupted count=…`；阈值可由 `audit_task_recovery.stale_after_minutes` 覆盖（默认 10 分钟）。
- `GET /health` 增加 `audits_running`（只读探测，DB 不存在时不会创建文件）。
- 新增 `GET /contracts/audits/running`：返回运行中任务（含 `stale_seconds` 与 `stale_after_seconds`）。
- 前端每 30s 轮询该接口，空闲时在进度区显示“服务端还有 N 个审计在运行”，可一键 `继续跟踪`。

## 5. P3：防重复与幂等

- `requestWithFallback` 只对幂等方法（GET/HEAD/OPTIONS）做多 base 重试；**非幂等请求（提交审计）一旦连接异常立刻抛错**，绝不换 base 重放。
- 提交端点增加幂等守卫：同一 `audit_id` 若已有 `running` 任务 → `409 AUDIT_ALREADY_RUNNING`（在写文件/建文档之前拦截，无副作用）。
- 前端收到 409 不再报错，而是切到“继续跟踪原任务”。

## 6. 行为变化（兼容性）

| 变更 | 影响 |
| --- | --- |
| `POST /contracts/audit` 返回码 200 → **202**，响应体不含 `result` | 旧前端/脚本需改用 `/contracts/audit/{id}/task` |
| `POST /contracts/{document_id}/pipeline/run` 同样 202 | 同上 |
| 新增 `409` | 同一 `audit_id` 重复提交被拒 |
| 新增 `interrupted` 状态 | 进程重启后遗留任务不再永久 `running` |

## 7. 测试

`tests/test_audit_task_runner.py`（8 用例）：僵尸任务回收、running 列表、`/health` 计数、202 提交并交接后台、409 幂等拦截、后台成功/失败落库与用户文案、运行注册表生命周期。

全量回归：`237 passed`，仍有 3 个失败 + 1 个 error 为改动前既有的中文编码/文件名与 passlib 环境问题。

## 8. 已知限制

1. 后台任务是**进程内守护线程**，不是持久任务队列：进程退出即中断。兜底是启动扫描标记 `interrupted` + chunk 复用（重试只补剩余 chunk）。
2. 未实现 SSE/WebSocket 推送，仍为 1s 轮询；任务量大时建议改为事件流。
3. `running_audit_ids()` 只在单进程内有效；多 worker 部署时以 DB `contract_audit_progress` 为准（对外接口均走 DB）。

---

## 9. 真机验证（571 条款真实合同 + 小合同冒烟）

| 场景 | 实测结果 |
| --- | --- |
| 改造前那次 571 条款审计（旧同步流程） | 16:29:09 起跑，58 chunks，**58 个 chunk 全部 `ok`**，复盘重审 6 个 chunk，17:54:40 完成，**85.5 分钟**，产出 10 条风险（high 0 / medium 9 / low 1），`contract_audit` 已落库 |
| `POST /contracts/audit`（新异步） | **202，0.28–0.32 秒返回** `{"status":"running","mode":"async"}` |
| 同一 `audit_id` 立即重复提交 | **409** `AUDIT_ALREADY_RUNNING`，未产生第二次执行 |
| `GET /contracts/audits/running` | 返回 `{total:1, items:[{audit_id, percent:40, stale_seconds:0}]}` |
| `GET /health` | 新增 `"audits_running": 0` |
| `GET /contracts/audit/{id}/task` | 进度随后台任务推进（40% → 100%），完成后 `has_result=true`，`meta` 含 `execution_path/chunks_total/chunk_reused_count/review_retry_applied_count` |
| 同一文件二次提交（复用） | `chunk_reused_count=1`、`review_retry_applied_count=0`，全程 **数十秒** 完成 |

### 验证中修掉的一个真实缺陷

首轮冒烟发现 `/task` 的进度**永远停在 20%**、完成后的 `meta` 为空：因为 `_load_audit_progress` 优先返回内存态（提交握手的 5/10/20%），而后台任务只写 DB。已改为**以 DB 行为准**（内存仅作兜底并合并 detail），修复后进度与 meta 均正常。

## 10. 部署注意

1. 前端需**硬刷新**一次以加载新 bundle；旧 bundle 仍按“POST 返回结果”处理。
2. 本轮改造后 `POST` 不再返回审计结果，外部脚本必须改为轮询 `/contracts/audit/{id}/task`。
3. 后端重启会把仍在跑的任务交给启动回收逻辑标记为 `interrupted`（10 分钟阈值），重试时已完成 chunk 直接复用。
