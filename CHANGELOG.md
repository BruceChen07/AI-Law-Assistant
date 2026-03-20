# CHANGELOG

## [Unreleased]
### Refactored
- 重构了 `app/services/contract_audit.py`，采用单一职责原则（SRP）将内部长代码拆分为多个高内聚的子模块。
- **接口变更点**：零（0）。对外调用方（例如 API 路由）对 `audit_contract` 的调用方式及返回结构保持 100% 一致。

### 拆分前后文件对照表

| 原功能/职责 | 原代码位置 | 新代码位置 (拆分后) |
| :--- | :--- | :--- |
| **纯文本处理与工具函数** (如去重、归一化) | `contract_audit.py` | `app/services/utils/contract_audit_utils.py` |
| **异步桥接** (`_run_coro_sync`) | `contract_audit.py` | `app/services/contract_audit_modules/async_bridge.py` |
| **合同条款切分与构建** (`_build_preview_clauses`) | `contract_audit.py` | `app/services/contract_audit_modules/clause_builder.py` |
| **法条白名单与目录管理** (`_build_citation_lookup` 等) | `contract_audit.py` | `app/services/contract_audit_modules/citation_catalog.py` |
| **独立轨迹日志落盘** (`_write_audit_trace` 等) | `contract_audit.py` | `app/services/contract_audit_modules/trace_writer.py` |
| **缺失类风险识别与抑制** (`_should_suppress_missing_risk` 等) | `contract_audit.py` | `app/services/contract_audit_modules/risk_suppression.py` |
| **结果拼装与风险定位** (`_attach_risk_locations` 等) | `contract_audit.py` | `app/services/contract_audit_modules/result_assembler.py` |
| **长短记忆审计主流程** (`_audit_with_memory`) | `contract_audit.py` | `app/services/contract_audit_modules/memory_pipeline.py` |
| **对外门面与 IO 编排** (`audit_contract`) | `contract_audit.py` | `app/services/contract_audit.py` (仅作 Facade 保留) |

### Added
- 引入了 `structlog` 用于模块级结构化日志输出，确保跨模块调用的可观测性。
