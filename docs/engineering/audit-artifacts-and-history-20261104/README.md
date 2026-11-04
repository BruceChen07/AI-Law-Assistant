# 审计结果归档与历史审计页面

- 实施日期：2026-11-04
- 诉求：审计结果必须能在**本地目录**长期查看（前端关掉也不受影响），并提供历史审计结果浏览页面。

---

## 1. 结果现在存在哪里

| 位置 | 内容 | 说明 |
| --- | --- | --- |
| `data/app.db` → `contract_audit` | `result_json`（完整审计结果）、`status`、模型信息 | 数据库主副本 |
| `data/app.db` → `contract_audit_chunk` | 每个 chunk 的条款 ID、原文区间、状态、尝试次数、原始响应 | 可追溯 / 可复用 |
| `data/app.db` → `contract_audit_progress` | 进度、计划快照、最后心跳 | 断点续跑 + 前端恢复 |
| **`data/audits/<audit_id>/`** | **纯文件归档（新增）** | 前端/数据库都不可用时仍可读 |
| `data/audits/index.jsonl` | 追加式索引入口 | 每行一次审计的摘要 |
| `logs/YYYY-MM-DD/main.log`、`data/memory/debug/contract_audits_*` | 运行日志与调试 trace | 排障用 |

### 单个审计的归档文件

```
data/audits/aee3449b-4380-472b-9523-e57e9d409f9c/
├── report.json     完整结果（audit 或 error），可直接给下游消费
├── meta.json       运行元数据（execution_path、chunks、覆盖率、review 次数、耗时）
├── chunks.jsonl    每行一个 chunk：clause_ids / source_spans / status / attempts / reviewed / tokens / prompt_hash
└── report.md       人类可读报告（风险概览 + 逐条风险：级别/问题/建议/依据/条款）
```

实测（571 条款合同）：`report.json` 41.6 KB、`chunks.jsonl` 31.4 KB、`report.md` 7.2 KB、`meta.json` 961 B。

- 目录可用 `audit_artifact_dir` 配置覆盖，默认 `<data_dir>/audits`。
- 写入时机：后台任务结束时（成功/失败都写）由 `audit_task_runner` 调用 `write_audit_artifacts`；写入失败只记日志，不影响审计。
- 历史审计（本功能上线前的 35 条）可用 `POST /contracts/audits/{audit_id}/artifacts/rebuild` 从数据库补档。

## 2. 历史审计页面

- 入口：顶栏 **「审计历史」** 按钮 或 地址 `http://localhost:5173/#history`。
- 列表字段：合同文件名、状态、时间、风险数（高/中/低）、`audit_id`、**本地存档目录**、已生成的存档文件与大小。
- 操作：
  - **查看结果**：把历史结果载入主审计页（结果面板 + 合同预览）；
  - **下载报告(MD)** / **下载 JSON**：直接取归档文件（带鉴权）；
  - **生成/刷新存档**：对只有数据库记录的历史审计补写归档文件；
  - 状态筛选（全部/已完成/失败/进行中/已中断）。
- 页面顶部同时显示 `artifact_root`（归档根目录），方便直接去文件系统查看。

## 3. 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/contracts/audits/history?limit=&offset=&status=` | 历史列表（数据库已完成记录 + 无审计行的 running/interrupted 记录），带 `artifact_files` 与 `artifact_dir` |
| GET | `/contracts/audits/{audit_id}/artifacts` | 该审计的归档文件清单 |
| GET | `/contracts/audits/{audit_id}/artifacts/{name}` | 下载单个归档文件（白名单：report.json / meta.json / chunks.jsonl / report.md，防目录穿越） |
| POST | `/contracts/audits/{audit_id}/artifacts/rebuild` | 从数据库重建归档 |

## 4. 测试

`tests/test_audit_artifacts.py`（5 用例）：归档文件内容与索引、仅数据库审计的补档、历史接口（已完成 + 进行中）、存档清单/下载/重建/路径穿越拦截、后台任务自动归档。

全量回归：`242 passed` + 既有 3 个编码类失败与 1 个 passlib 环境 error（改动前即存在）。

## 5. 已知限制

1. 归档为“结束时一次性写入”，运行中的审计没有实时文件快照（进度以数据库为准）。
2. `chunks.jsonl` 默认不含模型原始响应（原始响应仍在 `contract_audit_chunk.raw_response`），避免归档体积过大。
3. 归档目录不做自动清理，按需手动归档/删除；`index.jsonl` 为追加式，不做压缩。
