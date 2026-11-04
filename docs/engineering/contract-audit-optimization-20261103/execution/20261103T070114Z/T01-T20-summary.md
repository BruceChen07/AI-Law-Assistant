# T01-T20 实施证据摘要

- 分支：`feature/contract-audit-optimization-v2`
- 基线：`ca8da9da042e362a624ec1157f3113cc325cfe53`
- 记录时间：`2026-11-03T07:12:27Z`
- 配置策略：`audit_optimization.enabled=false`，未修改用户当前 `app/config.json` 的启用状态。

## 已实现

1. 测试应用工厂支持配置和服务注入，可关闭数据库、管理员及模型预检。
2. 验证环境脚本创建独立配置、数据库、日志、TEMP、Trace 和证据目录，并拒绝生产数据目录。
3. 来源模型使用 Unicode code point `[start,end)`，保持页码、条款预览 ID 和全文非空白字符覆盖。
4. v2 规划按完整渲染消息精确计数，强制输入、输出和安全预算不超过上下文。
5. 797 片段不再追加到异常尾组；旧路径超过轮数时显式 `COVERAGE_INCOMPLETE`。
6. v2 编排执行全部计划块，有界重试，任一块解析失败或截断时整体失败。
7. 双语固定前缀和可变主审单元分离，实际 `max_tokens` 使用规划的输出预算。
8. 严格结果校验拒绝额外文本、类型错误、未知引用、未知条款及法规元数据不一致。

## 定向验证

```text
23 passed, 33 warnings
31 passed, 32 warnings
17 passed, 1 warning
```

警告来自既有 pytest/anyio/jieba 弃用项、`datetime.utcnow()` 及 Windows asyncio 资源回收；本轮未将警告冒充为全量无告警通过。

## 未完成

- 完整 `tests/fixtures/audit_v2/manifest.json` 和授权真实样本。
- llama.cpp `/apply-template`、`/tokenize` 及 KV cache 在线能力复验。
- 跨条款“缺失/未约定”候选复核和冲突聚合。
- 持久化任务、API、前端、全量质量与真实性能验收。
