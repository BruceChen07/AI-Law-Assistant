# 分批审计落库 / 断点续跑 / 复盘重审（P1 → P3 → P2）

- 实施日期：2026-11-04
- 触发问题：351 KB、571 条款的合同一轮 v2 审计跑了 67 分钟，在最后一次 LLM 调用时因 `WinError 10054`（llama-server 断开）整轮失败，42 次调用全部作废；期间 DB 无记录、前端刷新后彻底失去任务。
- 目标（按实施顺序）：
  1. **P1** 每个 chunk 结果即时落库，可按 `(source_hash, plan_hash)` 复用已完成 chunk；
  2. **P3** 审计过程与结果持久化，刷新 / 换浏览器 / token 失效后仍可恢复，错误语义可执行；
  3. **P2** 聚合后做一次复盘，只对被 flag 的 chunk 回取原文定点重审。

---

## 1. 数据结构

新增两张表（`app/core/database.py`，`init_db` 幂等迁移，含 `reviewed` 列的 ALTER 兜底）：

### `contract_audit_chunk` —— 每 chunk 一行

| 字段 | 说明 |
| --- | --- |
| `audit_id`, `chunk_id` | 复合主键；同一审计内唯一 |
| `source_hash` | 合同文本 sha256，复用匹配键之一 |
| `plan_hash` | chunk 计划指纹（chunk_id + unit_ids + prompt_hash），复用匹配键之二 |
| `clause_ids`, `source_spans` | 该 chunk 覆盖的条款 id 与原文区间（start/end），**重审时据此回取原文** |
| `prompt_hash`, `prefix_hash` | 提示词指纹，便于核对提示变化 |
| `input_tokens`, `output_tokens` | 计划预算与实际输出预算 |
| `attempts`, `status` | 尝试次数与结果（`ok` / `parse_failed` / `truncated`） |
| `raw_response` | 模型原始返回（默认上限 20000 字符，可关） |
| `parsed_json` | 归一化后的 chunk 审计结果 |
| `error`, `reviewed` | 失败原因 / 是否已被复盘重审过 |

### `contract_audit_progress` —— 一条审计一行（持久进度）

`audit_id` 主键，记录 `status`、`stage`、`percent`、`message`、`detail_json`、`document_id`、`user_id`、`plan_json`（计划快照）、`plan_hash`、`source_hash`、`chunks_total`。

---

## 2. P1：逐 chunk 落库 + 复用

- 新增 `app/services/contract_audit_modules/chunk_store.py`：`persist_plan` / `record_chunk` / `load_reusable_chunks` / `reusable_round_entry` / `chunk_plan_signature`。
- `_build_v2_classic_audit`：
  - 规划完成后写计划快照（`chunks_total`、`plan_hash`、`source_hash`）；
  - 每个 chunk 结束即 `UPSERT`（**失败也写**，`status=parse_failed/truncated` 是复盘的输入）；
  - 复用：按 `(source_hash, plan_hash)` 命中 `status='ok'` 的行则跳过 LLM，直接由 `parsed_json` 重建 round 条目，并**为新审计补写自己的 chunk 清单行**，保证每次审计都可追溯；
  - 3 次落库异常均只记 warning，不阻断审计。
- `_build_classic_audit` 增加 `capture_raw_response`（默认关闭），仅 chunk 路径开启，避免单轮 classic 结果膨胀。
- 配置（可选，全部有默认值）：`audit_chunk_persistence = {enabled, reuse_enabled, store_raw_response, max_raw_chars}`。

## 3. P3：进度持久化 + 恢复 + 错误语义

- `_set_audit_progress` 增加 `cfg/document_id/user_id`，同步写 `contract_audit_progress`；三个生命周期点（received → running → done/failed）全部落库。
- `GET /contracts/audit/{audit_id}/progress`：内存优先、缺失时回落到 DB，并在两者都存在时合并 `document_id` / `chunks_total`。
- 新增 `GET /contracts/audit/{audit_id}/task`：返回 `status`、`progress`、`document_id`、`has_result` 与已完成结果，用于刷新/换设备后恢复。
- 新增 `GET /contracts/{document_id}/audits`：该文档最近的审计任务列表（含 running）。
- `audit_id` 现在从接口贯通到 pipeline（`run_contract_pipeline_bundle(audit_id=...) → audit_contract(audit_id=...) → _build_v2_classic_audit`），chunk 行与进度行共用同一 id（此前 pipeline 会自造 `audit_<hex>`，导致两张表对不上）。
- 错误语义：`COVERAGE_INCOMPLETE` → 422 + “条款规模超出计划容量”文案；`INVALID_MODEL_JSON` / `MODEL_RESPONSE_TRUNCATED` → 502 + “已完成批次会保留，可重试复用”。
- 前端（`web/App.jsx`）：
  - 审计开始把 `auditId` 写入 `localStorage.active_contract_audit_id`；
  - 挂载时读回并调用 `/task`：done 直接还原结果与预览，running 继续轮询，failed 显示原因；
  - 轮询遇到 401 不再静默：停止轮询并提示“登录状态已失效，重新登录后自动恢复任务”；`ensureOk` 现在会带上 `error.status`。

## 4. P2：复盘 + 定点重审

- v2 在覆盖校验通过后先聚合一次，再调用现成的 `build_reliability_review_plan`（此前只有 classic 接）：
  - 复盘输入是聚合结果的结构化清单（解析失败、截断、高风险缺引用、跨 chunk 冲突），**不是摘要去审摘要**；被 flag 的 chunk 用 `source_spans` 回取原文重审；
  - 受 `audit_reliability_policy.max_retry_per_chunk / max_retry_chunks` 双重约束，超限只记录 `retry_limit_reached`；
  - 重审成功后替换该 chunk 的 round 条目并更新其落库行（`attempts+1`、`reviewed=1`）。
- 复用 + 复盘组合：若复用行的 `reviewed=1`，跳过重复复盘（记录 `already_reviewed_reused`），避免每次重跑都重复同一次重审。
- 新增 meta：`review_retry_applied_count`、`review_retry_chunk_ids`、`chunk_reused_count`、`plan_hash`、`reliability{reliability_level, retry_count, ...}`；raw 中记录 `review_plan` 与 `review_retry_logs`。

---

## 5. 实测验证（本机真机，llama.cpp + Qwen3.6-35B-A3B Q4，16k 上下文）

| 场景 | 结果 |
| --- | --- |
| 2 条款小合同首次审计 | 1 chunk，1 次 chunk 调用 + 1 次复盘重审，约 130 s；`contract_audit_chunk` 落 1 行（`attempts=1`→重审后 2），`raw_response` 约 2 KB |
| 同一文件再次审计（首次复用） | `chunk_reused_count=1`，仍执行 1 次复盘重审，132.2 s，落库 `reviewed=1` |
| 同一文件第三次审计 | `chunk_reused_count=1`、`review_retry_applied_count=0`，**1.7 s 返回**（零 LLM 调用） |
| `GET /contracts/audit/{id}/task` | `status=done`、`has_result=true`、`progress=100`、`document_id` 正确 |
| `GET /contracts/{doc}/audits` | 返回该文档审计列表（含 running） |
| 后端进程内无进度时 | `/progress` 从 DB 回落并返回 `durable=true` |
| 571 条款合同 v2 计划 | 25 chunks（每 chunk 1–4 条，固定前缀 5449 token，见上一轮排查结论） |

历史数据无影响：34 条既有 `contract_audit` 记录保持不变，新表为空表增量创建。

## 6. 顺手修复（阻碍服务重启）

`bin/start-services.ps1` 三处缺陷（Windows PowerShell 5.1 下必现）：

1. `Get-Content config.json -Raw | ConvertFrom-Json` 用 ANSI 读 UTF-8 → 中文配置解析失败；改为 `-Encoding UTF8`。
2. 函数参数名 `$host` 与只读自动变量冲突；改为 `$HostName`。
3. `Start-Process` 的 stdout/stderr 指向同一文件 → “RedirectStandardOutput and RedirectStandardError are same”；拆成 `*.log` / `*.err.log`。

## 7. 测试

- 新增 `tests/test_audit_chunk_store.py`（10 用例）：逐 chunk 落库字段完整性、二次运行零调用复用、失败 chunk 落库且不被复用、复盘只重审被 flag 的 chunk、`max_retry_per_chunk` 限流、`reviewed` 跳过重复复盘、进度落库/读取、`/progress` 的 DB 回落、`/task` 结果恢复与 404。
- 全量回归：`229 passed`，另有 4 个与本改动无关的既有失败（中文文件名/编码与 passlib 哈希环境问题），改动前即存在。

## 8. 已知限制

1. **classic（非 v2）路径尚未接 chunk 落库与复盘**，只有 `write_round_trace` 的调试 JSONL；开启 `audit_optimization.enabled=true` 才走本机制。
2. 复用以 `plan_hash` 为键，**调整 `audit_optimization` 分组参数会使旧 chunk 失效**（这是期望行为，避免复用过期切分）。
3. 复盘重审每个 chunk 每次运行最多 1 次（`max_retry_per_chunk`），已 `reviewed` 的复用结果不再重审；如需强制重审，清理对应 `contract_audit_chunk` 行或改配置。
4. 前端恢复依赖浏览器 `localStorage` 与同一后端库；换设备需通过 `/contracts/{document_id}/audits` 自行定位。
5. 571 条款合同首轮仍需 25 个 chunk 串行调用（约 30–55 分钟），但中途失败只补跑剩余 chunk，不再整轮清零。
