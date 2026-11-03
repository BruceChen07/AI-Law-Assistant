# 合同审核阶段审计日志系统使用手册

## 1. 启用方式

系统已在应用启动时自动初始化，无需单独执行迁移命令。

默认生成以下独立资源：

- 日志数据库：`data/audit_logs/contract_audit_stage_logs.db`
- 导出目录：`data/exports/contract_audit_logs/`

可通过配置项覆盖：

- `contract_audit_log_db_path`
- `contract_audit_log_export_dir`

## 2. 自动记录范围

当用户通过以下接口触发合同审核时，系统会自动记录阶段日志：

- `POST /contracts/audit`
- `POST /contracts/{document_id}/pipeline/run`

记录内容包括：

- 合同名称
- 毫秒级 UTC 时间戳
- 审核阶段
- 阶段状态与结果
- 操作人 ID
- IP 地址
- 审核意见
- 扩展上下文

## 3. 查询接口

### 3.1 查询日志列表

请求：

```http
GET /contract-audit-logs?contract_name=采购合同&stage=done&started_at_utc=2026-10-16T00:00:00.000Z&ended_at_utc=2026-10-16T23:59:59.999Z
Authorization: Bearer <token>
```

说明：

- 普通用户默认只能查询自己的日志
- 管理员可额外使用 `operator_id` 查询指定用户日志

返回字段：

- `items`
- `total`
- `limit`
- `offset`
- `filters`

## 4. 导出接口

### 4.1 导出 JSONL

```http
GET /contract-audit-logs/export?stage=failed&export_format=jsonl
Authorization: Bearer <token>
```

### 4.2 导出 CSV

```http
GET /contract-audit-logs/export?contract_name=采购合同&export_format=csv
Authorization: Bearer <token>
```

导出文件字段：

- `id`
- `event_key`
- `audit_id`
- `document_id`
- `contract_name`
- `occurred_at_utc`
- `stage_code`
- `stage_status`
- `stage_result`
- `stage_message`
- `audit_opinion`
- `operator_id`
- `ip_address`
- `source_endpoint`
- `payload`

## 5. 典型复盘方法

### 5.1 查看某份合同完整审核轨迹

按合同名称 + 时间范围查询：

- `contract_name=xxx`
- `started_at_utc`
- `ended_at_utc`

观察阶段顺序是否完整：

- `received`
- `saved`
- `document`
- `digitizing`
- `extracting`
- `retrieval`
- `auditing`
- `done / failed`

### 5.2 排查失败原因

筛选：

- `stage=failed`

重点查看：

- `stage_result`
- `stage_message`
- `audit_opinion`
- `payload.error_detail`

### 5.3 统计某位操作人的审计过程

管理员使用：

- `operator_id=<user-id>`

## 6. 运维建议

- 建议定期归档导出目录
- 若用于长期复盘，建议将独立日志库纳入备份策略
- 如需更高并发与更长保留周期，可平滑迁移到专用审计存储
