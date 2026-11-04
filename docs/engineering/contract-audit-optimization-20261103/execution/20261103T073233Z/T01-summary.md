# T01 执行记录

## 输入与依赖

- 设计版本：1.0
- 依赖任务：T00 DONE
- 隔离根目录：`D:/AuditValidation/20261103T073233Z`

## 修改

- 使用现有 `create_app` 注入能力和 `prepare_audit_validation.py`。
- 生成独立 APP_CONFIG、数据库、文件、日志、trace和TEMP路径。
- 未修改生产 `app/config.json` 或 `data/app.db`。

## 验证

| 测试ID | 命令或场景 | 结果 | 证据 |
|---|---|---|---|
| T01隔离配置 | `prepare_audit_validation.py --mode unit` | PASS | `D:/AuditValidation/20261103T073233Z/config.json` |
| 定向回归 | planner/v2/validation/admission/environment | 34 passed, 0 failed | `D:/AuditValidation/20261103T073233Z/evidence/pytest-targeted.xml` |
| 生产保护 | 测试前后数据库hash/mtime核对 | PASS | `baseline.json` |

## 风险与未完成项

- 21条warning主要为既有UTC API弃用和Windows asyncio未回收操作告警，未影响本轮断言，但需后续分类。
- 系统Python最初缺少pytest；安装器在写入口exe时受沙箱限制，但模块安装成功并以 `python -m pytest` 运行。
- 未执行真实模型、OCR或浏览器测试。

## 下一步

- T02：补齐固定合成样本、manifest和旧缺陷复现说明。
- 不得把fake LLM回归当作真实法律质量或性能验收。
