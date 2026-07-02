# 端侧大模型 CPU 本地化落地详细设计

## 1. 文档信息

| 字段 | 内容 |
|---|---|
| 文档状态 | Draft |
| 当前版本 | v0.1 |
| 创建日期 | 2026-06-23 |
| 适用范围 | AI-Law-Assistant 端侧大模型 CPU 本地化改造 |
| 维护要求 | 每个阶段开发开始、阶段完成、测试完成后必须更新本文档 |

## 2. 设计目标

### 2.1 总体目标
- 将当前以云端大模型为核心的合同/财税审计链路，改造为“纯 CPU 可运行的本地端侧优先”架构。
- 在不破坏现有 FastAPI + React + OCR + 检索链路的前提下，优先实现本地模型接入、任务分级路由、结构化输出稳定化和本地-only 企业部署。
- 将调研结论落成可执行设计，支持后续按阶段实施、验收和回归。

### 2.2 业务目标
- 敏感合同内容优先在本地处理，降低外发风险。
- 保持合同审计、财税匹配、实体抽取三类核心链路可用。
- 为后续模型替换、量化调优、灰度验证保留配置化能力。

### 2.3 非目标
- 本期不追求“纯 CPU 端侧完全等价替代所有云端能力”。
- 本期不实现本地多模态端到端替代 OCR，仍以 OCR + 文本审计为主。
- 本期不实现训练级微调平台，仅预留 LoRA/SFT 接入位。

## 3. 设计依据

### 3.1 当前系统代码依据
- 当前合同审计主链路要求模型输出严格 JSON，且 classic 模式默认关闭 thinking。
- memory 审计链路存在多轮调用、短时超时窗和调用预算约束。
- 税规匹配默认 4 路并发，实体抽取最高可到 8 路并发。
- 当前模型接入层优先兼容 OpenAI-compatible `/v1/chat/completions`。

### 3.2 外部模型与部署依据
- 端侧主模型优先候选：`qwen3.6:27b`。
- 端侧轻量侧车模型优先候选：`llama3.2:3b`。
- 纯 CPU 部署统一通过 `Ollama` 交付，原因是：
  - 提供标准化的模型拉取、运行与状态管理。
  - 本地运行入口一致，减少环境差异。
  - 更适合当前项目在 Windows 环境下的统一部署与运维。

### 3.3 关键约束
- 仅支持纯 CPU。
- 必须支持本地 OpenAI-compatible 服务接入。
- 结构化输出必须可解析、可校验、可修复。
- 开发过程必须记录阶段目标、开发进度和测试结果。

## 4. 当前系统问题与改造必要性

### 4.1 当前现状
- 当前默认主模型仍为云端模型，合同审计、财税匹配和实体抽取都依赖统一 LLM 封装。
- 业务侧对模型的要求不是“能回答问题”，而是：
  - 中文合同/法规/财税理解。
  - 严格 JSON 输出。
  - 多轮条款级推理。
  - 在预算限制下保持稳定。
  - 支持一定并发。

### 4.2 直接切换为单一本地模型的风险
- 纯 CPU 下大模型时延显著增加，无法直接保持当前云端体验。
- 小模型容易出现 JSON 字段缺失、证据引用错位、推理深度不足。
- 本地模型上下文和吞吐必须通过工程策略补齐，不能只依赖模型本身。

### 4.3 设计原则
- 本地优先，企业部署分支默认不允许外部云端兜底。
- 主模型和轻量模型分层。
- 任务路由而不是一刀切统一模型。
- 所有关键路径增加可观测性和回归用例。

## 5. 目标架构

### 5.1 架构总览

```text
User/API Request
    ->
Audit Orchestrator
    ->
Task Router
    -> local-small-model  (entity extraction / pre-check / simple classification)
    -> local-main-model   (contract audit / tax reasoning / structured judgment)
    -> cloud-fallback     (high-risk / timeout / invalid-json / low-confidence)
    ->
JSON Validator / Repair / Result Normalizer
    ->
Persistence / API Response / Trace
```

### 5.2 目标部署形态
- 本地推理服务 1：主模型服务
  - 推荐：`qwen3.6:27b`
  - 部署方式：`Ollama`
- 本地推理服务 2：轻量模型服务
  - 推荐：`llama3.2:3b`
  - 部署方式：`Ollama`
- 应用服务：
  - 复用现有 FastAPI。
  - 新增模型路由、降级、回退、追踪模块。

### 5.3 推荐硬件档位

| 档位 | 适用 | CPU | RAM | 存储 | 模型建议 |
|---|---|---|---|---|---|
| 最低可落地 | 单任务试运行 | 16 物理核 | 64GB | 1TB NVMe | 24B-27B Q4 主模型 |
| 推荐生产试点 | 小规模内测 | 24 物理核 | 128GB | 1TB+ NVMe | 27B Q4 + 3B 侧车 |
| 旗舰验证 | 多用户试点 | 32 物理核 | 128GB-192GB | 2TB NVMe | 27B Q4/Q5 + 独立 worker |

## 6. 模型分层与任务路由设计

### 6.1 模型角色划分

| 角色 | 模型 | 负责任务 | 设计原因 |
|---|---|---|---|
| 主审计模型 | `Qwen3.6-27B` 或 `Mistral Small 3.2 24B` | 合同审计、复杂税规判断、最终风险生成 | 推理、长上下文、结构化输出能力更强 |
| 轻量侧车模型 | `Llama 3.2-3B` | 实体抽取、条款预分类、简单三态分类、低风险解释 | 降低主模型负载，提高整体吞吐 |
| 云端兜底模型 | 保留现有云端兼容模型 | 高风险复核、超时重试、异常 JSON 修复 | 避免极端场景精度塌陷 |

### 6.2 路由规则
- `contract_audit classic`
  - 默认走主审计模型。
- `contract_audit memory clause pass`
  - 默认走主审计模型。
- `tax_matcher simple match`
  - 优先走轻量侧车模型。
  - 若结果标签冲突、置信度低或 JSON 无效，升级主审计模型。
- `tax_contract_parser entity extraction`
  - 默认走轻量侧车模型。
  - 若关键字段缺失率超过阈值，升级主审计模型。
- 以下场景统一走云端兜底：
  - 本地超时。
  - JSON 连续两次修复失败。
  - 高风险合同。
  - 重点客户或人工指定强校验任务。

### 6.3 升级判定条件
- `invalid_json_rate > 5%`
- `missing_required_fields > 0`
- `citation_id_mismatch > 0`
- `confidence < 0.65`
- `local_timeout_count >= 1`

## 7. 模块改造设计

### 7.1 配置层改造

#### 新增配置建议
- `local_llm_enabled`
- `local_llm_mode`
- `local_main_model`
- `local_small_model`
- `local_main_api_base`
- `local_small_api_base`
- `local_cloud_fallback_enabled`
- `local_routing_enabled`
- `local_timeout_sec`
- `local_json_repair_enabled`
- `local_max_parallel_requests`
- `local_audit_context_limit`

#### 建议新增配置结构

```json
{
  "local_llm": {
    "enabled": true,
    "routing_enabled": true,
    "cloud_fallback_enabled": true,
    "json_repair_enabled": true,
    "main_model": {
      "provider": "ollama",
      "api_base": "http://127.0.0.1:11434/v1",
      "model": "qwen3.6:27b"
    },
    "small_model": {
      "provider": "ollama",
      "api_base": "http://127.0.0.1:11434/v1",
      "model": "llama3.2:3b"
    },
    "routing": {
      "tax_match_use_small_model": true,
      "entity_extract_use_small_model": true,
      "high_risk_force_cloud": true
    }
  }
}
```

### 7.2 推理接入层改造

#### 目标
- 在不破坏现有 `LLMService` 对外调用方式的前提下，支持按任务选择模型。

#### 修改建议
- 扩展 `app/core/llm.py`
  - 增加任务画像参数：`task_profile`
  - 增加模型角色参数：`main / small / cloud_fallback`
  - 增加本地失败后的升级逻辑
- 新增 `app/core/llm_router.py`
  - 统一封装任务到模型的路由决策
  - 统一记录命中模型、超时、重试、回退原因

#### 路由接口建议

```python
def chat_with_profile(
    messages,
    task_profile: str,
    overrides: dict | None = None,
) -> tuple[str, dict]:
    ...
```

### 7.3 合同审计链路改造

#### 目标
- 在不改审计结果 schema 的前提下，让合同审计支持本地主模型和云端兜底。

#### 修改点
- `app/services/contract_audit.py`
  - 调用统一改为 `chat_with_profile(..., task_profile="contract_audit_main")`
  - 增加本地模型失败时的 trace 标记
- `app/services/contract_audit_modules/memory_pipeline/callbacks.py`
  - clause 审计和 memory flush 调用统一走路由层
  - 对 memory flush 使用更小上下文和更低 token 上限
- `app/services/contract_audit_modules/memory_pipeline/audit_loop.py`
  - 为本地模式新增更宽松 timeout
  - 增加 per-audit 本地预算保护

### 7.4 财税匹配链路改造

#### 目标
- 将税规三态匹配优先下沉到轻量侧车模型，降低主模型负载。

#### 修改点
- `app/services/tax_matcher.py`
  - 默认使用 `task_profile="tax_match_small"`
  - 对 `non_compliant` 或低置信度结果进行二次复核
- `app/services/tax_contract_parser.py`
  - 默认使用 `task_profile="entity_extract_small"`
  - 对关键字段缺失记录统计

### 7.5 JSON 稳定化改造

#### 目标
- 解决本地模型更容易出现的 JSON 破损、字段缺失和类型不一致问题。

#### 新增模块建议
- `app/services/json_guard.py`
  - schema 校验
  - 容错修复
  - 字段补全
  - 错误分类

#### 处理流程

```text
LLM raw output
  -> strip / normalize
  -> parse JSON
  -> validate required fields
  -> repair once if needed
  -> retry with stronger prompt if needed
  -> fallback if still invalid
```

### 7.6 追踪与观测改造

#### 目标
- 对本地模型命中、时延、回退、失败原因进行可观测。

#### 新增追踪字段
- `trace.model_role`
- `trace.model_name`
- `trace.route_reason`
- `trace.fallback_used`
- `trace.timeout_ms`
- `trace.invalid_json`
- `trace.repair_attempts`
- `trace.confidence`

#### 持久化建议
- 在现有审计 trace 基础上追加本地模型指标。
- 后续支持导出阶段测试对比数据。

## 8. 部署与运行设计

### 8.1 本地模型服务建议

#### 主模型服务
- 框架：`Ollama`
- 模型：`qwen3.6:27b`
- 端口：`11434`
- 作用：主审计和复杂推理

#### 轻量模型服务
- 框架：`Ollama`
- 模型：`llama3.2:3b`
- 端口：`11434`
- 作用：抽取、预判、轻量匹配

### 8.2 建议运行策略
- 主模型串行优先，避免 CPU 抖动。
- 轻量模型可承担有限并发。
- 审计任务采用队列化执行，不直接追求云端级并发体验。
- 高风险或大文档进入“重审计队列”。

### 8.3 推荐服务策略
- `contract_audit`：单任务串行
- `tax_matcher`：最多 2 路并发
- `entity_extract`：最多 2-4 路并发
- `memory mode`：默认关闭长上下文扩展，仅在复核阶段开启

## 9. 性能与容量设计

### 9.1 目标口径

| 指标 | 当前云端预期 | 本期端侧目标 |
|---|---|---|
| 单次普通条款判断 | 3-10s 内 | 10-30s 内 |
| 单份合同审计 | 分钟级可完成 | 可接受分钟级增长，但必须稳定完成 |
| 税规匹配并发 | 4 路 | 降为 2 路 |
| 实体抽取并发 | 8 路峰值 | 降为 2-4 路 |

### 9.2 容量结论
- 纯 CPU 端侧的首要目标不是高并发，而是稳定和可预测。
- 若不降低并发和超时阈值，当前方案不可落地。
- 必须通过模型分层、任务路由和队列控制换取稳定性。

## 10. 风险与缓解

| 风险 | 影响 | 等级 | 缓解措施 |
|---|---|---|---|
| 本地推理时延过高 | 用户等待时间增加 | 高 | 队列化、并发下调、侧车分流、云端兜底 |
| JSON 输出不稳定 | 入库与前端展示失败 | 高 | JSON Guard、强 schema、失败升级 |
| 中文财税精度不足 | 风险漏判或误判 | 高 | 保留主模型复核和高风险云端兜底 |
| 长上下文性能塌陷 | memory 模式体验差 | 高 | 采用分段审计 + summary flush |
| 本地模型升级成本高 | 运维复杂 | 中 | 固定版本、灰度升级、回归集验证 |

## 11. 实施阶段划分

### 阶段 0：基线与准备
- 目标：
  - 固化当前云端基线结果。
  - 选定主模型、侧车模型和部署框架。
  - 准备本地测试机器与模型文件。
- 交付物：
  - 基线测试报告
  - 模型清单
  - 硬件准备记录
- 验收标准：
  - 能在本地启动 OpenAI-compatible 模型服务。

### 阶段 1：本地推理接入
- 目标：
  - 接入本地主模型和侧车模型。
  - 在 `llm.py` 之上增加路由能力。
- 交付物：
  - 本地配置项
  - 路由模块
  - 基础 trace 字段
- 验收标准：
  - 合同审计、税规匹配、实体抽取都可命中本地服务。

### 阶段 2：链路改造与 JSON 稳定化
- 目标：
  - 将合同审计、memory 审计、税规匹配、实体抽取全部切到任务画像路由。
  - 完成 JSON Guard。
- 交付物：
  - 路由化业务代码
  - JSON Guard 模块
  - 错误分类与回退逻辑
- 验收标准：
  - 关键链路的 JSON 解析成功率达到目标值。

### 阶段 3：性能调优与降级策略
- 目标：
  - 完成并发下调、超时重试、云端兜底和高风险复核策略。
- 交付物：
  - 队列参数
  - fallback 策略
  - 灰度开关
- 验收标准：
  - 大多数任务稳定完成，不出现大面积超时。

### 阶段 4：回归、灰度与交付
- 目标：
  - 跑完整回归集，形成端侧交付包和验收文档。
- 交付物：
  - 回归报告
  - 部署文档
  - 已知问题列表
- 验收标准：
  - 关键业务链路通过回归和人工验收。

## 12. 开发进度记录规范

### 12.1 使用要求
- 每个阶段开始前，必须填写“阶段目标”和“计划完成时间”。
- 每次提交关键功能后，必须更新“实际进展”和“阻塞项”。
- 每次测试执行后，必须更新“测试结果记录表”。
- 若方案变更，必须在“设计变更记录”中追加一条记录。

### 12.2 阶段进度记录表

| 阶段 | 状态 | 负责人 | 计划开始 | 计划完成 | 实际完成 | 阶段目标 | 当前进展 | 阻塞项 | 下一步 |
|---|---|---|---|---|---|---|---|---|---|
| 阶段 0 | 进行中 | AI Agent | 2026-06-23 | 2026-06-24 |  | 固化本地模型配置模板、选型与验证入口 | 已补充 `local_llm` 配置结构，已确定主模型/侧车模型和 Ollama 端口约定 | 尚未完成真实本地模型下载与联通验证 | 启动本地 `Ollama` 服务并补基线记录 |
| 阶段 1 | 基础骨架完成 | AI Agent | 2026-06-23 | 2026-06-24 | 2026-06-23 | 接入本地主模型/侧车模型路由，扩展 LLM 接入层 | 已新增 `llm_router.py`，`LLMService` 支持 `task_profile` / `model_role` 路由与 trace，单元测试 9 项通过 | 业务链路尚未逐个切换到任务画像调用 | 进入阶段 2，开始改造合同审计与财税链路 |
| 阶段 2 | 进行中 | AI Agent | 2026-06-23 | 2026-06-25 |  | 将关键业务链路切到任务画像路由，并完成首批结构化稳定化能力 | 已完成 `tax_contract_parser`、`tax_matcher`、`contract_audit classic`、`memory callbacks`、`tax_risk` 的任务画像接入；已新增 `JSON Guard` 并接入 `tax_common`/`tax_risk` | 统一重试/升级策略、真实本地模型联调仍未完成 | 继续实现失败升级策略，并补更广泛的本地联调用例 |
| 阶段 3 | 进行中 | AI Agent | 2026-06-23 | 2026-06-26 |  | 完成统一失败升级策略、高风险云端复核和端侧并发收敛 | 已新增本地运行时 fallback helper；`tax_matcher` 支持异常/无效结果回退与高风险云端复核；`tax_risk` 支持高风险直接云端路由；`memory_pipeline` 条款审计与 flush 已接入 fallback；税务链路并发改为优先读取本地执行配置 | 真实本地模型压测与 memory 长文档超时验证仍未完成 | 开始真实本地模型联调，并补 memory 长文档与超时场景验证 |
| 阶段 4 | 进行中 | AI Agent | 2026-06-23 | 2026-06-26 |  | 跑完整回归集，补齐部署文档、回归报告和已知问题清单 | 已新增阶段 4 回归脚本，已生成回归报告，已补部署 Runbook 与已知问题清单 | 真实本地模型 smoke、长文档压测与人工验收仍未完成 | 启动真实本地模型后执行 smoke，并补人工验收记录 |
| 阶段 5 | 已完成 | AI Agent | 2026-06-23 | 2026-06-23 | 2026-06-23 | 完成 Ollama-only 运行栈迁移、Python 化启动入口和本机联调修复 | 已将本地模型运行统一到 `Ollama`；新增 `init.py`、`start-services.py`、`start-local-llm-servers.py`、`download-local-llm-models.py`、`apply-local-llm-config.py`；`LLMService` 已支持 `provider=ollama`；已修复 `qwen3.6:27b` thinking 兼容与 PID BOM 读取问题 | 长文档压测、人工验收、历史 UTC warnings 清理仍未完成 | 继续补真实业务样本压测与 stop-services Python 入口 |
| 阶段 6 | 已完成 | AI Agent | 2026-06-23 | 2026-06-23 | 2026-06-23 | 完成企业纯本地分支、云端依赖审计和 i5-12500/64GB 硬件适配评估 | 已创建 `feature/enterprise-local-ollama-only` 分支并禁用所有云端兜底配置；已完成全量云端依赖审计（零运行时外部调用）；已完成 `qwen3.6:27b` 和 `llama3.2:3b` 在 i5-12500/64GB 上的实测基准；已删除含硬编码 API Key 的 `bin/test_qwen_ai.py`；已新增 `bin/benchmark-local-llm.py`；53 项回归全部通过 | 27B 主模型在 i5-12500 上仅 ~2.2 tok/s，建议测试更轻主模型 | 补充 stop-services Python 入口并开展真实业务联调 |

### 12.3 设计变更记录表

| 日期 | 变更人 | 变更项 | 原方案 | 新方案 | 变更原因 | 影响范围 |
|---|---|---|---|---|---|---|
| 2026-06-23 | 初始版本 | 新建设计文档 | 无 | 本文档 v0.1 | 落实端侧本地化方案 | 全链路 |
| 2026-06-23 | AI Agent | 阶段 0/1 实现落地 | 仅有设计方案 | 已完成本地模型配置模板、LLM 路由层和基础测试 | 将设计转为可执行代码骨架 | `app/core/llm.py`、`app/core/llm_router.py`、`app/config.example.json`、`tests/` |
| 2026-06-23 | AI Agent | 阶段 3 首批实现落地 | 仅有阶段目标描述 | 已完成统一 fallback helper、高风险云端复核和并发配置收敛 | 将阶段 3 设计转为可验证代码 | `app/services/local_llm_runtime.py`、`app/services/tax_matcher.py`、`app/services/tax_risk.py`、`tests/` |
| 2026-06-23 | AI Agent | 阶段 4 交付物落地 | 仅有阶段目标描述 | 已完成回归脚本、回归报告、部署 Runbook 和已知问题清单 | 将阶段 4 交付项转为可执行与可审阅产物 | `bin/run-edge-llm-regression.ps1`、`plan/edge-llm-cpu-local-regression-report.md`、`plan/edge-llm-cpu-local-deployment-runbook.md`、`plan/edge-llm-cpu-local-known-issues.md` |
| 2026-06-23 | AI Agent | Ollama-only 迁移 | 本地运行方案同时保留 `llama.cpp` 与 `Ollama` 约定 | 已切换为仅保留 `Ollama` 运行时、模型名和部署文档 | 消除 `llama.cpp` 残留配置与脚本假设，统一本地运行栈 | `app/core/llm.py`、`bin/`、`app/config.example.json`、`README.zh-CN.md`、`plan/`、`tests/` |
| 2026-06-23 | AI Agent | 企业纯本地分支与硬件适配评估 | 仅保留本地 Ollama 配置但仍暴露云端兼容分支 | 已创建 `feature/enterprise-local-ollama-only` 分支，禁用所有云端兜底配置，删除含硬编码 API Key 的测试脚本，新增本地性能基准工具 | 满足企业内网安全合规要求，完成 i5-12500/64GB 硬件实测评估 | `bin/test_qwen_ai.py`（删除）、`bin/benchmark-local-llm.py`（新增）、`app/config.example.json`、`plan/` |

### 12.4 当前阶段改动文件记录

| 阶段 | 文件 | 改动 |
|---|---|---|
| 阶段 0 | `app/config.example.json` | 新增 `local_llm` 配置模板、主模型/侧车模型与路由映射 |
| 阶段 1 | `app/core/llm_router.py` | 新增本地主模型/侧车模型/云端回退路由解析 |
| 阶段 1 | `app/core/llm.py` | 新增 `task_profile` / `model_role` 支持、route trace、`chat_with_profile()` |
| 阶段 1 | `tests/test_llm_router.py` | 新增路由决策单元测试 |
| 阶段 1 | `tests/test_llm_local_mode.py` | 新增本地模式接入单元测试 |
| 阶段 2 | `app/services/tax_common.py` | 修复 `parse_llm_json_object()` 缺少 `re` 导入的问题 |
| 阶段 2 | `app/services/tax_contract_parser.py` | 实体抽取接入 `entity_extract_small` 任务画像，并兼容无 `chat_with_profile()` 的假 LLM |
| 阶段 2 | `app/services/tax_matcher.py` | 税规匹配接入 `tax_match_small` 任务画像，并兼容旧式 `chat()` 调用 |
| 阶段 2 | `app/services/contract_audit.py` | classic 合同审计接入 `contract_audit_main` 任务画像 |
| 阶段 2 | `app/services/contract_audit_modules/memory_pipeline/callbacks.py` | 条款审计接入 `contract_clause_audit`，memory flush 接入 `memory_flush` |
| 阶段 2 | `app/services/audit_tax.py` | 增加 `_is_tax_related_text()` 兼容别名，修复现有测试收集失败 |
| 阶段 2 | `app/services/json_guard.py` | 新增容错 JSON 提取与轻量修复底座 |
| 阶段 2 | `app/services/tax_common.py` | LLM JSON 解析统一改为复用 `JSON Guard` |
| 阶段 2 | `app/services/tax_risk.py` | 风险生成接入 `tax_risk_main` 任务画像与结构化容错解析 |
| 阶段 2 | `tests/test_tax_contract_parser.py` | 新增实体抽取 small 路由测试 |
| 阶段 2 | `tests/test_tax_matcher.py` | 新增税规匹配 small 路由测试，并升级端到端测试依赖 |
| 阶段 2 | `tests/test_json_guard.py` | 新增 fenced JSON、尾逗号修复与默认值回退测试 |
| 阶段 2 | `tests/test_tax_risk.py` | 新增 `tax_risk_main` 路由与 JSON Guard 联合回归 |
| 阶段 3 | `app/services/local_llm_runtime.py` | 新增统一 fallback、云端回退和并发配置解析 helper |
| 阶段 3 | `app/services/tax_matcher.py` | 增加异常/无效结果云端回退、高风险云端复核和本地并发限制 |
| 阶段 3 | `app/services/tax_risk.py` | 高风险项支持直接云端复核，并使用本地并发限制 |
| 阶段 3 | `app/config.example.json` | 新增 `local_llm.execution` 配置段，描述 fallback 与 worker 限制 |
| 阶段 3 | `tests/test_local_llm_fallback.py` | 新增异常回退、无效结果回退和 worker 配置优先级测试 |
| 阶段 3 | `tests/test_tax_matcher.py` | 新增高风险云端复核测试 |
| 阶段 3 | `tests/test_tax_risk.py` | 增加高风险风险生成命中云端复核断言 |
| 阶段 3 | `app/services/contract_audit_modules/memory_pipeline/callbacks.py` | 条款审计与 memory flush 接入统一 fallback，并记录 fallback trace |
| 阶段 3 | `tests/test_memory_pipeline_fallback.py` | 新增 memory clause 坏 JSON 回退与 flush 异常回退测试 |
| 阶段 4 | `bin/run-edge-llm-regression.ps1` | 新增阶段 4 回归脚本，可生成 Markdown 回归报告并支持可选 smoke |
| 阶段 4 | `plan/edge-llm-cpu-local-regression-report.md` | 新增阶段 4 回归报告文件，记录 53 项回归结果 |
| 阶段 4 | `plan/edge-llm-cpu-local-deployment-runbook.md` | 新增端侧 CPU 本地部署与联调 Runbook |
| 阶段 4 | `plan/edge-llm-cpu-local-known-issues.md` | 新增阶段 4 已知问题与关闭条件清单 |
| 阶段 5 | `app/core/llm.py` | 新增 `provider=ollama` 的官方 API 调用路径 |
| 阶段 5 | `bin/download-local-llm-models.py` | 从 GGUF 文件下载切换为通过 Ollama API 拉取模型 |
| 阶段 5 | `bin/start-local-llm-servers.py` | 从 `llama-server` 启动脚本切换为 `ollama serve` 管理脚本 |
| 阶段 5 | `bin/apply-local-llm-config.py` | 将本地模型配置切换为 `ollama` provider 与 `11434/v1` |
| 阶段 5 | `app/config.example.json` | 示例配置切换为 Ollama-only |
| 阶段 5 | `tests/test_llm_router.py` | 路由测试更新为 Ollama 模型名与端点 |
| 阶段 5 | `tests/test_llm_local_mode.py` | 本地模式测试改为覆盖 Ollama 官方 API 分支 |
| 阶段 6 | `bin/test_qwen_ai.py` | 删除：移除了含硬编码 DashScope API Key 的调试脚本（安全清理） |
| 阶段 6 | `bin/benchmark-local-llm.py` | 新增：企业本地部署硬件评估基准测试工具，支持双模型（27B/3B）吞吐与延迟验证 |
| 阶段 6 | `app/config.example.json` | 更新：默认禁用所有云端兜底开关，强制本地 Ollama 唯一数据通路 |
| 阶段 6 | `plan/edge-llm-cpu-local-deployment-detailed-design.md` | 更新：追加阶段 6 进度、变更记录和文件改动清单 |
| 阶段 6 | `plan/edge-llm-cpu-local-known-issues.md` | 更新：标记 KI-001 为已缓解，新增 KI-006（26GB 模型加载延迟）与 KI-007（i5-12500 CPU 瓶颈） |

## 13. 测试设计与结果记录

### 13.1 测试目标
- 验证本地模型接入可用。
- 验证核心链路结构化输出稳定。
- 验证端侧改造后现有能力不发生明显退化。
- 验证降级和兜底策略可触发、可追踪。

### 13.2 测试范围
- 单元测试
  - 路由决策
  - JSON Guard
  - 回退判定
  - 配置解析
- 集成测试
  - 合同审计主链路
  - memory 审计
  - 税规匹配
  - 财税实体抽取
- 回归测试
  - 现有 API 不破坏
  - 现有前端调用不破坏
  - OCR/RAG/导出链路不退化
- 非功能测试
  - 时延统计
  - 超时场景
  - 长文档场景
  - fallback 场景

### 13.3 推荐新增测试文件
- `tests/test_llm_router.py`
- `tests/test_llm_local_mode.py`
- `tests/test_json_guard.py`
- `tests/test_local_llm_fallback.py`
- `tests/test_contract_audit_local_mode.py`
- `tests/test_tax_matcher_local_mode.py`
- `tests/test_tax_contract_parser_local_mode.py`

### 13.4 测试用例结果记录表

| 用例编号 | 用例名称 | 所属阶段 | 输入场景 | 预期结果 | 实际结果 | 是否通过 | 备注 |
|---|---|---|---|---|---|---|---|
| TC-001 | 本地主模型可连通 | 阶段 1 | 访问本地 `/v1/chat/completions` | 返回有效响应 | 待执行 | 待定 | 需真实本地模型服务 |
| TC-002 | 轻量模型路由命中 | 阶段 1 | tax match 简单请求 | 命中 small model | 已通过单元测试 `tests/test_llm_router.py` 和 `tests/test_llm_local_mode.py` | 是 | 当前为 mock 路由验证 |
| TC-002A | 默认任务命中主模型 | 阶段 1 | `task_profile=default` | 命中 main model | 已通过 `tests/test_llm_router.py` | 是 | mock 路由验证 |
| TC-002B | 显式 small 任务命中侧车模型 | 阶段 1 | `task_profile=tax_match_small` | 命中 small model | 已通过 `tests/test_llm_router.py` 和 `tests/test_llm_local_mode.py` | 是 | mock 路由验证 |
| TC-002C | 侧车配置缺失时回退云端 | 阶段 1 | small model 缺失 | 自动选择 cloud fallback | 已通过 `tests/test_llm_router.py` | 是 | mock 路由验证 |
| TC-003 | 合同审计 JSON 可解析 | 阶段 2 | 普通合同审计 | 返回合法 JSON | 已通过 `tests/test_contract_audit.py` 回归验证 | 是 | 当前验证 classic/memory 兼容性 |
| TC-003A | 实体抽取命中 small 任务画像 | 阶段 2 | 合同条款实体抽取 | 命中 `entity_extract_small` | 已通过 `tests/test_tax_contract_parser.py` | 是 | mock LLM 路由验证 |
| TC-003B | 税规匹配命中 small 任务画像 | 阶段 2 | clause-rule LLM 判定 | 命中 `tax_match_small` | 已通过 `tests/test_tax_matcher.py` | 是 | mock LLM 路由验证 |
| TC-003C | classic 合同审计兼容旧式 chat 假 LLM | 阶段 2 | memory/classic 审计回归 | 既有测试不破坏 | 已通过 `tests/test_contract_audit_memory_mode.py` 和 `tests/test_contract_audit.py` | 是 | 通过 fallback helper 兼容 |
| TC-004 | JSON 修复逻辑生效 | 阶段 2 | 构造 fenced JSON、尾逗号 JSON、无效 JSON | 自动解析、修复或使用默认值 | 已通过 `tests/test_json_guard.py` | 是 | 当前为轻量修复，不含多轮重试 |
| TC-004A | tax_risk 命中主模型任务画像 | 阶段 2 | 风险描述生成 | 命中 `tax_risk_main` | 已通过 `tests/test_tax_risk.py` | 是 | mock LLM 路由验证 |
| TC-004B | tax_risk 可消费脏 JSON 输出 | 阶段 2 | fenced JSON + trailing comma | 仍能生成 issue_text/suggestion | 已通过 `tests/test_tax_risk.py` 和 `tests/test_json_guard.py` | 是 | 通过 JSON Guard 容错 |
| TC-005 | 高风险触发云端兜底 | 阶段 3 | `non_compliant` 高风险匹配样本 | fallback_used=true 或命中 cloud fallback | 已通过 `tests/test_tax_matcher.py` 和 `tests/test_tax_risk.py` | 是 | 当前为 mock LLM 路由与复核验证 |
| TC-005A | 本地异常触发云端回退 | 阶段 3 | 本地 small 模型抛异常 | 自动切换 `cloud_fallback` | 已通过 `tests/test_local_llm_fallback.py` | 是 | mock fallback 验证 |
| TC-005B | 无效结果触发云端回退 | 阶段 3 | 本地返回无效 JSON/无效结果 | 自动切换 `cloud_fallback` | 已通过 `tests/test_local_llm_fallback.py` | 是 | mock fallback 验证 |
| TC-006 | tax matcher 并发受控 | 阶段 3 | 配置本地 worker 限制 | 读取并使用本地 worker 上限 | 已通过 `tests/test_local_llm_fallback.py` | 是 | 当前验证配置解析优先级 |
| TC-007 | memory 审计坏 JSON 可自动回退 | 阶段 3 | 条款审计本地返回坏 JSON | 自动切换 `cloud_fallback` 并返回有效结构 | 已通过 `tests/test_memory_pipeline_fallback.py` | 是 | mock memory clause fallback 验证 |
| TC-007A | memory flush 异常可自动回退 | 阶段 3 | flush 本地调用抛异常 | 自动切换 `cloud_fallback` 并返回压缩结果 | 已通过 `tests/test_memory_pipeline_fallback.py` | 是 | mock memory flush fallback 验证 |
| TC-007B | memory 审计可稳定完成 | 阶段 3 | 长合同审计 | 不因超时整体失败 | 待执行 | 待定 | 需真实本地模型与长文档样本 |
| TC-008 | 现有 API 无破坏 | 阶段 4 | 阶段 4 回归脚本覆盖核心路由、结构化、fallback、memory 与合同审计测试 | 全部通过 | 已通过 `plan/edge-llm-cpu-local-regression-report.md` 记录的 53 项回归 | 是 | 当前为代码级回归，真实本地 smoke 仍待执行 |

### 13.5 阶段测试汇总表

| 阶段 | 测试时间 | 执行人 | 通过数 | 失败数 | 阻塞数 | 结论 | 报告链接/说明 |
|---|---|---|---|---|---|---|---|
| 阶段 0 | 2026-06-23 | AI Agent | 0 | 0 | 1 | 已完成配置模板与选型落地，待真实本地模型联通验证 | 本次未执行真实模型服务测试 |
| 阶段 1 | 2026-06-23 | AI Agent | 9 | 0 | 1 | 路由层和 LLM 接入骨架可用，单元测试通过 | `python -m pytest tests/test_llm_api_key_resolution.py tests/test_llm_router.py tests/test_llm_local_mode.py` |
| 阶段 2 | 2026-06-23 | AI Agent | 41 | 0 | 1 | 首批业务链路与 JSON 容错底座已打通，税务与合同审计回归通过 | `python -m pytest tests/test_json_guard.py tests/test_tax_risk.py tests/test_tax_contract_parser.py tests/test_tax_matcher.py tests/test_contract_audit_memory_mode.py tests/test_contract_audit.py`；剩余阻塞为真实本地模型联调与统一失败升级策略未完成 |
| 阶段 3 | 2026-06-23 | AI Agent | 44 | 0 | 1 | 首批性能调优与降级策略已覆盖税务链路与 memory pipeline，具备异常/无效结果回退和高风险云端复核能力 | `python -m pytest tests/test_local_llm_fallback.py tests/test_tax_matcher.py tests/test_tax_risk.py tests/test_tax_contract_parser.py tests/test_memory_pipeline_fallback.py tests/test_contract_audit_memory_mode.py tests/test_contract_audit.py`；剩余阻塞为真实本地模型联调与长文档超时压测未完成 |
| 阶段 4 | 2026-06-23 | AI Agent | 53 | 0 | 1 | 阶段 4 回归入口与交付文档已生成，核心代码级回归通过 | `powershell -ExecutionPolicy Bypass -File .\bin\run-edge-llm-regression.ps1`；已生成 `plan/edge-llm-cpu-local-regression-report.md`，剩余阻塞为真实本地 smoke 与人工验收未完成 |
| 阶段 5 | 2026-06-23 | AI Agent | 53 | 0 | 0 | Ollama-only 迁移完成，Python 启动链路和本机模型联通验证通过 | 已执行 `python -m pytest tests/test_llm_router.py tests/test_llm_local_mode.py tests/test_json_guard.py tests/test_local_llm_fallback.py tests/test_tax_contract_parser.py tests/test_tax_matcher.py tests/test_tax_risk.py tests/test_memory_pipeline_fallback.py tests/test_contract_audit_memory_mode.py tests/test_contract_audit.py`，并通过 `ollama list`、`python .\bin\start-local-llm-servers.py`、`python .\bin\download-local-llm-models.py`、`python .\bin\apply-local-llm-config.py --dry-run` 和 `LLMService` 实机调用完成本机验证 |

## 14. 代码落点建议

| 模块 | 文件 | 动作 |
|---|---|---|
| LLM 路由 | `app/core/llm_router.py` | 新增 |
| LLM 接入增强 | `app/core/llm.py` | 扩展 |
| 本地模型配置 | `app/config.example.json` | 扩展 |
| JSON Guard | `app/services/json_guard.py` | 新增 |
| 合同审计接入 | `app/services/contract_audit.py` | 改造 |
| memory 审计接入 | `app/services/contract_audit_modules/memory_pipeline/callbacks.py` | 改造 |
| memory 预算调优 | `app/services/contract_audit_modules/memory_pipeline/audit_loop.py` | 改造 |
| 税规匹配路由 | `app/services/tax_matcher.py` | 改造 |
| 实体抽取路由 | `app/services/tax_contract_parser.py` | 改造 |
| 测试 | `tests/` | 新增与扩展 |

## 15. 验收标准

### 15.1 功能验收
- 本地主模型和侧车模型都可用。
- 合同审计、税规匹配、实体抽取三条链路支持本地模式。
- JSON 解析与修复链路可用。
- 云端兜底可配置启停。

### 15.2 质量验收
- 关键测试全部通过。
- 高风险场景有明确回退策略。
- 关键 trace 字段完整。
- 文档记录完整，可追溯每一阶段开发与测试结论。

## 16. 开发执行要求

### 16.1 每阶段必须输出
- 阶段目标
- 实际改动文件列表
- 测试用例执行结果
- 已知问题与风险
- 下一阶段计划

### 16.2 每次代码合并前必须检查
- 本文档阶段进度表是否更新
- 测试结果表是否更新
- 配置变更是否同步到文档
- 若设计发生调整，是否补充到设计变更表

## 17. 下一步执行建议
- 先按阶段 0 建立本地模型基线和样本回归集。
- 再实现 `llm_router + json_guard` 两个核心底座。
- 然后逐步切入 `tax_contract_parser -> tax_matcher -> contract_audit -> memory_pipeline`。
- 完成全链路接入后再做性能调优和灰度验证。
