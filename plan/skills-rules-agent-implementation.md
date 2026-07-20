# Skills / Rules / Agent 实施文档

## 1. 目标

在当前审计系统基础上，分阶段落地：

1. 用户可见当前可用的 skills、规则包、规则与审计模板。
2. 后续支持用户级 agent 配置、skills 组合与规则包选择。
3. 记录每个阶段的开发进度、测试结果与待办风险，便于持续迭代。

## 2. 外部参考 Skill

### 2.1 已成功获取的参考

| 来源 | 方向 | 用途 |
|---|---|---|
| `@codefarmerman/china-tax-law` | 中国财税法律知识 | 作为税法知识类 skill 的初始参考 |
| `@yy-c8/receipt-assistant` | 票据/发票处理 | 作为票据识别与票据审计类 skill 的初始参考 |
| `@munich949/tax-digital-localization` | 财税数字化本地化 | 作为税务术语/多语言本地化类 skill 的初始参考 |

### 2.2 当前无法访问的参考

以下链接当前返回 404 或未找到页面，第一阶段不直接作为种子内容来源，仅保留占位：

- `https://clawhub.ai/skills/skills/zhang-tax-law`
- `https://clawhub.ai/xhj2aidevs/skills/aitaxs-assistant`
- `https://clawhub.ai/skills/skills/zhang-intl-tax-law`

## 3. 分阶段计划

| 阶段 | 状态 | 目标 | 交付物 |
|---|---|---|---|
| 阶段 1 | 已完成 | skills / rules / templates 可见化基础设施 | 数据表、默认种子、只读 API、测试 |
| 阶段 2 | 已完成 | 用户级 agent profile 与只读模板绑定 | agent_profile 表、查询接口、模板绑定 |
| 阶段 3 | 已完成 | 用户可配置 skills / rule packs | 更新接口、参数校验、回显 |
| 阶段 4 | 已完成 | 规则草案与审批流 | draft/version/approval 模型 |
| 阶段 5 | 已完成 | skills 执行编排与 evidence pack 融合 | runtime / template engine / audit trace |
| 阶段 6 | 已完成 | skill sandbox / runtime executor / replayable audit session | session / skill run / replay API |

## 4. 阶段 1 设计范围

### 4.1 目标

- 新增 skills / rule packs / templates 三类能力注册表
- 提供默认内置种子
- 提供用户可读接口
- 与当前 `tax_rule` 表打通，支持查看现有规则

### 4.2 本阶段不做

- 用户上传自定义代码型 skill
- 规则编辑、审批、发布
- 用户级 agent 更新与运行时编排

## 5. 开发进度记录

| 时间 | 阶段 | 进展 |
|---|---|---|
| 2026-07-18 | 阶段 1 | 完成现状调研，确认当前系统已有 Python 规则引擎雏形与 OCR 插件动态注册机制 |
| 2026-07-18 | 阶段 1 | 拉取外部参考 skill 信息，已确认 3 个可用参考来源 |
| 2026-07-18 | 阶段 1 | 开始实现 `audit_skill`、`audit_rule_pack`、`audit_template` 数据表与默认种子 |
| 2026-07-18 | 阶段 1 | 完成 `app/services/audit_capabilities.py`，落地默认 skills / rule packs / templates 种子与查询服务 |
| 2026-07-18 | 阶段 1 | 完成 `/api/capabilities/*` 只读接口，支持 skills、规则包、规则、模板查询 |
| 2026-07-18 | 阶段 1 | 完成 `tests/test_audit_capabilities.py`，覆盖种子、规则查询、API 只读链路 |
| 2026-07-18 | 阶段 2 | 开始实现 `agent_profile` 数据结构，支持用户拥有自己的 agent 配置 |
| 2026-07-18 | 阶段 2 | 完成 `agent_profile` 数据表与服务层，支持按模板继承默认 skills / rule packs |
| 2026-07-18 | 阶段 2 | 完成 `/api/agents` 列表、详情、创建接口 |
| 2026-07-18 | 阶段 2 | 完成 `tests/test_agent_profiles.py`，覆盖模板继承、非法 skill 校验和 API 创建/读取链路 |
| 2026-07-18 | 阶段 3 | 完成 `agent_profile` 更新能力，支持用户修改 display name、描述、prompt、skills、rule packs 与运行参数 |
| 2026-07-18 | 阶段 3 | 完成 `/api/agents/{profile_id}` 更新接口，并补充 owner 范围、空更新、非法 skill 与运行参数组合校验 |
| 2026-07-18 | 阶段 3 | 扩展 `tests/test_agent_profiles.py`，覆盖服务层更新与 API 更新回归 |
| 2026-07-18 | 阶段 4 | 新增 `audit_rule_pack_draft`、`audit_rule_pack_version` 数据表，落地规则包草案与发布版本模型 |
| 2026-07-18 | 阶段 4 | 完成规则包 draft 创建、更新、提交、审批与发布服务，审批通过后自动写入版本并回写当前规则包 |
| 2026-07-18 | 阶段 4 | 完成 `/api/capabilities/rule-pack-drafts/*` 与 `/api/capabilities/rule-packs/{pack_id}/versions` 接口 |
| 2026-07-18 | 阶段 4 | 扩展 `tests/test_audit_capabilities.py`，覆盖服务层与 API 层的 draft -> pending_review -> approved/versioned 链路 |
| 2026-07-18 | 阶段 5 | 新增 `tax_pipeline_runtime.py`，支持按 agent profile / template 生成技能执行计划、固定 rule pack version 并构建 evidence pack |
| 2026-07-18 | 阶段 5 | 扩展 `/tax-audit/contracts/{contract_id}/pipeline/run`，支持可选 `profile_id` 并返回 `runtime` 元数据 |
| 2026-07-18 | 阶段 5 | 将运行时元数据写入 `audit_trace`，并把证据片段持久化到 `evidence_anchor`，实现运行结果可追踪 |
| 2026-07-18 | 阶段 5 | 扩展 `tests/test_tax_pipeline_runtime.py`，覆盖运行时服务落库与路由回显 |
| 2026-07-18 | 阶段 6 | 新增 `audit_runtime_session`、`audit_runtime_skill_run` 数据表，记录可回放审计 session 与每个 skill 的执行明细 |
| 2026-07-18 | 阶段 6 | 扩展 `tax_pipeline_runtime.py`，落地 builtin-only sandbox executor、session 持久化与 replay 机制 |
| 2026-07-18 | 阶段 6 | 新增 `/tax-audit/contracts/{contract_id}/sessions`、`/tax-audit/runtime/sessions/{session_id}`、`/skills`、`/replay` 接口 |
| 2026-07-18 | 阶段 6 | 扩展 `tests/test_tax_pipeline_runtime.py`，覆盖 session 查询、skill run 查询与 replay 回归 |

## 6. 测试记录

| 时间 | 阶段 | 测试项 | 结果 | 备注 |
|---|---|---|---|---|
| 2026-07-18 | 阶段 1 | `python -m py_compile app\services\audit_capabilities.py app\api\routers\capabilities.py tests\test_audit_capabilities.py` | Pass | 语法检查通过 |
| 2026-07-18 | 阶段 1 | `python -m pytest tests\test_audit_capabilities.py::test_audit_capabilities_seed_and_query -q` | Pass | `1 passed in 1.05s` |
| 2026-07-18 | 阶段 1 | `python -m pytest tests\test_audit_capabilities.py::test_audit_capabilities_rules_endpoint_data -q` | Pass | `1 passed in 0.84s` |
| 2026-07-18 | 阶段 1 | `python -m pytest tests\test_audit_capabilities.py::test_capabilities_api_readonly -q` | Pass | `1 passed in 0.89s` |
| 2026-07-18 | 阶段 1 | `python -m pytest tests\test_audit_capabilities.py -q` | Pass | `3 passed in 1.60s` |
| 2026-07-18 | 阶段 2 | `python -m py_compile app\core\database.py app\services\audit_capabilities.py app\api\routers\agents.py tests\test_agent_profiles.py` | Pass | 语法检查通过 |
| 2026-07-18 | 阶段 2 | `python -m pytest tests\test_agent_profiles.py -q` | Pass | `3 passed in 1.65s` |
| 2026-07-18 | 阶段 3 | `python -m py_compile app\services\audit_capabilities.py app\api\routers\agents.py tests\test_agent_profiles.py` | Pass | 语法检查通过 |
| 2026-07-18 | 阶段 3 | `python -m pytest tests\test_agent_profiles.py -q` | Pass | `6 passed in 14.97s` |
| 2026-07-18 | 阶段 4 | `python -m py_compile app\core\database.py app\services\audit_capabilities.py app\api\routers\capabilities.py tests\test_audit_capabilities.py` | Pass | 语法检查通过 |
| 2026-07-18 | 阶段 4 | `python -m pytest tests\test_audit_capabilities.py -q` | Pass | `5 passed in 2.15s` |
| 2026-07-18 | 阶段 5 | `python -m py_compile app\services\crud.py app\services\tax_pipeline_runtime.py app\services\tax_matcher.py app\services\tax_risk.py app\services\audit_orchestrator.py app\api\routers\tax_audit.py app\api\schemas.py tests\test_tax_pipeline_runtime.py` | Pass | 语法检查通过 |
| 2026-07-18 | 阶段 5 | `python -m pytest tests\test_tax_pipeline_runtime.py tests\test_tax_report.py -q` | Pass | `3 passed in 5.67s` |
| 2026-07-18 | 阶段 6 | `python -m py_compile app\core\database.py app\services\tax_pipeline_runtime.py app\api\routers\tax_audit.py app\api\schemas.py tests\test_tax_pipeline_runtime.py` | Pass | 语法检查通过 |
| 2026-07-18 | 阶段 6 | `python -m pytest tests\test_tax_pipeline_runtime.py tests\test_tax_report.py -q` | Pass | `4 passed in 7.63s` |

## 7. 风险与后续关注

- 当前规则包第一阶段只做“可见化”，仍直接映射 `tax_rule` 全量规则，后续需要拆分为多规则包。
- 外部参考 skill 链接有部分不可访问，后续如用户提供更稳定源，可补充种子内容。
- 当前 agent profile 已支持创建、查看、更新，但尚不支持删除、草案和审批流。
- 当前规则包审批流已支持 draft/version/review/publish 的最小闭环，但尚未支持多级审批、审批历史列表与差异对比视图。
- 当前阶段 5 已打通最小运行时链路，但 skill 仍是“编排级映射”，尚未实现用户上传代码型 skill 或 sandbox 执行器。
- rule pack version 当前已可固定并回显，但尚未支持历史版本回放执行与差异化 rerun。
- 当前阶段 6 已支持 builtin-only sandbox executor 与 replayable audit session，但尚未支持用户自定义 Python skill 的隔离运行、资源限额和超时终止。
- 当前 replay 会复用 session 固化的 rule pack pins 与请求参数，但尚未支持跨版本结果 diff、session compare view 和 rerun reason 注记。
- 后续如继续迭代，建议进入阶段 7：user skill package manifest / sandbox quota / replay diff & compare UI。
