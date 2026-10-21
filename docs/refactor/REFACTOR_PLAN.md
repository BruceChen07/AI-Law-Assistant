# 重构计划与进展（P1 文件拆分）

> 本文档是重构的唯一事实来源。**每个阶段开始前更新状态，完成后更新结果与闸门数据。**
> 目标：功能零变化 · 复杂度下降 · 可维护性上升 · 为后续性能优化铺路。

---

## 1. 原则（不可违反）

| # | 原则 | 说明 |
|---|---|---|
| 1 | **逐字移动，不顺手改逻辑** | 拆分 PR 里不允许出现行为变更；用 `git diff -M` 复核相似度 |
| 2 | **保留对外签名** | `build_router(cfg)` / `admin.router` / `get_conn(cfg)` / `init_db(cfg)` 一律不变，调用方零改动 |
| 3 | **一次只拆一个文件** | 每个 PR 可独立回滚 |
| 4 | **不得引入新依赖** | 不新增 requirements；不改 prompt；不改 SQL |
| 5 | **顺序敏感处显式保护** | FastAPI 路由按注册顺序匹配，动态段会吞静态路径 |
| 6 | **每个阶段必须过闸门** | 任一闸门不通过则回退，不进入下一阶段 |

---

## 2. 回归闸门（每阶段必跑）

| 闸门 | 命令 | 通过标准 |
|---|---|---|
| G1 路由面 | `python scripts/snapshot_api_surface.py --compare .refactor-baseline/api_before.json <after.json> --allow-added-dep cfg` | `RESULT: identical route surface` + **0 新增遮蔽对**（不再要求顺序逐字节一致） |
| G2 单元测试 | `python scripts/test_gate.py --check` | 与冻结基线同样的失败集合（不允许新增失败；新修复的会单独列出） |
| G3 复杂度 | `python scripts/audit_complexity.py` | 目标文件 `score` / `maxCx` / `maxFn` 下降 |
| G4 相似度 | `git diff -U0` 逐行复核 | 变更以逐字移动为主，逻辑行不动；批量替换后必须看逐行 diff |
| G5 可导入 | `python -c "import app.main"` | 成功（需模型就位） |
| G6 Schema 等价 | `python scripts/verify_schema_equivalence.py` | P3 专用：新旧 `init_db` 建库后 149 个 schema 对象完全一致 |

> **G1 演进说明**：最初要求"注册顺序逐字节一致"。P2 按资源分组后顺序必然变化，真正的不变量
> 是"没有路由变得不可达"。因此 G1 改为**遮蔽检测**——若路由 A 在某个段位置是动态段、其后
> 注册的路由 B 在同一位置是字面量、且段数相同、HTTP 方法有交集，则 B 被遮蔽。已用人为
> 构造的影子样本验证检测有效。

冻结基线快照：`.refactor-baseline/api_before.json`、`complexity_before.json`、`pytest_baseline.json`

---

## 3. 基线指标（2026-10-21 冻结）

### 3.1 API 面
```
83 routes / 7 routers
  contracts    11    ← 待拆
  tax_audit    22    ← 待拆
  admin        38    ← 待拆
  regulations   5
  auth          4
  embedding     2
  health        1
```

### 3.2 复杂度
```
files 176 · lines 45,258 (sloc 39,813) · functions 1,623
functions > 100 lines : 54
functions > 200 lines : 12
complexity > 10       : 67
complexity > 20       : 8
complexity > 40       : 2
```

### 3.3 测试基线

```
pytest tests -q  →  12 failed, 182 passed   (BUG-01 修复后，2026-10-21 刷新)
历史基线        →  17 failed, 177 passed   (BUG-01 修复前)
```

**注意：基线本身不是绿的。** 因此闸门定义为「不新增失败」，而非「全绿」。

BUG-01 修复后转绿 5 个：
`test_tax_audit_crud` · `test_tax_contract_parser::analyze_*` · `test_tax_lifecycle` ·
`test_tax_matcher::match_*` · `test_tax_parser::parse_*`

剩余 12 个失败按根因归类：

| 数量 | 症状 | 根因 | 归属 |
|---|---|---|---|
| 4 | `ValueError: embedding service is required`（`tax_matcher.py:257`） | 测试未注入 embedder | 测试脚手架 |
| 3 | `KeyError: 'db_path'` | 测试传入 cfg 缺 `db_path` | 测试脚手架 |
| 1 | `sqlite3.OperationalError: database is locked`（`prompt_binding.py:489`） | 并发写同一 sqlite（与"主库未开 WAL"同源） | 代码/配置 |
| 1 | `401 Unauthorized`（`test_tax_audit_pipeline`） | 流水线测试未走认证覆盖 | 测试脚手架 |
| 1 | `assert '...' in '...'`（`test_audit_prompt`） | 与未提交的 `default_prompts.json` 改动相关 | 未提交改动 |
| 2 | 其余断言失败（`test_audit_tax`、`test_contract_audit_progress_logging`） | 待查 | 待查 |

> 这些失败属于**既有问题**，不在本次重构范围内，但重构必须保证不新增失败。

### 3.4 测试覆盖分布（用于风险评估）
| 目标 | 覆盖它的测试文件数 |
|---|---|
| contracts.py | 8 |
| tax_audit.py | 9 |
| database.py | 9 |
| admin.py | 7 |
| **audit_capabilities.py** | **0** ⚠️ |

---

## 4. 阶段计划与状态

图例：`⬜ 未开始` · `🔄 进行中` · `✅ 已完成` · `⏸ 阻塞` · `⏭ 跳过`

| 阶段 | 对象 | 状态 | 闸门结果 |
|---|---|---|---|
| P0 | 基础设施：`get_app_config` + 基线冻结 | ✅ | G1/G2 基线建立 |
| P1a | `contracts.py` → 包：helper 抽出（`_error_specs` / `_progress` / `_support`） | ✅ | G1 ✅ G2 ✅ G5 ✅ |
| P1b | `contracts.py` → 包：11 个 handler 抽出（6 子模块）+ 测试补丁目标迁移 | ✅ | G1 ✅ G2 ✅ G5 ✅ |
| P2 | `tax_audit.py` → `routers/tax_audit/`（8 模块，22 路由） | ✅ | G1 ✅ G2 ✅ G5 ✅ |
| P3 | `database.py` → `core/migrations/`（11 模块，119 条 DDL） | ✅ | G1 ✅ G2 ✅ **G6 ✅** G5 ✅ |
| P4 | `audit_capabilities.py` → 包（8 模块，含前置 characterization test） | ✅ | G1 ✅ G2 ✅ G5 ✅ 11 单测 ✅ |
| P5 | `admin.py` → 包（14 模块，38 路由 + 36 模型） | ✅ | G1 ✅ G2 ✅ G5 ✅ |
| P6 | 函数级收尾：search ✅ / callbacks / preview / manager / contract_audit | 🔄 | G1 ✅ G2 ✅ G5 ✅ |

> 排序依据：收益 ÷ 风险。两个 router 测试最厚（8/9 个测试文件）且复杂度最高（cx 57 / 51），故排最前；
> `audit_capabilities` 虽然耦合低但**零测试**，必须先补 characterization test。

---

## 5. 各阶段详细方案

### P0 · 基础设施 ✅ 已完成

- [x] `app/api/dependencies.py` 增加 `get_app_config(request)`（从 `request.app.state.cfg` 取）
- [x] 冻结基线快照：`.refactor-baseline/api_before.json`（83 路由）、`complexity_before.json`
- [x] 冻结测试基线：`.refactor-baseline/pytest_baseline.json`（17 failures）
- [x] 工具：`scripts/snapshot_api_surface.py`、`scripts/test_gate.py`

### P1a · contracts.py 拆出 helper ✅ 已完成

结果：

| 文件 | 行数 |
|---|---|
| `contracts/__init__.py` | 867（原 1442，−40%） |
| `contracts/_error_specs.py` | 384（`_AUDIT_ERROR_SPECS` 静态数据 + 4 个查找函数） |
| `contracts/_progress.py` | 113（进度表状态 + 3 个函数） |
| `contracts/_support.py` | 54（`_build_digitization_meta` / `_resolve_request_ip` / `_build_retrieval_options`） |

闸门：G1 identical route surface · G2 GATE PASSED（17 = 基线）· G5 `import app.main` OK（91 routes）· ruff F821 全通过。

**关键设计**：handler 全部留在 `__init__.py`。因为测试用
`monkeypatch.setattr(app.api.routers.contracts, <name>, fake)` 打补丁，必须保持
`app.api.routers.contracts` 仍是 handler 解析全局变量的那个模块。
helper 通过 `__init__.py` 重导出，`from ...contracts import _set_audit_progress` 等写法继续有效。

踩坑记录（已修复）：`_audit_progress` 是 `ast.AnnAssign`（带注解赋值），首版提取脚本只处理了
`ast.Assign`，导致状态变量留在 `__init__.py` 而函数移到 `_progress.py`，触发 `NameError`。
教训：提取脚本必须处理所有赋值形态，并用 `ruff --select F821` 复验。

复杂度指标未变化（54 个 >100 行 / 67 个 cx>10）：因为 `build_router` 840 行仍在 `__init__.py`，
helper 都是小函数。**真正降低复杂度指标的是 P1b。**

### P1b · contracts.py 抽出 11 个 handler ✅ 已完成

`build_router` 从 840 行 / cx 57 → 15 行 façade。

| 文件 | 行数 | 内容 |
|---|---|---|
| `contracts/__init__.py` | 97 | `build_router(cfg)` façade + 重导出 |
| `contracts/audit.py` | 450 | audit / pipeline run / progress |
| `contracts/export.py` | 176 | report/export |
| `contracts/summary.py` | 112 | ui-config / pdf-pipeline summary / summary |
| `contracts/preview.py` | 81 | preview-manifest / preview page image |
| `contracts/source.py` | 46 | source download |
| `contracts/preview2.py` | 41 | document preview（为保注册顺序单独成模块） |
| `contracts/_cfg.py` | 24 | 配置依赖解析 |

闸门：G1 identical route surface（`--allow-added-dep cfg`）· G2 GATE PASSED（17 = 基线）·
G5 `app.main` OK（91 routes）· ruff F821/F811 clean。

复杂度收益（全库）：

| 指标 | 基线 | P1b 后 |
|---|---|---|
| 函数 >100 行 | 54 | **53** |
| 函数 >200 行 | 12 | **11** |
| 圈复杂度 >20 | 8 | **7** |
| 圈复杂度 >40 | 2 | **1** ← 840 行的 `build_router` 消失 |

**三个必须记录的决策**：

1. **`preview2.py` 为何存在**：原始注册顺序是 `preview-manifest` → `preview/pages` →
   `source` → `preview` → `export`。若 `preview` 与 `source` 各自成模块并按
   `preview, source` include，`/preview` 会排到 `/source` 之前。二者末段字面量不同、
   实际不会互相吞并，但为**严格保持注册顺序**，把 `preview_contract_document` 单独放进
   `preview2.py`；include 顺序 = audit → summary → preview → source → preview2 → export。

2. **`cfg` 从闭包变量变为注入依赖**：`contracts/_cfg.py::contracts_cfg` 优先取
   `request.app.state.cfg`（生产），回退到最近一次 `build_router(cfg)` 绑定的配置
   （测试用裸 `FastAPI()` 的场景）。`build_router(cfg)` 语义保持完整，生产调用方零改动。

3. **测试补丁目标迁移**（`test_contracts_source_download.py` 10 处、
   `test_contract_audit_log_api.py` 4 处）：**只改 `monkeypatch.setattr` 第一个参数，
   断言一行未动**。handler 移出后补丁必须打在 handler 所在子模块才生效。

踩坑记录（已修复）：

- `insert_cfg_param` 首版取文本第一个 `(`，插进了装饰器 `@router.post(...)` 内部 → 语法错误。
  修正为定位 `def ` 之后的括号。
- 子模块漏导 `APIRouter`/`Depends`（它们只出现在生成的 `router = APIRouter()` 中，
  不属于 handler 的自由变量）→ 生成器改为强制注入。
- `_progress` 名字被通用导入块与专用导入块重复导入（F811）→ 通用块跳过同包来源。
- 测试裸 `FastAPI()` 无 `app.state.cfg` → 首版直接 503；引入 `_cfg.py` 回退层解决。
- 测试重定向脚本把 `build_contracts_router` 误替换成 `build_contracts_audit`（子串匹配）
  → 逐行 diff 复核时发现并修正。教训：批量替换后必须看 `git diff -U0`。

### P2 · tax_audit.py ✅ 已完成

原状：453 行，`build_router` 350 行 / cx 51，22 个路由闭包。

结果（22 条路由全部落位，最大文件 124 行）：

| 文件 | 行数 | 路由数 |
|---|---|---|
| `tax_audit/__init__.py` | 105 | `build_router(cfg)` façade + 重导出 |
| `tax_audit/reports.py` | 124 | 5（report GET/POST/export、export 状态、下载） |
| `tax_audit/contracts.py` | 120 | 7（import/analyze/clauses/match/matches/pipeline/trace） |
| `tax_audit/issues.py` | 66 | 4（generate/list/review/issue-trace） |
| `tax_audit/cleanup.py` | 58 | 4（cleanup run/retry/jobs/archive） |
| `tax_audit/regulations.py` | 47 | 2（import/parse） |
| `tax_audit/_shared.py` | 45 | `ALLOWED_TAX_AUDIT_EXTENSIONS` / `_validate_extension` / `_save_upload` |
| `tax_audit/_cfg.py` | 19 | 配置依赖解析 |

闸门：G1 identical route surface（0 新增遮蔽对）· G2 GATE PASSED（17 = 基线）·
G5 `app.main` OK（91 routes）· ruff F821/F811 clean。
**本阶段未修改任何测试文件**（22 个 handler 无一个被 monkeypatch）。

复杂度收益（全库）：

| 指标 | P1b 后 | P2 后 |
|---|---|---|
| 圈复杂度 >40 | 1 | **0** ← 全部消除 |
| 函数 >200 行 | 11 | **10** |
| 圈复杂度 >10 | 67 | **66** |

**闸门升级（本阶段做的关键改进）**：原 G1 要求"注册顺序逐字节一致"。但按资源分组后
顺序必然变化。真正的不变量不是顺序，而是**没有路由变得不可达**。因此把 G1 的顺序检查
改为**遮蔽检测**：若某条路由在同一段位置上是动态段、而其后注册的路由在同一位置是字面量、
且段数相同、HTTP 方法有交集，则该路由被遮蔽。

- 已用人为构造的影子样本验证检测有效：把 `/contracts/pdf-pipeline/summary` 排到
  `/contracts/{document_id}/summary` 之后，工具报出
  `1 NEW shadowed route pair(s)`。
- `tax_audit` 的 22 条路由中，所有区分位都是字面量（`regulations`/`contracts`/`issues`/
  `reports`/`cleanup`/`archive`），因此分组重排后 **0 新增遮蔽对**，安全。

踩坑记录（已修复）：首版生成器把 `ALLOWED_TAX_AUDIT_EXTENSIONS` / `_validate_extension` /
`_save_upload` 复制进 `_shared.py` 后，没有从 `__init__.py` 的头部删除，导致重复定义（F811）。
修正为：生成头部时按行号区间剔除已移走的语句，并折叠遗留空行。

### P3 · database.py ✅ 已完成

原状：893 行，`init_db` 826 行内联 DDL（123 条语句：2 条 setup + 119 条 DDL + 2 条 teardown）。

结果：`database.py` **59 行**，`init_db` 只剩 6 行；DDL 拆到 11 个模块，最大 180 行。

```
app/core/migrations/
  __init__.py        apply_migrations(cur) —— 按原顺序调用 11 个模块
  _01_core.py        users / sessions / documents / audit_logs               (15 stmt)
  _02_prompts.py     audit_prompt(+列迁移) / audit_prompt_binding            (18)
  _03_contracts.py   contract_audit / document_digitization(+sha256 迁移)    (11)
  _04_rag.py         regulation* / article / ingest_job / article_fts / article_embedding (8)
  _05_tax.py         tax_rule / contract_document / contract_clause / clause_rule_match    (10)
  _06_audit_trail.py audit_issue / audit_trace / evidence_anchor             (9)
  _07_export_archive.py export_job / export_snapshot / tax_audit_archive_record / tax_audit_cleanup_job (11)
  _08_vector_upload.py vector_store_config(+INSERT OR IGNORE) / upload_log   (4)
  _09_capabilities.py audit_skill(+列迁移) / audit_skill_file / audit_skill_version /
                     audit_rule_pack* / audit_template                      (23)
  _10_agents.py      agent_profile                                          (3)
  _11_runtime.py     audit_runtime_session / audit_runtime_skill_run         (7)
```

切分按**领域连续区间**，所以每条的 SQL 文本与执行顺序完全不变 —— 用 AST 校验了
"119 条 DDL 全部落在且仅落在一个区间"，不存在跨边界语句。

闸门：G1 identical route surface · G2 GATE PASSED（17 = 基线）· G5 `app.main` OK ·
**G6 schema 等价通过**。

复杂度收益（全库）：

| 指标 | P2 后 | P3 后 |
|---|---|---|
| 函数 >200 行 | 10 | **9** ← `init_db` 826 行消失 |
| 圈复杂度 >10 | 66 | **64** |

**G6 为何要分两部分**：原始代码本身是坏的（见下方 BUG-01），所以无法直接比对 schema。
- **Part A（未修复）**：新旧都必须在同一位置以同一异常失败 → 证明"损坏方式一致"。
- **Part B（两侧都中和那行重复列）**：各自建库并逐对象比对 `sqlite_master` →
  **149 个对象（43 表 + 63 索引 + FTS 影子表）完全一致**，覆盖全部 119 条语句。

---

## 附：重构期间发现的既有 BUG

### BUG-01 · `audit_skill` DDL 重复列，导致全新数据库无法建表 ✅ 已修复

位置：`app/core/database.py` 原 L599-601（现位于 `migrations/_09_capabilities.py`）

```sql
CREATE TABLE IF NOT EXISTS audit_skill(
    id TEXT PRIMARY KEY,
    id TEXT PRIMARY KEY,          -- ← 重复定义
```

影响：**任何全新数据库调用 `init_db` 都会抛 `duplicate column name: id`**，
即全新部署起不来。

处理：作为 P3 的独立修复提交（不混在搬移里）。删掉重复行后：
- G6 Part A：原代码仍失败、当前代码建库成功
- G6 Part B：149 个 schema 对象与"修复前的正确形态"完全一致
- 测试：17 → **12** 失败，5 个转绿；其余失败换了根因（见 3.3）

### BUG-02 · 主库并发写触发 `database is locked`（中）

`test_tax_risk` 现在报 `sqlite3.OperationalError: database is locked`
（`prompt_binding.py:489`）。与之前性能分析里的发现同源：`core/database.py::get_conn`
只设了 `busy_timeout`，**没有开启 WAL**。多线程/多请求并发写同一 sqlite 时会互锁。

建议（未做）：`get_conn` 里加 `PRAGMA journal_mode=WAL`（该项是持久化的，只在首次生效）
+ `PRAGMA synchronous=NORMAL`。注意：之前实测该改动对**单线程**耗时无改善（785ms vs 856ms），
它的价值在于**并发不互锁**，不要用它承诺"变快"。

---

### P4 · audit_capabilities.py ✅ 已完成

原状：2695 行 / 61 函数 / 649 行内联种子数据。**零测试覆盖**。

**前置任务（先做）**：`tests/test_audit_capabilities_char.py` —— 11 个 characterization 测试，
覆盖种子幂等性、6 个公共函数的 CRUD 往返、重复创建/未知 id/非法状态/删除引用等异常路径、
列表过滤与排序确定性、以及 3 个内部域函数可调用。**拆分前后均 11 passed**。

结果：8 个模块，`__init__.py` 99 行（全量重导出旧表面，`admin.py` 零改动）。

| 文件 | 行数 | 内容 |
|---|---|---|
| `skills.py` | 897 | 6 个公共 + 种子钩子 + 归一化 + 引用计数 + 详情挂载 |
| `_seed_data.py` | 805 | 8 个内置种子数据块 + 6 个构建函数 |
| `rule_packs.py` | 432 | 规则包列表 / 草稿 / 评审 / 版本 / `list_rules` |
| `agents.py` | 262 | agent profile CRUD + 绑定校验 |
| `_normalizers.py` | 103 | 纯归一化/校验助手 + 值常量 |
| `_row_mappers.py` | 75 | 各实体的 Row→dict 映射 |
| `_row_mappers`+`templates.py` | 48 | 模板列表/详情 |
| `__init__.py` | 99 | 重导出（公共 + 内部，完全向后兼容） |

闸门：G1 identical route surface · G2 GATE PASSED（12 = 基线）· G5 `app.main` OK ·
characterization 11 passed · ruff F821/F811 clean · **`admin.py` 未改一行**。

**如实说明**：本阶段改善的是**文件体积**（2695 → 最大 897 行），
全局函数级指标几乎不变（>100 行 53、>200 行 9、cx>20 7）——
因为原文件虽有 2695 行，但没有超长函数（最长 124 行）。
遗留：`skills.py` 897 行仍偏大，后续可再拆 `skills_crud.py` / `skill_normalize.py`。

踩坑记录（已修复）：
1. `_seed_data.py` 首版把**数据块放在构建函数之前**，而种子数据在模块导入时就调用
   `_skill_card()` / `_skill_version()` → `NameError: name '_skill_card' is not defined`。
   修正为**构建函数在前、数据块在后**。教训：带"导入期求值"的数据块必须排在依赖之后。
2. `__init__.py` 把公共名字在"公共导出"和"全量兼容导出"里各导一次 → F811 重复定义。
   修正为全量块排除已导出的公共名。

### P5 · admin.py ✅ 已完成

原状：2009 行 / 36 个 Pydantic 模型 / 38 条路由 + 26 个 helper（**不是闭包工厂**）。

结果：14 个模块，`__init__.py` 只保留带 prefix 的 `router` 与注册顺序。

| 文件 | 行数 | 内容 |
|---|---|---|
| `prompts.py` | 673 | 8 个 prompt 端点 + 2 个 binding + 15 个 prompt helper |
| `schemas.py` | 293 | 36 个 Pydantic 模型 |
| `token_usage.py` | 177 | `get_token_usage`(108) + CSV 导出 |
| `skills.py` | 157 | 6 个技能管理端点 |
| `__init__.py` | 109 | `router = APIRouter(prefix="/api/admin")` + include |
| `documents.py` / `llm_config.py` / `_common.py` / `users.py` / `memory_config.py` / `memory_rules.py` / `ui_config.py` / `vector_store.py` / `stats.py` | 34–104 | 各域子路由 |

闸门：G1 identical route surface（0 新增遮蔽对）· G2 GATE PASSED（12 = 基线）·
G5 `app.main` OK（91 routes）· ruff F821/F811 clean · **`app_factory.py` 未改一行**。

**关键点**：
1. **零 monkeypatch 风险** —— 与 P1 不同，全项目没有任何测试引用 `routers.admin`，
   所以本阶段**未修改任何测试文件**。
2. **prefix 处理**：`__init__.py` 保留 `APIRouter(prefix="/api/admin", tags=["admin"])`，
   子模块用裸 `APIRouter()`，由父路由 `include_router` 继承 prefix。38 条路由路径不变。
3. **按域连续分组**：38 条路由在原文件里恰好按域连续排列，因此子模块 include 顺序
   = 原注册顺序，无需 `preview2` 那种拆法。
4. **G1 归一化修正**：模型移入 `schemas.py` 后 `response_model` 的 `__module__` 变了
   （`admin.DocumentListResponse` → `admin.schemas.DocumentListResponse`），
   但 OpenAPI 组件名取自 `__name__`，契约不变。因此 G1 的 `response_model` 比较
   改为**只比类名**（`_rm_name()`），否则会误报 28 处差异。
5. `from app.api.routers.admin.schemas import *` 保留旧的全量模型可见性，
   外部 `from app.api.routers.admin import XxxResponse` 继续有效。

复杂度：全局指标持平（>100 行 53、>200 行 9、cx>20 7）—— 与 P4 同理，
admin.py 虽 2009 行但无超长函数（最长 `get_token_usage` 108 行）。
本阶段收益是**文件体积**（2009 → 最大 673 行）。

### P6 · 函数级收尾 🔄 进行中

与前 5 个阶段性质不同：**不是拆文件，而是拆函数**，会真正改动内部逻辑，
因此每个子项单独成 PR、单独过闸门，不要求逐字搬移。

#### P6-a · `search.py::search_regulations` ✅ 已完成

原状：268 行 / cx **31**。

做法：纯提取式重构（逻辑顺序、SQL、日志文本、异常分支全部保持不变），
拆出 8 个模块级助手：

| 助手 | 职责 | cx |
|---|---|---|
| `_build_bm25_sql` | FTS5 候选 SQL + 过滤参数 | 4 |
| `_fetch_bm25_rows` | 执行 + 排名打分 + 失败告警 | 3 |
| `_build_semantic_sql` | 向量候选 SQL + 过滤参数 | 4 |
| `_fetch_semantic_rows` | 点积打分 + top-N 截断（异常上抛给调用方保留原告警） | 3 |
| `_merge_and_score` | 候选合并 + 融合打分 | 5 |
| `_resolve_rerank_enabled` | `rerank_mode`/`rerank_enabled`/`ab` 哈希判定 | 4 |
| `_apply_rerank` | rerank + 阈值 + 失败回退 | 5 |
| `_decorate_rows` | 生效状态 / answer / match_tokens / citation_id | 4 |

结果：`search_regulations` **268 行 / cx 31 → 105 行 / cx 8**。

闸门：G1 identical route surface · G2 GATE PASSED（12 = 基线）· G5 `app.main` OK ·
目标测试 24 passed（`test_utils` / `test_vector_stores` / `test_api` /
`test_audit_retrieval_translation` / `test_memory_system_unit`）· ruff F821 clean。

全局指标变化：函数 >200 行 **9 → 8**、cx >20 **7 → 6**、cx >10 **64 → 63**。

> 注意：`search.py` 总行数从 307 增到 362（助手函数各自有 docstring 与签名），
> 但**单函数复杂度大幅下降** —— 这正是"文件行数"与"圈复杂度"两个指标分离的又一次体现。

#### P6-b ~ P6-e · 待做


| 文件 | 做法 |
|---|---|
| `callbacks.py` | `create_memory_callbacks` 21 参数 → `@dataclass` 配置 + 拆 `ClauseAuditCallback`/`FlushCallback` |
| `search.py` | `search_regulations` 268 行 → `_build_bm25_sql` / `_run_bm25` / `_run_semantic` / `_merge_candidates` / `_score_and_rank` / `_apply_rerank` |
| `contract_preview_assets.py` | `build_contract_preview_manifest` 嵌套 7 → ≤3 |
| `manager.py` | `split_contract` cx 38 → 条款类型策略 |
| `contract_audit.py` | `_build_classic_audit` / `_aggregate_classic_results` / `_build_contract_summary` 移入 `contract_audit_modules/` |

---

## 6. 变更日志

| 日期 | 阶段 | 内容 | 闸门 |
|---|---|---|---|
| 2026-10-21 | P0 | 冻结基线：83 路由快照 + 复杂度快照 + 17 个失败测试清单；新增 `get_app_config`；建立 `snapshot_api_surface.py` / `test_gate.py` | — |
| 2026-10-21 | P1a | `contracts.py`(1442) → 包：`_error_specs`(384) `_progress`(113) `_support`(54)，`__init__` 867。handler 保持原位以确保测试补丁目标有效。修复 AnnAssign 提取遗漏。 | G1 ✅ G2 ✅ G5 ✅ F821 ✅ |
| 2026-10-21 | P1b | 11 个 handler 拆入 audit/summary/preview/source/preview2/export；新增 `_cfg.py`；`build_router` 840 行 → 15 行；测试补丁目标迁移 14 处（断言未动） | G1 ✅ G2 ✅ G5 ✅ F821 ✅ |
| 2026-10-21 | P2 | `tax_audit.py`(453) → 8 模块包，`build_router` 350 行 → 12 行；22 路由全落位；**未改测试**；G1 升级为遮蔽检测（验证有效） | G1 ✅ G2 ✅ G5 ✅ F821 ✅ |
| 2026-10-21 | P3 | `database.py`(893) → 59 行；`init_db` 826 行 → 6 行；119 条 DDL 拆入 `core/migrations/` 11 模块（逐字搬移）；新增 G6 schema 等价闸门；**发现 BUG-01** | G1 ✅ G2 ✅ **G6 ✅** G5 ✅ |
| 2026-10-21 | BUG-01 | 删除 `audit_skill` DDL 中重复的 `id TEXT PRIMARY KEY`；全新库可建；测试 17→12 失败（5 转绿）；刷新测试基线 | G6 ✅ G2 ✅（基线刷新） |
| 2026-10-21 | P4 | 新增 `tests/test_audit_capabilities_char.py`（11 测试，先于拆分落地）；`audit_capabilities.py`(2695) → 8 模块包，`__init__` 99 行全量重导出，`admin.py` 零改动 | G1 ✅ G2 ✅ G5 ✅ 11 单测 ✅ F821 ✅ |
| 2026-10-21 | P5 | `admin.py`(2009) → 14 模块包（`schemas.py` 36 模型 + 11 子路由 + `_common`）；prefix 由 `__init__.router` 承载；**未改测试**；G1 的 `response_model` 比较改为只比类名 | G1 ✅ G2 ✅ G5 ✅ F821 ✅ |
| 2026-10-21 | P6-a | `search.py::search_regulations` 268 行 / cx 31 → 105 行 / cx 8，拆出 8 个助手；逻辑顺序与 SQL 逐字保留 | G1 ✅ G2 ✅ G5 ✅ 24 目标测试 ✅ |
