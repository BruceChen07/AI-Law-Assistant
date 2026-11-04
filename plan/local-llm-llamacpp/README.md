# llama.cpp 本地替换阶段记录索引

记录目录：`plan/local-llm-llamacpp/`

## 已完成

- `phase-01-bootstrap-implementation.md`
- `phase-02-admin-integration-implementation.md`
- `phase-01-02-test-report.md`
- `phase-03-runtime-integration-implementation.md`
- `phase-03-runtime-integration-test-report.md`
- `phase-03-runtime-cutover-runbook.md`
- `phase-04-smoke-validation-implementation.md`
- `phase-04-smoke-validation-test-report.md`
- `phase-05-ollama-replacement-feasibility-and-benchmark-plan.md`
- `phase-05-benchmark-test-report.md`
- `phase-06-pure-llamacpp-cutover-implementation.md`
- `phase-06-pure-llamacpp-cutover-test-report.md`
- `phase-07-llamacpp-runtime-binaries-import-implementation.md`
- `phase-07-llamacpp-runtime-binaries-import-test-report.md`
- `phase-08-live-inference-integration-implementation.md`
- `phase-08-live-inference-integration-test-report.md`

## 记录约定

后续每个阶段都按以下内容归档：

- 功能实现细节
- 代码变更说明
- 技术选型调整
- 功能测试结果
- 兼容性测试结果
- 性能测试/性能观察
- BUG 修复记录

## 当前约束

- 仅允许端侧本地大模型
- 禁止引入云端模型路由作为默认或回退路径
