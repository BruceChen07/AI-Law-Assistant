# 09 GPT 5.6 Sol 工程执行约定

## 1. 角色与输入

你是本项目的实现工程师。输入是完整工作树、本目录全部文档、用户授权和经脱敏的测试数据。不要仅依赖上轮对话摘要。模型名称不改变本项目的安全、质量和Git授权规则。

本交接包不要求特殊Agent平台。可由单个工程师/模型顺序完成；除非用户另行授权，不假设有并行子Agent。跨上下文交接靠本地状态文件，不靠聊天记忆。

## 2. 第一次启动必须执行

1. 阅读00～08，核对 `delivery-manifest.json` 文件齐全、版本一致、任务全部初始TODO。
2. 确认用户已批准工程实现；未批准只做文档审阅，不执行迁移/代码修改。
3. 在仓库根目录运行 git status/branch/log/diff 和工具版本检查，记录HEAD、dirty摘要和变更归属。
4. 重新确认服务PID、端口、磁盘余量；当前服务运行状态不能沿用2026-11-03的观察。
5. 创建独立工程证据目录，如 `docs/engineering/contract-audit-optimization-20261103/execution/<run_id>/`。run_id使用UTC `YYYYMMDDTHHMMSSZ`。当前交付不预创建运行证据以免误认为已执行。
6. 初始化下面的任务台账和问题表，按T00启动；禁止跳到“缩短prompt/减轮数”。

## 3. 工程状态文件模板

在execution根目录维护 `state.json`，每完成一步原子更新：

```json
{
  "schema_version": 1,
  "design_version": "1.0",
  "run_id": "YYYYMMDDTHHMMSSZ",
  "head": "",
  "dirty_files": [],
  "phase": "P0",
  "task_id": "T00",
  "task_status": "TODO",
  "last_completed_task": null,
  "next_action": "核对工作树和验证环境",
  "blocking_dependencies": [],
  "approved_exceptions": [],
  "evidence_files": [],
  "updated_at": null
}
```

`tasks.json` 从delivery-manifest的tasks复制，每任务补 `{started_at,finished_at,files_changed,test_ids,evidence,blocker,reviewer}`。完成前必须检查dependencies全部DONE/获准WAIVED；安全门不能WAIVED。不要修改原始交付manifest中的TODO来冒充实施状态。

## 4. 每步执行日志

`commands.jsonl` 每行一条：

```json
{"task_id":"T00","started_at":"2026-11-03T08:00:00.000Z","finished_at":"2026-11-03T08:00:01.000Z","cwd":"D:/Workspace/AI-Law-Assistant","command":"git status --short --branch","exit_code":0,"stdout_path":"logs/T00-status.stdout.txt","stderr_path":"logs/T00-status.stderr.txt","production_touched":false}
```

示例仅示范格式，不是实际执行记录。凭据必须以 `<REDACTED>` 脱敏，不能把完整环境变量/用户token写日志。命令长时间运行时记录runner/process ID和当前状态，终止后补exit_code或interrupted，不假报0。

每任务的 `Txx-summary.md`：

```markdown
# Txx 执行记录
## 输入与依赖
- 设计版本：
- 源码/配置hash：
- 依赖任务状态：
## 修改
- 文件/函数及修改原因：
- 保留的用户改动：
## 验证
| 测试ID | 命令或场景 | 开始/结束 | 结果 | 证据相对路径 |
|---|---|---|---|---|
## 风险与未完成项
- 已知限制：
- 需要审批：
## 下一步
- 下一任务：
- 不得执行的动作：
```

## 5. 问题与截图记录模板

`issues.md` 每个问题使用：

| 字段 | 必填内容 |
|---|---|
| issue_id / category / severity | 分类：代码冲突、编译错误、功能异常、性能问题、兼容性缺陷、安全问题、环境阻断 |
| observed_at / commit / config_hash | 带时区时间、代码和配置定位 |
| preconditions | 服务版本、测试身份、输入sample_id、端口、是否fake模型 |
| reproduce | 可重复的有序操作，不写“偶现失败”代替步骤 |
| expected / actual | 具体状态码、字段、耗时或输出差异 |
| evidence | 日志文件及行范围、截图文件、测试报告路径 |
| impact / workaround | 影响用户/功能，规避是否降低质量 |
| owner / status / retest | 责任、开放/修复/复测通过，复测命令和时间 |

截图命名 `<UTC>_<test_id>_<stage>.png`；`screenshots/index.json` 记录文件、页面URL、viewport、job_id脱敏值、ts、预期和实际。不得截录真实token、保密全文。没有截图时用结构化日志作为证据并说明场景无需UI，禁止伪造截图。

## 6. 每次编码循环

1. 阅读最新源文件和git diff，确认没有并行用户改动冲突。
2. 先写能复现问题的测试，记录旧实现失败。
3. 按03/04/05契约最小实现，新增配置有默认值/校验/路径规范化。
4. 同步更新中英prompt、接口schema和测试，不能仅修中文表现。
5. 运行该任务定向回归，然后阶段全量；测试环境写入和线程泄漏也要检查。
6. 将错误分类，修复后记录新run证据，不覆盖旧失败记录。
7. 达门后更新state/tasks/summary，给用户简短总结；不自动git commit/push。

停止条件：需改业务边界、需新增云服务/模型、需重启生产、需修改用户既有冲突代码、需放宽质量门、缺真实标注/运行资源。停止相关危险动作，说明可继续的隔离测试；不要让用户重做本可从代码读取的信息。

## 7. 必须避免的捷径

- 把模型“已调用”当作全文已覆盖；把解析字符覆盖当作OCR准确率。
- 将max_rounds减小或设置超长上下文以绕过漏段问题。
- 为过测试删除引用验证、关闭全部重试或把失败改成空风险。
- 调小max_tokens后宣称速度按比例提升；把约7 token/s当成所有机器的实测结果。
- 把线程池/202响应当作模型计算加速；把HTTP断连当作slot已释放。
- 绕过owner鉴权、跨用户复用prompt cache、缓存已失效法规。
- 在测试时修改真实app/config.json、真实管理员密码或业务数据库。
- 在缺少外部模型/联网时虚构完成、跳过统计、用mock性能替代真实模型性能。

## 8. 最终交付包与完成定义

必须提供：

1. 完整代码diff和变更摘要，明确未提交状态。
2. 迁移/回滚脚本、OpenAPI、配置schema和锁定依赖清单。
3. 所有阶段G0～G6状态及证据，测试矩阵对应情况。
4. 基准B1/B2数据、专家质量评估、目标达成与差距，冷暖运行分别展示。
5. 离线安装包manifest、SHA256、预检与启动命令、备份恢复记录。
6. 残余风险、未验证平台、经批准例外、维护操作和下一任执行者提示。

没有真实全覆盖完成结果、没有质量验收、没有部署演练时，不得写“全部落地完成”。可交付阶段成果，但须明确还缺什么。

## 9. 中断后交接

先读state.json→当前任务summary→issues.md→最近命令日志，再检查进程/文件/数据库迁移实际状态。工具中断可能已部分执行，不能直接重复迁移、提交或删除操作。恢复运行使用新的run_id，引用前一run_id，不覆盖证据。

交接摘要必须回答：现在在哪个任务、已完成哪些门、改了哪些文件、哪些命令仍在运行、下一条安全动作、哪些动作需要审批。
