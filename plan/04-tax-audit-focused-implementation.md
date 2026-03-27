# 财税合同审计专项实施清单（下一步执行版）

## 目标
- 将“仅基于用户上传财税法规”的审计方案转化为可开发、可验收、可上线的任务清单。
- 在不破坏现有 FastAPI + React + OCR + 检索链路的前提下完成增量改造。

## 范围约束
- 仅审计财税合同条款。
- 仅引用用户上传法规规则库。
- 不接入外部通用法条。

## 里程碑节奏（6 周）

## 第 1 周：数据模型与任务骨架
- 新增数据表：
  - regulation_document
  - tax_rule
  - contract_document
  - contract_clause
  - clause_rule_match
  - audit_issue
  - audit_trace
- 在 `app/core/database.py` 增加初始化 SQL 与迁移入口。
- 在 `app/services/crud.py` 增加上述表的 CRUD 方法。
- 新增 API 路由骨架：
  - `POST /tax-audit/regulations/import`
  - `POST /tax-audit/contracts/import`
  - `GET /tax-audit/contracts/{contract_id}/issues`
- 验收标准：
  - 可写入/读取 6 类核心实体。
  - API 返回结构稳定（Pydantic schema 固定）。

## 第 2 周：法规解析服务（Parser）
- 扩展上传文件支持：PDF/Word/Excel/扫描件统一进入解析入口。
- 复用现有 OCR 管线，新增法规条款切分函数：
  - 章/节/条/款/项切分
  - 页码与段落回溯信息保留
- 实现财税字段抽取：
  - 税种、税率、时限、审批要件、适用主体、区域、生效失效日期
- 将条款标准化写入 `tax_rule`。
- 验收标准：
  - 单个法规文件可产出结构化规则数据。
  - 规则均可回溯到原文页码/段落。

## 第 3 周：合同结构化与条款树
- 在 `contract_audit` 解析后增加条款树构建：
  - clause_id、path、page_no、paragraph_no
- 增加条款级实体抽取：
  - 金额、税率、发票类型、开票时点、代扣代缴义务、优惠条件
- 入库 `contract_clause`。
- 验收标准：
  - 合同条款可视化树可稳定生成。
  - 每条风险可回跳条款定位字段。

## 第 4 周：比对服务（Match）与三态结论
- 检索流程改造为规则库限定：
  - 仅检索 `tax_rule` 生成的索引内容
  - 不读取通用法规库
- 采用三路比对：
  - 规则判定（硬条件）
  - 关键词召回（BM25/FTS）
  - 向量召回 + 重排（保留双路查询策略）
- 产出匹配状态：
  - compliant / non_compliant / not_mentioned
- 写入 `clause_rule_match`。
- 验收标准：
  - 同一输入重复执行，匹配标签稳定。
  - non_compliant 与 not_mentioned 可解释。

## 第 5 周：风险生成、复核与回写
- 风险生成器：
  - 对 non_compliant/not_mentioned 生成高/中/低风险
  - 强制绑定 `rule_id + evidence`
- 增加复核接口：
  - `POST /tax-audit/issues/{issue_id}/review`
  - 支持确认、驳回、降级、例外理由
- 增加审计轨迹写入 `audit_trace`。
- 验收标准：
  - 所有高/中风险必须有法规证据链。
  - 人工操作全量可追溯。

## 第 6 周：报告服务与上线基线
- 生成审计报告结构：
  - 概览、风险清单、证据清单、复核结论、例外列表
- 前端增加分屏审阅页（兼容现有 React）：
  - 左：合同原文定位
  - 中：风险列表
  - 右：法规原文与规则信息
- 增加归档与 30 天清理任务（文件与中间产物）。
- 验收标准：
  - 报告可导出且证据跳转正确。
  - 生命周期清理任务可观测、可重试。

## 接口与模块落点（与当前代码映射）
- 路由层：`app/api/routers/` 新增 `tax_audit.py`
- 解析服务：`app/services/tax_parser.py`
- 比对服务：`app/services/tax_matcher.py`
- 报告服务：`app/services/tax_report.py`
- 复核服务：`app/services/tax_review.py`
- 模型定义：`app/api/schemas.py` 增加 tax-audit 相关 schema

## 数据库迁移建议
- 新增迁移脚本目录：`app/migrations/`
- 首个迁移：`0001_tax_audit_core.sql`
- 回滚脚本：`0001_tax_audit_core.rollback.sql`
- 所有表增加：
  - `created_at`
  - `updated_at`
  - `created_by`（可空）

## 测试计划（与开发同步）
- 单元测试：
  - 规则切分、字段抽取、匹配判定、风险分级、复核状态机
- 集成测试：
  - 法规导入 → 合同导入 → 比对 → 复核 → 报告导出全链路
- 回归测试：
  - 现有 `contract_audit`、`search`、OCR 不退化
- 指标门槛：
  - 关键路径测试通过率 100%
  - 核心模块覆盖率不低于 80%

## 上线前检查清单
- 配置项：
  - `tax_audit_enabled`
  - `tax_audit_rules_only`
  - `tax_audit_result_schema_version`
- 观测项：
  - 审计耗时
  - 规则召回率
  - 无证据风险占比
- 风险开关：
  - 支持一键回退到现有审计链路

## 执行优先级
- P0：规则库入库、三态匹配、风险证据链、复核轨迹
- P1：分屏审阅、报告导出、清理任务
- P2：Milvus/MinIO 全量迁移、性能调优
