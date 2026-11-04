# 实施问题清单

## IMP-001 现有 API 测试可能初始化生产资源

| 字段 | 内容 |
|---|---|
| category / severity | 安全问题 / 高 |
| observed_at / commit | 2026-11-03T07:01:14Z / `ca8da9d` |
| preconditions | 导入 `tests/test_api.py` |
| reproduce | `test_api.py` 导入 `app.main.app`；`app.main` 模块级执行 `create_app()` |
| expected | 测试先注入独立配置和 fake services，不写真实数据库或管理员 |
| actual | import 时会执行目录创建、`init_db`、默认管理员和模型预检 |
| evidence | `app/main.py`、`app/app_factory.py`、`tests/test_api.py` |
| impact / workaround | 可能污染业务数据；在 T01 完成前不运行现有全量 API 测试 |
| owner / status | 实施工程 / OPEN |

## IMP-002 旧分组在轮数上限后吞并尾部

| 字段 | 内容 |
|---|---|
| category / severity | 功能异常 / 严重 |
| observed_at / commit | 2026-11-03T07:01:14Z / `ca8da9d` |
| preconditions | 条款数量超过 `max_rounds * max_clauses_per_group` |
| reproduce | 构造 797 个短条款，调用 `plan_clause_groups` |
| expected | 完整预算分组或明确 `TOKEN_BUDGET_EXCEEDED`，不得静默吞并 |
| actual | 达到 max_rounds 后持续向最后一组 append |
| evidence | `app/services/audit_token_policy.py:plan_clause_groups` |
| impact / workaround | 最后一轮异常大，并与 prompt 前10条截断叠加形成漏审风险 |
| owner / status | 实施工程 / OPEN |
