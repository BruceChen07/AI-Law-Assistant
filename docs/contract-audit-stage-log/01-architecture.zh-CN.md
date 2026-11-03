# 合同审核阶段审计日志系统设计文档

## 1. 目标

本系统用于为合同 AI 审核流程提供独立、可追溯、可查询、可导出的阶段审计日志能力，满足后续业务复盘、操作追责、阶段定位与运行分析需求。

设计目标：

- 与核心合同审核业务解耦，日志能力独立演进
- 使用标准化结构记录合同审核全过程
- 支持幂等写入，避免重复状态造成日志污染
- 支持查询、导出、复盘分析
- 在高并发写入场景下保持稳定，不阻塞核心审核流程

## 2. 架构设计

### 2.1 模块划分

本次新增模块分为三层：

1. `app/services/contract_audit_stage_log.py`
   - 独立日志服务层
   - 负责日志库初始化、上下文注册、阶段日志写入、查询、导出

2. `app/api/routers/contract_audit_logs.py`
   - 独立查询/导出接口层
   - 提供日志检索与标准化导出能力

3. `app/api/routers/contracts.py`
   - 仅负责在合同审核关键节点触发日志写入
   - 不承担日志存储与查询职责

### 2.2 存储方案

采用**独立 SQLite 数据库文件**存储合同审核阶段日志，默认路径：

`data/audit_logs/contract_audit_stage_logs.db`

独立库的目的：

- 避免与主业务库表耦合
- 便于独立备份、归档、迁移
- 便于后续扩展为单独服务或外部日志仓

### 2.3 数据表结构

核心表：`contract_audit_stage_log`

关键字段如下：

- `id`: 日志主键
- `event_key`: 幂等事件键，唯一约束
- `audit_id`: 审核任务 ID
- `document_id`: 合同文档 ID
- `contract_name`: 合同名称
- `occurred_at_utc`: 精确到毫秒的 UTC 时间戳
- `stage_code`: 阶段标识
- `stage_status`: 阶段状态，如 `running/done/failed`
- `stage_result`: 阶段结果，如 `running/success/AUDIT_PDF_DIGITIZATION_FAILED`
- `stage_message`: 当前阶段说明
- `audit_opinion`: 当前阶段或最终阶段的审核意见
- `operator_id`: 操作人唯一标识
- `ip_address`: 请求来源 IP
- `source_endpoint`: 触发来源接口
- `payload_json`: 扩展结构化上下文
- `created_at / updated_at`: 日志创建与更新时间

### 2.4 标准日志结构

标准输出结构如下：

```json
{
  "id": "uuid",
  "event_key": "contract-audit-progress:...",
  "audit_id": "audit-xxx",
  "document_id": "doc-xxx",
  "contract_name": "采购合同-2026版",
  "occurred_at_utc": "2026-10-16T07:00:00.123Z",
  "stage_code": "retrieval",
  "stage_status": "running",
  "stage_result": "running",
  "stage_message": "retrieving evidence",
  "audit_opinion": "",
  "operator_id": "user-123",
  "ip_address": "127.0.0.1",
  "source_endpoint": "/contracts/audit",
  "payload": {
    "progress": 40,
    "status": "running"
  }
}
```

## 3. 写入流程设计

### 3.1 上下文注册

在合同审核入口创建 `audit_id` 后，先注册日志上下文：

- 合同名称
- 文档 ID
- 操作人 ID
- IP 地址
- 来源接口

### 3.2 阶段自动记录

合同审核流程通过统一的 `update_progress(...)` 封装推进：

1. 更新原有内存进度
2. 同步写入独立审计日志
3. 保留现有进度查询接口兼容性

当前自动记录的关键阶段包括：

- `received`
- `saved`
- `document`
- `digitizing`
- `digitized`
- `extracting`
- `extract_done`
- `parsing`
- `retrieval`
- `retrieval_done`
- `auditing`
- `reviewing`
- `audit_done`
- `done`
- `failed`

### 3.3 幂等策略

日志写入采用 `event_key` 唯一约束实现幂等：

- 相同 `audit_id + status + progress + stage + message + result` 生成固定事件键
- 重复写入不会新增重复记录
- 冲突时仅更新必要字段与 `updated_at`

这样可避免：

- 前端重复轮询导致的重复阶段日志
- 同一阶段多次回调造成的日志膨胀

## 4. 查询接口设计

### 4.1 列表查询

接口：

`GET /contract-audit-logs`

支持筛选条件：

- `contract_name`
- `started_at_utc`
- `ended_at_utc`
- `stage`
- `audit_id`
- `document_id`
- `operator_id`（管理员）
- `limit`
- `offset`

返回：

- 标准化日志列表
- 总数
- 分页参数
- 实际生效过滤条件

### 4.2 导出接口

接口：

`GET /contract-audit-logs/export`

支持：

- 与列表查询相同的过滤条件
- `export_format=jsonl|csv`

导出目标：

`data/exports/contract_audit_logs/`

## 5. 性能与稳定性策略

- 独立 SQLite 数据库，避免污染主业务表
- 启用 `busy_timeout`
- 初始化时启用 `WAL` 模式增强并发读写稳定性
- 每次写入使用短连接，避免长事务阻塞
- 写入逻辑通过独立服务封装，异常仅记录错误日志，不中断核心审计流程

## 6. 后续扩展建议

后续可继续扩展：

- 审计日志归档与生命周期管理
- 前端复盘面板
- 支持按风险等级、审核模式、模型提供方过滤
- 支持投递到外部审计平台或 ELK/ClickHouse
