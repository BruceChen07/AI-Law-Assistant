# 本轮问题表

## ISSUE-001

| 字段 | 内容 |
|---|---|
| category / severity | 环境阻断 / low |
| observed_at / commit | 2026-11-03T07:29:00Z / `ca8da9d` |
| preconditions | 系统Python 3.12，首次运行pytest |
| reproduce | 执行 `python -m pytest ...` |
| expected / actual | 应运行测试；实际首次提示No module named pytest |
| evidence | `T01-summary.md` |
| impact / workaround | 仅阻断测试；安装pytest模块后使用 `python -m pytest` 通过 |
| owner / status / retest | 工程 / resolved / 34 passed |

## ISSUE-002

| 字段 | 内容 |
|---|---|
| category / severity | 部署完整性 / medium |
| observed_at / commit | 2026-11-03T07:38:48Z / `ca8da9d` |
| preconditions | 只读检查本地llama.cpp运行时 |
| reproduce | 获取 `third_party/llama.cpp/bin-win-cpu-x64/llama-server.exe` 大小与SHA256 |
| expected / actual | 应为完整可执行包；观察文件仅9216字节 |
| evidence | `baseline.json` |
| impact / workaround | 不能据此认证离线部署；P6校验完整二进制与DLL包 |
| owner / status / retest | 运维 / open / 未执行 |

## ISSUE-003

| 字段 | 内容 |
|---|---|
| category / severity | 测试运行时 / low |
| observed_at / commit | 2026-11-03T07:38:00Z / `ca8da9d` |
| preconditions | Windows Python 3.12，FastAPI TestClient |
| reproduce | 运行 `tests/test_validation_environment.py` |
| expected / actual | 无未回收操作；pytest报告一个pending overlapped operation warning |
| evidence | `D:/AuditValidation/20261103T073233Z/evidence/pytest-targeted.xml` |
| impact / workaround | 本轮断言通过，可能影响长期全量测试稳定性；后续单独复现 |
| owner / status / retest | 后端 / open / 定向测试34 passed |
