# 条款数量上限（160 条）排查报告

- 报告日期：2026-11-04
- 触发事件：`POST /contracts/audit` 返回 500，日志 `COVERAGE_INCOMPLETE: legacy audit plan exceeds max_rounds`
- 涉及模块：条款切分 / token 预算 / classic 多轮审计计划 / 审计结果存储 / 前端上传
- 复现资产：`repro_clause_coverage_limit.py`、`repro_results.json`、`repro_stdout.log`（同目录）
- 结论级别：**确认根因，可稳定复现**

---

## 1. 结论摘要

系统"最多只支持 160 条条款"不是数据库限制，也不是前端校验限制，而是 **classic 旧版多轮审计计划器的静态容量上限**：

```
容量 = max_rounds(20) × max_clauses_per_group(8) = 160 条
```

当前运行配置 [config.json](file:///d:/Workspace/AI-Law-Assistant/app/config.json) **既没有 `audit_token_policy` 段，也没有 `audit_optimization` 段**，因此：

1. `max_rounds` 落到代码默认值 20（[audit_token_policy.py#L78](file:///d:/Workspace/AI-Law-Assistant/app/services/audit_token_policy.py#L78)）；
2. `max_clauses_per_group` 落到代码默认值 8（[audit_token_policy.py#L72-L73](file:///d:/Workspace/AI-Law-Assistant/app/services/audit_token_policy.py#L72-L73)）；
3. `audit_optimization` 段缺失使 v2 计划器保持关闭（[contract_audit.py#L1451-L1458](file:///d:/Workspace/AI-Law-Assistant/app/services/contract_audit.py#L1451-L1458)），只能走 classic multipass；
4. 记忆模块被运维配置临时禁用（[config.json#L225-L230](file:///d:/Workspace/AI-Law-Assistant/app/config.json#L225-L230)），进一步锁定 classic 路径。

当 `ceil(条款数 / 8) > 20`（即 161 条及以上）时，[plan_clause_groups](file:///d:/Workspace/AI-Law-Assistant/app/services/audit_token_policy.py#L186-L205) 抛 `LegacyAuditCoverageError`，审计在**调用 LLM 之前**即中止，接口 500。

真实失败文档实测为 **571 条条款**，需要 **72 轮**，是该上限的 3.6 倍。

---

## 2. 排查范围与方法

| 项目 | 内容 |
| --- | --- |
| 静态走查 | 条款切分、token 预算、classic/v2 调度、DB 读写、API 路由、前端上传与预览 |
| 配置解析 | 以运行态 `app/config.json` 实际加载结果为准（`load_config()`），不使用示例配置推断 |
| 模拟测试 | 合成 159/160/161/200/571 条合规条款，覆盖计划器、完整 pipeline（桩 LLM）、落库、错误映射 |
| 真实样本复算 | 对线上失败文件 `data/uploads/cache/73ec9496-....docx` 重新提取文本并切分 |
| 对照组 | 开启 `audit_optimization`（v2）后重跑 200/571 条，验证修复方向 |

运行环境：Windows / Python 3.12.0 / SQLite 3.42.0 / 项目虚拟环境 `app/.venv`。

模拟中的 LLM 使用 `FakeLLM` 桩（固定返回合法 JSON），因此本报告的耗时**不代表真实模型耗时**，仅用于验证编排、异常与数据落库行为。

---

## 3. 条款管理与审计链路梳理

```
上传 .docx/.pdf
  └─ extract_text_with_config            文本抽取                contracts.py#L128-L174
  └─ build_preview_clauses               逐行切分条款            clause_builder.py#L13-L29
       └─ split_contract_clauses         "每一非空行 = 一条条款"  tax_contract_parser.py#L44-L81
  └─ build_contract_audit_budget         token 预算 / 是否多轮     audit_token_policy.py#L82-L172
  └─ memory? → 临时禁用则 classic         路径分发                contract_audit.py#L2110-L2206
       └─ classic v2? → audit_optimization.enabled=false 则跳过   contract_audit.py#L1813-L1825
       └─ requires_multi_pass → _build_multipass_classic_audit
            └─ plan_clause_groups        ★160 条容量上限          audit_token_policy.py#L175-L205
            └─ 每轮 _run_multipass_chunk_round → LLM              contract_audit.py#L1095-L1244
  └─ 结果聚合 aggregate_chunk_audit_results                         result_aggregator.py#L28+
  └─ 落库 contract_audit.result_json（单条 JSON）                    crud.py#L89-L97
```

### 3.1 运行时实际生效参数（实测）

| 参数 | 运行值 | 来源 |
| --- | --- | --- |
| `audit_token_policy` 段 | 不存在 | `app/config.json` |
| `audit_optimization` 段 / v2 | 不存在 / 关闭 | `app/config.json` |
| `default_output_tokens` | 2048 | 取自 `llm_config.max_tokens=2048`（[config.json#L63-L72](file:///d:/Workspace/AI-Law-Assistant/app/config.json#L63-L72)） |
| `context_window_tokens` | 16384 | 代码默认 |
| `reserve_output_tokens` / `safety_margin_tokens` | 2048 / 1024 | 代码默认 |
| `input_soft_limit_tokens` / `input_hard_limit_tokens` | 11776 / 13312 | 代码默认推导 |
| `clause_group_target_tokens` | 4200 | 代码默认 |
| `max_clauses_per_group` | **8** | 代码默认 |
| `max_rounds` | **20** | 代码默认 |
| 理论容量 | **160 条** | 20 × 8 |

### 3.2 关键业务规则代码

限制不是一条显式的"最大条款数"校验，而是由 [plan_clause_groups](file:///d:/Workspace/AI-Law-Assistant/app/services/audit_token_policy.py#L175-L205) 的组包逻辑隐式产生的：

```python
should_wrap = bool(current_group) and (
    len(current_group) >= max_clauses_per_group          # 默认 8，真实约束
    or current_tokens + clause_tokens > target_tokens    # 4200，本场景未触发
)
if should_wrap:
    groups.append(current_group)
    ...
if len(groups) >= max_rounds:                            # 默认 20
    raise LegacyAuditCoverageError("COVERAGE_INCOMPLETE: ...")
```

注意：`raise` 发生在**新组已满 20 个、还有剩余条款时**，因此通过上限恰好是 `20 × 8 = 160`，第 161 条即失败。

---

## 4. 根因分析（分层）

### RC-01（直接根因）静态轮次上限 × 每组条款数 = 160

- 位置：[audit_token_policy.py#L183-L205](file:///d:/Workspace/AI-Law-Assistant/app/services/audit_token_policy.py#L183-L205)
- 约束公式：`required_rounds = ceil(N / min(max_clauses_per_group, floor(4200 / avg_clause_tokens)))`
- 本场景实测平均条款 token 仅 **68.8**（真实文档）/ **108**（合成 200 条），8 条一组仅约 550 / 864 token，远低于 4200 目标，因此 **`max_clauses_per_group=8` 是唯一生效的约束**，token 预算并未起到扩容作用。
- 结果：容量被硬编码式地固定在 160 条，与合同实际文本规模无关。

### RC-02 运行配置缺失，全部落到"对长合同不友好"的默认值

- `app/config.json` 中没有 `audit_token_policy`，运维无法在 Admin 界面调整（前端无对应配置项）。
- 示例配置 [config.example.json#L188-L202](file:///d:/Workspace/AI-Law-Assistant/app/config.example.json#L188-L202) 更保守（`max_rounds=12`，容量仅 96），说明默认值本身没有按长合同设计。

### RC-03 记忆模块临时禁用，锁定 classic 路径

- [config.json#L225-L230](file:///d:/Workspace/AI-Law-Assistant/app/config.json#L225-L230) 中 `memory_temporary_disable.enabled=true`，`fallback_mode=classic`。
- 日志首行 `memory_temporarily_disabled ... clauses=571` 即该分支（[contract_audit.py#L2171-L2202](file:///d:/Workspace/AI-Law-Assistant/app/services/contract_audit.py#L2171-L2202)）。
- 记忆模块自身也有轮次上限（`memory_max_rounds` 被钳制在 8–30，[audit_loop.py#L167](file:///d:/Workspace/AI-Law-Assistant/app/services/contract_audit_modules/memory_pipeline/audit_loop.py#L167)），并非长合同的兜底方案。

### RC-04 v2 计划器未启用

- `audit_optimization.enabled=false`（缺省），[contract_audit.py#L1813-L1825](file:///d:/Workspace/AI-Law-Assistant/app/services/contract_audit.py#L1813-L1825) 直接跳过 v2。
- 对照组实测：同一批 571 条条款，v2 仅需 **6 个 chunk**，`coverage_audited=1.0`，可正常完成。

### RC-05 缺少前置容量校验与错误语义映射（放大故障影响）

- 上传/审计入口只校验扩展名（[contracts.py#L159-L162](file:///d:/Workspace/AI-Law-Assistant/app/api/routers/contracts.py#L159-L162)、[App.jsx#L830-L836](file:///d:/Workspace/AI-Law-Assistant/web/App.jsx#L830-L836)），没有任何条款数/文件规模预检。
- `COVERAGE_INCOMPLETE` 未在错误映射表中命中，[contracts.py#L39-L61](file:///d:/Workspace/AI-Law-Assistant/app/api/routers/contracts.py#L39-L61) 返回通用文案，把用户引向"模型配置"，掩盖真实原因。
- 失败发生在 26.4s 解析/检索之后（线上日志），成本已发生但无任何可用产出。

### 非根因项（已排查，排除）

| 怀疑方向 | 结论 | 证据 |
| --- | --- | --- |
| 数据库存储上限 | **排除**。200 条结果 190,329 B、571 条 544,890 B，写入/回读 sha256 一致；SQLite 单值上限 1,000,000,000 B | 测试 T05 |
| 前端输入校验 | **不构成限制**。仅校验扩展名，无条款数/大小限制；`clause_limit` 参数后端接收后直接忽略 | [App.jsx#L830-L836](file:///d:/Workspace/AI-Law-Assistant/web/App.jsx#L830-L836)、[contracts.py#L487-L517](file:///d:/Workspace/AI-Law-Assistant/app/api/routers/contracts.py#L487-L517) |
| 文本切分失败 | **排除**。真实文档 85,271 字符成功切出 571 条，抽取耗时 157.5ms | 测试 T03 |
| 记忆模块导致 | **排除**。该次请求已被临时禁用，未进入记忆路径 | 线上日志 + 配置 |

---

## 5. 模拟测试记录

> 完整机器可读结果见同目录 `repro_results.json`，运行日志见 `repro_stdout.log`。

### T01 条款计数边界（运行态策略，计划器层）

| 条款数 | 结果 | 轮数 | 最后一轮条款 |
| --- | --- | --- | --- |
| 159 | 成功 | 20 | c153–c159（7 条） |
| 160 | 成功 | 20 | c153–c160（8 条） |
| **161** | **失败：LegacyAuditCoverageError** | — | — |
| **200** | **失败：LegacyAuditCoverageError** | — | — |
| **571** | **失败：LegacyAuditCoverageError** | — | — |

结论：容量边界精确落在 160/161 之间，与公式推导一致。

### T02 完整 classic pipeline（桩 LLM，验证业务流程）

| 场景 | 结果 | LLM 调用 | 进度事件 | 备注 |
| --- | --- | --- | --- | --- |
| 160 条 + 运行态策略 | 成功 | 20 | 41 | `multipass_classic_stage1`，最终 95% |
| **200 条 + 运行态策略** | **失败** | **0** | **0** | 计划阶段抛错，无任何轮次启动 |
| 200 条 + `max_rounds=25` | 成功 | 25 | 51 | 证明上限是唯一阻塞点 |
| **571 条 + 运行态策略** | **失败** | **0** | **0** | 同 RC-01 |
| 160 条 + 第 3/4 轮返回非 JSON（重试开启） | 成功 | 22 | 45 | 可靠性重试自动补齐，`parse_failed_rounds=0` |
| 160 条 + 第 3/4 轮返回非 JSON（`audit_reliability_policy.enabled=false`） | **返回成功，但静默降级** | 20 | 41 | `classic_parse_failed=true`、`parse_failed_rounds=2`，仍返回 11 条风险，无任何异常或覆盖告警 |

**中断特征**：抛错位于 [contract_audit.py#L1109](file:///d:/Workspace/AI-Law-Assistant/app/services/contract_audit.py#L1109)，早于 `audit_llm_plan_created` 与任何 `report_progress`，所以前端进度条在"审计中"阶段**不会收到任何轮次计划**，随后直接变为 failed。

### T03 真实失败文档复算

| 指标 | 实测值 |
| --- | --- |
| 文本字符数 | 85,271 |
| 切分条款数 | **571** |
| 条款 token 总量 | 39,268 |
| 平均每条款 token | 68.8 |
| 抽取耗时 | 157.5 ms（不含检索与规划） |
| 覆盖所需轮数 | ceil(571 / 8) = **72** |
| 当前容量覆盖率 | 160 / 571 = **28.02%** |
| 未覆盖条款（若强行 20 轮） | **411 条（71.98%）** |

### T04 对照组：v2 计划器（`audit_optimization.enabled=true`）

| 条款数 | 结果 | chunks | coverage | LLM 调用 |
| --- | --- | --- | --- | --- |
| 200 | 成功 | 2 | 1.0 | 2 |
| **571** | **成功** | **6** | **1.0** | **6** |

v2 自带覆盖校验（[contract_audit.py#L1723-L1731](file:///d:/Workspace/AI-Law-Assistant/app/services/contract_audit.py#L1723-L1731)），覆盖不全时会显式失败，不会静默漏审。

### T05 数据存储测试（临时 SQLite，与生产同 schema）

| 条款数 | 结果 JSON | 回读校验 | DB 文件 | 写入耗时 | SQLite 单值上限 |
| --- | --- | --- | --- | --- | --- |
| 200 | 190,329 B | sha256 一致 | 761,856 B | 11.3 ms | 1,000,000,000 B |
| 571 | 544,890 B | sha256 一致 | 1,118,208 B | 12.1 ms | 1,000,000,000 B |

**结论：未发现任何存储异常**。`contract_audit.result_json` 为 TEXT，无长度约束；字段与索引（[database.py#L132-L148](file:///d:/Workspace/AI-Law-Assistant/app/core/database.py#L132-L148)）不涉及条款数量。

### T06 错误提示映射（API 层）

| 输入异常 | 用户可见文案（zh） | 是否命中错误码 |
| --- | --- | --- |
| `LegacyAuditCoverageError: COVERAGE_INCOMPLETE ...` | "合同审计失败。请检查【Admin -> 模型配置】中的 LLM 参数后重试。" | **否** |

### 全部已观测错误与异常清单

| 编号 | 场景 | 异常类型 / 错误码 | 原始信息 | 级别 |
| --- | --- | --- | --- | --- |
| E01 | 161 条计划 | `LegacyAuditCoverageError` / `COVERAGE_INCOMPLETE` | `legacy audit plan exceeds max_rounds; refusing to append unchecked clauses to the final round` | 阻断 |
| E02 | 200 条计划 | 同上 | 同上 | 阻断 |
| E03 | 571 条计划（线上同源） | 同上 | 同上 | 阻断 |
| E04 | 200 条完整 pipeline | 同上，且 `llm_calls=0`、`progress_events=0` | 同上 | 阻断 + 无进度 |
| E05 | 571 条完整 pipeline | 同上 | 同上 | 阻断 |
| E06 | API 层 | HTTP 500 + 通用文案 | `合同审计失败。请检查【Admin -> 模型配置】...` | 误导 |
| E07 | 存储层（200/571） | 无 | — | 正常 |
| E08 | 真实文档抽取 | 无 | 571 条成功切分 | 正常 |
| E09 | 160 条 + 关闭可靠性重试 + 2 轮解析失败 | **不抛异常** | 返回 `ok`，但 `classic_parse_failed=true`、`parse_failed_rounds=2` | 静默降级 |

---

## 6. 影响清单

### 6.1 功能影响

| 编号 | 影响 | 严重度 | 依据 |
| --- | --- | --- | --- |
| F-01 | 同配置下，**所有 ≥161 条条款的合同审计 100% 失败**，不具备可用性 | 致命 | T01/T02 |
| F-02 | 失败为确定性失败，重试不会成功，用户只能反复看到同一条错误 | 高 | T02 |
| F-03 | 失败发生在计划阶段，0 次 LLM 调用，但已消耗文本抽取、OCR（如需）、法条检索的时间与算力（线上 26.4s） | 中 | 线上日志 + T02 |
| F-04 | 前端进度条仅收到 `failed 100%`，无"计划了多少轮/为何失败"的细节，无法自助判断 | 中 | T02 的 `progress_events=0` |
| F-05 | 错误文案指向模型配置，用户会误改 `llm_config`，排障方向被带偏 | 中 | T06 |
| F-06 | 条款列表接口 `GET /tax-audit/contracts/{id}/clauses` 使用 `list_contract_clauses(limit=200)` 默认值，超过 200 条时**静默截断且 `total` 也按截断后数量返回** | 中 | [crud.py#L459-L474](file:///d:/Workspace/AI-Law-Assistant/app/services/crud.py#L459-L474)、[tax_audit.py#L189-L195](file:///d:/Workspace/AI-Law-Assistant/app/api/routers/tax_audit.py#L189-L195) |
| F-07 | 预览接口 `clause_limit` 参数校验 1–10000 后被直接忽略（`_ = clause_limit`），始终返回全文且 `clauses=[]`、`clause_total=0` | 低 | [contracts.py#L487-L517](file:///d:/Workspace/AI-Law-Assistant/app/api/routers/contracts.py#L487-L517) |

### 6.2 数据与数据安全影响

| 编号 | 影响 | 严重度 | 依据 |
| --- | --- | --- | --- |
| D-01 | 失败前已写入：缓存文件、`upload_log` 一行、`documents` 一行；失败后再写一条 `contract_audit(status=failed)`。多次重试会累积缓存文件与 `upload_log` 记录 | 中 | [contracts.py#L168-L200、#L277-L297](file:///d:/Workspace/AI-Law-Assistant/app/api/routers/contracts.py#L277-L297)、[crud.py#L89-L97](file:///d:/Workspace/AI-Law-Assistant/app/services/crud.py#L89-L97) |
| D-02 | `insert_document` 对同名 `original_filename` 复用既有文档行并**覆盖 `file_path`**（[crud.py#L122-L138](file:///d:/Workspace/AI-Law-Assistant/app/services/crud.py#L122-L138)），导致同一 `document_id` 下堆积多次失败审计，历史版本不可区分 | 中 | 静态走查 |
| D-03 | 无部分结果落库：失败时只保存 `{"error", "detail"}`，不会产生"只审了前 160 条"的脏报告。这一点是正向的 | — | [contracts.py#L286-L296](file:///d:/Workspace/AI-Law-Assistant/app/api/routers/contracts.py#L286-L296) |
| D-04 | 存储层无异常：190 KB / 545 KB 结果均可完整落库回读，无截断、无编码问题 | 低 | T05 |
| D-05 | 失败异常原文（含内部实现描述）被写入 `contract_audit.result_json`，接口 `detail` 未泄露。本地/私有化部署风险低，但对外暴露 DB 的场景需注意 | 低 | [contracts.py#L291-L292](file:///d:/Workspace/AI-Law-Assistant/app/api/routers/contracts.py#L291-L292) |

### 6.3 业务流程影响

| 编号 | 影响 | 严重度 | 依据 |
| --- | --- | --- | --- |
| B-01 | 长合同（真实样本 571 条）在默认配置下**完全无法出具审计报告**，核心业务场景缺失 | 致命 | T03 |
| B-02 | 若仅把 `max_rounds` 提到 72 绕过，571 条会串行发起 72 次 LLM 调用，每次组内仅约 1,900 token，**容量利用率不足一半**；叠加 `llm_config.timeout=600s` 与前端 90 分钟超时（[base.js#L1-L4](file:///d:/Workspace/AI-Law-Assistant/web/api/base.js#L1-L4)），存在超时、重试风暴与用户流失风险 | 高 | T03 + 代码走查 |
| B-03 | classic 路径**没有最终覆盖校验**：单轮解析失败若重试后仍失败，只会在 meta 中标记 `classic_parse_failed`，仍返回部分结果，存在"报告看似成功但漏审"的合规风险（v2 有覆盖校验）。已实测：关闭重试后 2 轮解析失败，接口仍成功返回 11 条风险、无任何告警 | 高 | 测试 T02 最后一行 + [contract_audit.py#L1040-L1092](file:///d:/Workspace/AI-Law-Assistant/app/services/contract_audit.py#L1040-L1092) 对比 [#L1723-L1731](file:///d:/Workspace/AI-Law-Assistant/app/services/contract_audit.py#L1723-L1731) |
| B-04 | 用户流程为"上传成功 → 等待 → 报错 → 被建议改模型配置 → 重试仍失败"，缺少可执行的处置动作 | 中 | T06 |
| B-05 | 无容量预检，任何超大文件都会先落盘、入库再失败，多用户场景下可被用来消耗磁盘/解析资源 | 中 | 上传链路无大小限制 |

### 6.4 安全影响

| 编号 | 影响 | 结论 |
| --- | --- | --- |
| S-01 | 未发现数据泄露、越权或注入类问题 | 无 |
| S-02 | fail-fast 拒绝"把剩余条款塞进最后一轮"是**安全设计**，避免了静默漏审 | 正向 |
| S-03 | 缺少上传规模限制，属于资源滥用面（DoS 型）而非数据安全 | 低-中 |
| S-04 | 错误语义不透明，用户可能转向线下人工审阅，形成流程外风险 | 低 |

---

## 7. 修复建议

### 7.1 容量方案对比（基于 571 条真实条款实测）

| 方案 | 200 条合成 | 571 条真实 | 单组最大 token | 评价 |
| --- | --- | --- | --- | --- |
| 现值 20 × 8 | 失败 | 失败 | 550–864 | 根因配置，不可用 |
| `max_rounds=72`，组 8 | 25 轮 | 72 轮 | 1,898 | 能过但调用次数多、token 利用率低，不推荐 |
| **组 32，轮 20** | **7 轮** | **19 轮** | **3,977** | **改动最小，仍在 4200 预算内，推荐** |
| 组 64，轮 20 | 6 轮 | 11 轮 | 4,166 | 更快，但单轮输出更易截断 |
| 开启 v2 | 2 chunks | 6 chunks | — | 架构最优，但依赖 tokenizer 服务 |

### P0（立即止血）

**P0-1 调整 classic 分组参数（无需改代码）**

在 `app/config.json` 增加：

```json
"audit_token_policy": {
    "enabled": true,
    "context_window_tokens": 16384,
    "reserve_output_tokens": 2048,
    "safety_margin_tokens": 1024,
    "clause_group_target_tokens": 4200,
    "max_clauses_per_group": 32,
    "max_rounds": 20,
    "chunk_output_tokens": 1536,
    "max_evidence_items_per_round": 12
}
```

- 已验证：571 条 → 19 轮，单组最大 3,977 token，不超过 4,200 目标。
- 注意：单轮输出条目变多，需观察 `multipass_truncated_rounds`；若出现截断，将 `chunk_output_tokens` 提高到 2560–3072，或把组大小回落到 16 并同步提高 `max_rounds`（200 条 13 轮；571 条需 36 轮）。

**P0-2 或启用 v2 计划器（结构化修复）**

```json
"audit_optimization": {
    "enabled": true,
    "context_tokens": 16384,
    "safety_tokens": 1024,
    "output_tokens": 1536,
    "input_target_tokens": 7000,
    "max_chunks": 64,
    "max_attempts_per_chunk": 2,
    "max_retries_per_job": 3
}
```

- 已验证 571 条 6 chunks、`coverage_audited=1.0`。
- 前置条件：v2 使用 `LlamaCppTokenCounter` 调用本地 `/apply-template` 与 `/tokenize`（[audit_prompt_v2.py#L27-L89](file:///d:/Workspace/AI-Law-Assistant/app/services/audit_prompt_v2.py#L27-L89)），必须确保 llama.cpp 服务在线，否则会 `TOKENIZER_UNAVAILABLE`。上线前需在目标机器验证。

**P0-3 错误语义映射**

在 [_build_audit_error_detail](file:///d:/Workspace/AI-Law-Assistant/app/api/routers/contracts.py#L39-L61) 中增加 `coverage_incomplete` / `max_rounds` 分支，返回类似：

> "合同条款数超出当前审计计划容量（当前配置最多 N 条，本次 M 条），请联系管理员调整审计分组配置或使用长合同模式。"

并将该异常映射为 422（可重试但需配置变更），而非 500 通用错误。

### P1（消除静态上限漂移）

1. **计划器自适应轮次**：`required_rounds = ceil(N / max_clauses_per_group)`，与配置上限比较后给出"容量/需求"结构化错误（附 `clause_total`、`required_rounds`、`capacity`），而不是仅一句英文异常。
2. **切分粒度治理**：当前"每个非空行 = 一条条款"（[tax_contract_parser.py#L44-L81](file:///d:/Workspace/AI-Law-Assistant/app/services/tax_contract_parser.py#L44-L81)），571 条平均仅 68.8 token，说明包含大量短行、表格碎片。建议合并无编号短行（如 <20 字）与跨行条款，预计条款数可显著下降，同时提升条款语义完整性。
3. **classic 补齐覆盖校验**：对每轮 `clause_ids` 做覆盖比对，缺失时显式失败或标记 `coverage_incomplete`，与 v2 对齐，避免"部分结果当成功"。

### P2（体验与可观测性）

1. 条款列表分页：`list_contract_clauses` 增加 `offset`，接口 `total` 用 `COUNT(*)`，去掉隐式 200 截断。
2. 预览接口实现 `clause_limit` 或删除该参数，避免"参数存在但无效"的误导。
3. 上传前置校验：解析后先计算条款数，超过当前容量且无法扩展时直接返回 4xx，并清理缓存文件、不写 `documents`/`upload_log`。
4. 计划阶段结构化日志：`clause_total`、`required_rounds`、`capacity`、`binding_constraint`（clause_cap / token_cap），便于提前告警容量不足。
5. 失败清理：复用或新增 cleanup 任务，删除失败请求遗留的缓存文件与过期 `upload_log`。

### 7.2 验收用例

| 用例 | 操作 | 预期 |
| --- | --- | --- |
| AC-01 | 160 条合同，默认配置 | 成功，20 轮 |
| AC-02 | 161 条合同，默认配置 | 422，文案说明"条款数超出计划容量 N/M"，非通用 500 |
| AC-03 | 200 条合同，P0-1 配置 | 成功，7 轮 |
| AC-04 | 571 条真实文档，P0-1 配置 | 成功，19 轮，`multipass_truncated_rounds=0` |
| AC-05 | 571 条真实文档，开启 v2 | 成功，6 chunks，`coverage_audited=1.0` |
| AC-06 | 单轮持续返回非 JSON | 重试后成功；仍失败时 meta 标记且覆盖校验可发现 |
| AC-07 | 同一文件连续上传 3 次 | `documents` 不重复膨胀，缓存文件可清理，审计历史可按审计 ID 区分 |
| AC-08 | GET 条款列表（571 条） | `total=571`，分页返回完整 |

---

## 8. 复现步骤

```powershell
# 在项目根目录执行（Windows PowerShell）
app\.venv\Scripts\python.exe docs\engineering\clause-coverage-limit-20261104\repro_clause_coverage_limit.py

# 机器可读结果
Get-Content docs\engineering\clause-coverage-limit-20261104\repro_results.json
```

脚本内容与本次运行完全一致；它会：

1. 用 `load_config()` 读取运行态 `app/config.json` 并打印实际 policy；
2. 对 159/160/161/200/571 条执行 `plan_clause_groups`；
3. 用 `FakeLLM` 跑完整 `_build_multipass_classic_audit`（160/200/200+25 轮/571/解析失败）；
4. 在临时 SQLite 上执行 200/571 条结果落库与回读校验；
5. 以 v2 计划器跑 200/571 条对照组；
6. 调用 API 错误映射函数，输出用户可见文案；
7. 重新提取线上失败 docx 并复算条款数与 token 量。

> 说明：脚本中的临时数据库与日志目录创建在 `.runtime/validation-temp/clause-coverage-20261104/` 下，不会写入生产库；`FakeLLM` 不发起真实推理。

---

## 9. 附录：证据文件

| 文件 | 说明 |
| --- | --- |
| [repro_clause_coverage_limit.py](file:///d:/Workspace/AI-Law-Assistant/docs/engineering/clause-coverage-limit-20261104/repro_clause_coverage_limit.py) | 复现脚本（可直接运行） |
| [repro_results.json](file:///d:/Workspace/AI-Law-Assistant/docs/engineering/clause-coverage-limit-20261104/repro_results.json) | 本次运行的全部结构化结果（含每个场景的原始异常文本） |
| [repro_stdout.log](file:///d:/Workspace/AI-Law-Assistant/docs/engineering/clause-coverage-limit-20261104/repro_stdout.log) | 运行期完整日志（含 `audit_llm_request_start/done` 逐轮记录） |
| [config.json](file:///d:/Workspace/AI-Law-Assistant/app/config.json) | 运行态配置，确认缺失 `audit_token_policy` / `audit_optimization` |
| [audit_token_policy.py](file:///d:/Workspace/AI-Law-Assistant/app/services/audit_token_policy.py) | 160 容量上限的实现位置 |
| [contract_audit.py](file:///d:/Workspace/AI-Law-Assistant/app/services/contract_audit.py) | classic/v2 调度与多轮编排 |
| [contracts.py](file:///d:/Workspace/AI-Law-Assistant/app/api/routers/contracts.py) | 上传、审计、错误映射、预览接口 |
| [database.py](file:///d:/Workspace/AI-Law-Assistant/app/core/database.py) | `contract_audit` / `contract_clause` 存储结构 |
| [crud.py](file:///d:/Workspace/AI-Law-Assistant/app/services/crud.py) | 审计结果落库、条款列表 200 截断 |
| [tax_contract_parser.py](file:///d:/Workspace/AI-Law-Assistant/app/services/tax_contract_parser.py) | 逐行条款切分规则 |

### 已知局限

1. 桩 LLM 无法反映真实模型的输出长度、截断与耗时；P0 参数调整后建议用真实模型对 571 条样本做一次端到端回归。
2. 未在生产库上执行任何写操作；存储结论来自同 schema 的临时库。
3. 未覆盖多用户并发场景下的磁盘/CPU 压力，仅做了静态风险判断。
