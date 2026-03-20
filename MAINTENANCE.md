# 合同审计模块维护指南 (Maintenance Guide)

## 1. 架构概览

合同审计模块（`app/services/contract_audit.py` 及其子模块）负责统筹基于 LLM 的长短记忆合同审计管线。
重构后采用了 **门面模式 (Facade Pattern)** 与 **单一职责原则 (SRP)**。

### 模块依赖图
```text
[ API 层 ]
   │
   ▼
[ contract_audit.py (Facade) ] ───▶ [ memory_pipeline.py ] (核心管线编排)
   │                                     │
   ├──▶ [ clause_builder.py ]            ├──▶ [ citation_catalog.py ] (白名单管理)
   ├──▶ [ trace_writer.py ]              ├──▶ [ risk_suppression.py ] (风险抑制)
   └──▶ [ async_bridge.py ]              └──▶ [ result_assembler.py ] (结果归一)
                                         │
                                         ▼
                             [ utils/contract_audit_utils.py ] (底层纯函数)
```

## 2. 如何新增或修改规则

### 2.1 修改“缺失类风险”识别关键词
若需增加 LLM 识别“未提及/缺失”类风险的拦截，请修改：
- **文件**: `app/services/contract_audit_modules/risk_suppression.py`
- **变量**: `MISSING_RISK_MARKERS`

### 2.2 增加全局涉税反证的检测维度
若需在全合同上下文中收集新的业务要素（例如：印花税承担方）：
- **文件**: `app/services/contract_audit_modules/risk_suppression.py`
- **变量**: `RISK_TOPIC_KEYWORDS` 增加新 key 与关键词列表。
- 逻辑 `build_global_tax_context` 会自动提取该主题的上下文。

### 2.3 调整记忆管线 Token 限制与策略
- 修改外层配置 `app/config.json` 中的 `memory_short_token_limit`、`memory_max_rounds` 等参数。
- 在 `memory_pipeline.py` 中 `MemoryManagerConfig` 的初始化部分可调整缺省容灾参数。

## 3. 如何调试与查看日志

### 3.1 独立 Trace 日志查看 (强烈推荐)
为了防止刷屏，每次审计的详细大段 Prompt 和 LLM Response 已经剥离主日志。
- **路径**: `data/memory/debug/contract_audit_trace/YYYY-MM-DD/contract_audit_trace.jsonl`
- **格式**: JSONL（每行一条）
- **查看方法**:
  ```bash
  # Windows PowerShell
  Get-Content data\memory\debug\contract_audit_trace\2026-03-20\contract_audit_trace.jsonl -Tail 10
  ```

### 3.2 模块级报错定位
由于引入了 `structlog`，所有的日志都会附带模块名（`logger = structlog.get_logger(__name__)`）。
若在主应用日志中看到 `app.services.contract_audit_modules.risk_suppression` 报错，可直接去该文件定位问题。

## 4. 测试建议
- **运行单元测试**:
  ```bash
  $env:PYTHONPATH="."; pytest tests/test_contract_audit.py -v
  ```
- 修改子模块后，务必确保 `test_contract_audit.py` 中覆盖该函数的边界情况。所有核心函数现已设计为无状态（传入 list/dict，返回处理后的结果），非常适合快速编写 `pytest`。
