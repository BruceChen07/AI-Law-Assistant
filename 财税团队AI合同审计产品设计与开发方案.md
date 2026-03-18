# 财税团队 AI 合同审计产品设计与开发方案（重构版）

## 0. 文档目的
本方案仅面向“财税合同审计”场景，审计依据仅来自用户上传的财税法规文件，不引入民法典、著作权法等外部通用法条。目标是在现有项目基础上，形成可落地、可迭代、可追溯的审计系统。

## 1. 业务边界

### 1.1 处理对象（必须满足）
- 合同范围：仅处理财税相关合同与协议（涉税条款、财政补贴、税收优惠、转让定价、发票与结算、代扣代缴、税务申报义务等）。
- 法规范围：仅使用用户上传的财税法规文件（PDF/Word/Excel/扫描件）作为审计依据。
- 审计结论范围：仅输出“合同条款与用户法规规则库”的符合性结论，不扩展到外部法律体系。

### 1.2 不在范围（明确剔除）
- 不自动联网抓取通用法条。
- 不输出与财税无关的法律风险结论。
- 不以模型常识替代法规证据；所有高/中风险必须可追溯到上传法规条款。

---

## 2. 核心功能与流程

### 2.1 法规文件解析（规则库构建）
输入：用户上传财税法规文件（PDF/Word/Excel/扫描件）。

输出：结构化“财税规则库（Tax Rule Base）”。

关键能力：
- 文档解析与 OCR：支持原生文本 + 扫描件 OCR 回退。
- 条款切分：按“章/节/条/款/项”切分，保留原文定位。
- 字段抽取：提取税种、税率、计税依据、适用主体、适用区域、生效/失效日期、时限、审批要件、豁免条件、罚则。
- 规则标准化：将条款转换为统一规则表达（Rule DSL/JSON）。
- 版本管理：同一法规支持版本替换、历史追溯、灰度生效。

规则实体建议：
- rule_id, doc_id, law_title, article_no, rule_type
- trigger_condition, required_action, prohibited_action
- numeric_constraints（税率/金额/比例区间）
- deadline_constraints（时限）
- region, industry, effective_date, expiry_date
- source_page, source_paragraph, source_text

### 2.2 合同结构化（合同条款树）
输入：待审计合同（PDF/Word/扫描件）。

输出：合同条款树（Contract Clause Tree）。

关键能力：
- 文档解析：优先原生解析，文本不足触发 OCR。
- 段落切分：标题层级识别 + 条款编号归并。
- 条款级 NER：识别金额、税率、税目、开票主体、开票时间、付款条件、代扣代缴义务、优惠资格、关联交易要素等。
- 条款归一化：生成 clause_id、页码、段落号、原文偏移位置，支持后续定位回跳。

### 2.3 语义级匹配（混合检索 + 财税模型）
目标：将合同条款与财税规则库精准关联。

检索策略：
- 关键词检索：BM25/FTS5，优先召回显式税务术语。
- 向量检索：768 维嵌入向量召回语义近邻条款。
- 重排：财税领域 Cross-Encoder / 微调 BERT 进行相关性重排。
- 双路召回：原文查询 + 财税增强查询并行，避免仅税务词前缀导致的漏召回。

匹配输出：
- compliant（符合）
- non_compliant（不符合）
- not_mentioned（未提及）

### 2.4 风险提示（高/中/低）
针对 non_compliant / not_mentioned 条款生成风险项。

风险分级建议：
- 高风险：违反强制性财税规则或存在明确处罚风险。
- 中风险：条款不完整/约束不充分，可能引发税务调整。
- 低风险：表述不严谨但可通过补充条款修复。

每条风险必须包含：
- 风险描述（自然语言）
- 触发规则（rule_id + law_title + article_no）
- 证据片段（法规原文摘要 + 合同原文片段）
- 定位信息（合同页码、段落、clause_id）
- 修订建议（可直接用于合同修改）

### 2.5 交互式复核（人机协同）
面向法务/财税复核人员：
- 一键跳转：风险项跳转到合同原文与法规原文。
- 人工标注：确认/驳回风险，调整风险等级。
- 例外登记：填写“例外理由 + 责任人 + 到期日”。
- 审计轨迹：全量记录“模型结论→人工复核→最终结论”。
- 回写结果：将最终审计结果回写报告与任务状态。

---

## 3. 技术架构（兼容当前实现）

## 3.1 总体架构
- 后端：Python 3.11 + FastAPI。
- 服务形态：解析服务、比对服务、报告服务（微服务边界），初期可在单仓 FastAPI 内按模块部署，后续独立扩容。
- 前端：React + Ant Design（保持现有 React + Vite，不破坏现有页面，新增财税审计工作台采用 Ant 组件）。
- 向量库：Milvus 2.4（768 维，内网部署，ACL）。
- 文件存储：MinIO（S3 兼容、加密落盘、30 天生命周期清理）。
- 模型层：
  - 财税专用 BERT（10 万级标注条款）用于条款分类/匹配/风险分级辅助。
  - 本地 4-bit 量化 LLM 用于风险解释、摘要和建议生成（数据不出域）。

### 3.2 与当前项目兼容策略
当前项目已有：FastAPI、React、OCR、混合检索、Reranker、审计日志。

兼容落地方式：
- 保留现有 API 与页面，新增 `/tax-audit/*` 业务路由与服务层。
- 检索层先复用当前 SQLite FTS + 向量流程，随后平滑迁移至 Milvus（双写 + 灰度切换）。
- 文件层先复用本地存储接口抽象，新增 MinIO 适配器，逐步切换。
- 审计引擎先保留现有规则与提示词框架，再注入“财税规则库约束”和“仅用户法规证据”硬限制。

### 3.3 微服务边界定义
- 解析服务（Parser Service）
  - 文件解析、OCR、条款切分、字段抽取、规则入库。
- 比对服务（Match Service）
  - 合同结构化、规则检索、语义匹配、风险判定。
- 报告服务（Report Service）
  - 风险报告生成、审计轨迹汇总、导出与回写。

服务间建议：
- 同步：REST/gRPC（内部）。
- 异步：任务队列（法规入库、大文件审计、批量报告）。

---

## 4. 数据模型与接口定义

### 4.1 核心数据模型
- regulation_document
  - doc_id, file_name, file_type, version, uploaded_by, uploaded_at, checksum
- tax_rule
  - rule_id, doc_id, law_title, article_no, rule_type, conditions_json, actions_json, risk_default_level, effective_date, expiry_date, source_page, source_paragraph
- contract_document
  - contract_id, file_name, parse_status, ocr_used, uploaded_by, uploaded_at
- contract_clause
  - clause_id, contract_id, clause_path, page_no, paragraph_no, clause_text, entities_json
- clause_rule_match
  - match_id, clause_id, rule_id, match_score, match_label(compliant/non_compliant/not_mentioned), evidence_json
- audit_issue
  - issue_id, contract_id, clause_id, rule_id, risk_level, issue_text, suggestion, reviewer_status, reviewer_note
- audit_trace
  - trace_id, issue_id, action_type(model_generate/reviewer_confirm/reviewer_override), operator, action_time, payload_json

### 4.2 关键 API（示例）
- `POST /tax-audit/regulations/import`
- `POST /tax-audit/regulations/{doc_id}/parse`
- `POST /tax-audit/contracts/import`
- `POST /tax-audit/contracts/{contract_id}/analyze`
- `GET /tax-audit/contracts/{contract_id}/issues`
- `POST /tax-audit/issues/{issue_id}/review`
- `POST /tax-audit/contracts/{contract_id}/report`
- `GET /tax-audit/contracts/{contract_id}/trace`

---

## 5. 算法设计要点

### 5.1 规则优先 + 模型增强
- 第一层：硬规则判定（税率范围、时限、必备要件是否出现）。
- 第二层：语义匹配判定（BERT/向量/重排）。
- 第三层：LLM 仅用于解释与建议，不直接决定是否合规。

### 5.2 结果稳定性机制
- 固定检索参数与阈值版本。
- 输出强制 JSON Schema 校验。
- 风险判定规则版本化（可回放）。
- 审计结果缓存与幂等键控制，减少同文档多次波动。

### 5.3 证据约束机制
- 高/中风险必须携带 rule_id 与法规原文定位。
- 无证据时仅可输出“待人工复核”，不得输出“确定违规”。

---

## 6. 安全与合规
- 数据不出域：LLM 本地部署，内网调用。
- 存储加密：MinIO SSE + 数据库敏感字段加密。
- 访问控制：Milvus ACL + API RBAC。
- 生命周期管理：上传文件与中间产物 30 天自动清理（可按项目覆盖）。
- 审计留痕：操作日志、模型输入输出日志、人工复核日志分离存储。

---

## 7. 实施路径（高效落地）

### 阶段 A（2-4 周）
- 完成财税规则库数据模型与法规解析链路。
- 合同条款树生成与基础匹配（compliant/non_compliant/not_mentioned）。
- 输出基础风险清单与定位信息。

### 阶段 B（3-5 周）
- 接入财税专用 BERT 与重排模型。
- 上线交互式复核、例外登记、审计轨迹。
- 引入 Milvus 与 MinIO 适配层（保留旧链路回退开关）。

### 阶段 C（2-3 周）
- 本地 4-bit LLM 接入报告生成。
- 性能优化与压测，完善 ACL、清理策略、监控告警。
- 形成生产发布基线与运维手册。

---

## 8. 验收标准（面向上线）
- 业务边界：100% 仅使用用户上传财税法规作为审计依据。
- 审计完整性：每条高/中风险具备法规证据链与合同定位信息。
- 结果稳定性：同一合同重复审计，风险数量波动率控制在可配置阈值内。
- 性能：单份 30 页合同在目标硬件下可在可接受时间内完成审计。
- 可追溯：人工复核、例外理由、结果回写全链路可查询。

---

## 9. 结论
本重构方案将系统严格收敛到“财税法规 vs 合同条款”的符合性比对主线，在兼容当前 FastAPI + React + OCR + 混合检索架构的前提下，逐步演进到“微服务 + Milvus + MinIO + 本地量化 LLM”的生产级方案，可直接指导下一阶段研发实施。
