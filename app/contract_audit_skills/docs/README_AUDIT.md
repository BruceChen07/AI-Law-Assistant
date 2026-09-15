# 合同审计Skill体系 - 部署使用文档

## 1. 改造概述

本项目在原有法律助手基础上植入了**合同审计标准化Skill体系**，采用以下架构：

### 核心方案
- **标准化Skill Prompt封装 + 技能路由调度引擎**

### 配套增强
- **方案A**：按Skill隔离独立RAG向量知识库
- **方案B**：本地轻量化Function Calling工具绑定各审计技能

### 设计原则
- 不改动原有llama.cpp模型加载、推理底层封装代码
- 不引入任何云端LLM、在线API
- 严格离线运行，不上传合同文件到外部API
- 每个Skill独立Prompt模板、独立RAG库、绑定工具列表

## 2. 新增目录结构

```
app/contract_audit_skills/          # [新增] 合同审计Skill体系根目录
├── __init__.py
├── skill_config.yaml               # [新增] 全局Skill配置文件
│
├── core/                           # [新增] 核心引擎
│   ├── __init__.py
│   ├── skill_orchestrator.py       # [新增] Skill路由调度核心引擎（最关键）
│   └── skill_rag.py               # [新增] 分技能隔离RAG调度引擎
│
├── tools/                          # [新增] 工具注册中心
│   ├── __init__.py
│   └── registry.py                 # [新增] 本地Function Calling工具注册表
│
├── templates/                      # [新增] Skill Prompt模板（Jinja2）
│   ├── skill_text_parser.j2       # [新增] Skill 0: 前置文本解析
│   ├── skill_law_compliance.j2    # [新增] Skill 1: 法律合规审查
│   ├── skill_business.j2          # [新增] Skill 2: 商务权责审计
│   ├── skill_tax_finance.j2       # [新增] Skill 3: 财税票据审计
│   ├── skill_dispute_breach.j2    # [新增] Skill 4: 违约&争议解决
│   └── skill_risk_summary.j2      # [新增] Skill 5: 风险汇总分级
│
├── cli/                            # [新增] CLI入口
│   ├── __init__.py
│   └── contract_audit.py           # [新增] 命令行审计入口
│
├── rag_store/                      # [新增] 隔离RAG向量库目录
│   ├── law_compliance/             # [新增] 法律合规法规库
│   ├── business_clause/            # [新增] 商务合同模板库
│   ├── tax_finance/                # [新增] 财税政策库
│   ├── dispute_break/              # [新增] 争议判例库
│   └── risk_history/               # [新增] 历史风险库
│
├── test/                           # [新增] 测试
│   ├── __init__.py
│   └── audit_demo.py              # [新增] 测试脚本+测试合同样例
│
└── docs/                           # [新增] 文档（本文件）
```

### 现有仓库兼容性说明

**不需要改动的原有文件**：
- `app/core/llm.py` — llama.cpp推理封装
- `app/core/config.py` — 项目配置
- `app/vector_store/` — 向量库基础设施
- `app/services/contract_audit.py` — 现有审计门面

**可能需要轻微改动的文件**（仅可选接入Web API入口）：
- `app/api/routers/contracts.py` — 可新增一个审计端点调用本Skill体系

## 3. Skill体系说明

### 6个标准化Skill

| Skill ID | 名称 | 优先级 | RAG库 | 绑定工具 |
|----------|------|--------|-------|---------|
| skill_text_parser | 前置文本解析 | 0 | 无 | OCR、金额提取、文本分块 |
| skill_law_compliance | 法律合规审查 | 1 | law_compliance | 工商核验、法条匹配 |
| skill_business_clause | 商务权责审计 | 2 | business_clause | 金额比对、账期计算 |
| skill_tax_finance | 财税票据审计 | 3 | tax_finance | 税率计算、印花税测算 |
| skill_dispute_breach | 违约&争议解决 | 4 | dispute_break | 诉讼时效、违约金测算 |
| skill_risk_summary | 风险汇总分级 | 99 | risk_history | 台账写入、报告导出 |

### 执行链路

```
合同文件上传
    ↓
Skill 0: 文本解析分块（OCR + 金额提取 + 分块）
    ↓
路由匹配（根据合同类型选择Skill组合）
    ↓
Skill 1-4: 串行执行（各Skill独立RAG检索 + 工具调用 + LLM推理）
    ↓
Skill 5: 汇总分级 + 交叉校验
    ↓
输出: JSON审计报告 + Markdown审计报告
```

### 合同类型路由

| 合同类型 | 执行Skill |
|---------|----------|
| purchase（采购） | 1+2+3+4，高风险二次复核 |
| sales（销售） | 1+2+3+4，高风险二次复核 |
| lease（租赁） | 1+2+3 |
| labor（劳务） | 1+2 |
| engineering（工程） | 1+2+3+4，高风险二次复核 |
| default（未知） | 1+2+3+4，高风险二次复核 |

## 4. 端侧大模型模型加载配置

### 必备前提
- llama.cpp 已在项目 `bin/start-local-llamacpp-server.py` 中完成封装
- 确保llama.cpp server已启动，API端点可用（如 `http://127.0.0.1:8080/v1`）
- 端侧量化模型已下载并转换为GGUF格式

### LLM配置建议

在项目 `app/config.json` 中确认以下配置：

```json
{
  "llm_config": {
    "provider": "llama_cpp",
    "api_base": "http://127.0.0.1:8080/v1",
    "model": "qwen3-6b-q4_k_m",
    "temperature": 0.1,
    "max_tokens": 2048,
    "enable_thinking": false
  }
}
```

**推荐模型参数**：
- `temperature: 0.05-0.15`（越低幻觉越少，推荐0.1）
- `max_tokens: 1024-2048`（根据模型上下文窗口调整）
- `context_window: 8192`（n_ctx，需大于最大文本块+Prompt长度）
- `enable_thinking: false`（端侧量化模型关闭思考链以加速）

**硬件参考** (i5-12500 + 64GB RAM)：
- 7B量化模型：~8-15 tok/s，单Skill审计约30-60秒
- 完整6 Skill链路：约3-8分钟

## 5. 快速开始

### 5.1 安装依赖

```bash
pip install -r requirements-audit.txt
```

### 5.2 运行测试

```bash
cd app/contract_audit_skills

# 运行所有单元测试（工具函数、配置、逻辑验证）
python test/audit_demo.py

# 仅测试工具函数
python test/audit_demo.py --test-tools

# 仅测试交叉校验逻辑
python test/audit_demo.py --test-cross-validation

# 模拟完整审计链路数据流
python test/audit_demo.py --test-pipeline
```

### 5.3 执行完整合同审计

```bash
cd app/contract_audit_skills

# 审计合同文件
python cli/contract_audit.py --file /path/to/contract.pdf --output ./output

# 自定义LLM参数
python cli/contract_audit.py --file contract.docx --temp 0.05 --max-tokens 1024

# 干运行（仅验证配置）
python cli/contract_audit.py --file contract.pdf --dry-run

# RAG库统计
python cli/contract_audit.py --stats
```

### 5.4 Python API调用

```python
from app.contract_audit_skills.core.skill_orchestrator import SkillOrchestrator
from app.core.llm import LLMService
from app.core.config import load_config

# 加载配置
cfg = load_config()
llm = LLMService(cfg)

# 创建Orchestrator
orchestrator = SkillOrchestrator(llm_service=llm)

# 执行审计
with open("contract.txt", "r") as f:
    contract_text = f.read()

report = orchestrator.audit(contract_text)

# 查看结果
print(f"综合风险等级: {report.overall_risk_level}")
print(f"发现风险: {report.risk_summary}")
for risk in report.risk_items:
    print(f"  [{risk['risk_level']}] {risk['issue']}")

# 导出报告
orchestrator.export_report(report, "audit_report.json", fmt="json")
orchestrator.export_report(report, "audit_report.md", fmt="markdown")
```

## 6. RAG知识库导入

### 6.1 目录结构准备

每个Skill的RAG库存储在独立目录下：

```
rag_store/
├── law_compliance/    # → Skill 1 法律合规
├── business_clause/   # → Skill 2 商务权责
├── tax_finance/       # → Skill 3 财税票据
├── dispute_break/     # → Skill 4 违约争议
└── risk_history/      # → Skill 5 风险汇总
```

### 6.2 JSONL导入格式

```jsonl
{"text": "法规/判例/模板文本内容", "document_name": "民法典", "paragraph": "第585条", "source": "民法典", "doc_type": "法规"}
{"text": "另一条知识文本", "document_name": "合同编司法解释", "paragraph": "第65条", "source": "司法解释", "doc_number": "法释[2023]XX号"}
```

### 6.3 通过CLI导入

```bash
# 导入法规到法律合规库
python cli/contract_audit.py --import-rag --import-skill skill_law_compliance \
    --import-path ./knowledge/laws.jsonl

# 导入财税政策到财税库
python cli/contract_audit.py --import-rag --import-skill skill_tax_finance \
    --import-path ./knowledge/tax_policies.jsonl

# 导入判例到争议库
python cli/contract_audit.py --import-rag --import-skill skill_dispute_breach \
    --import-path ./knowledge/cases.jsonl
```

### 6.4 通过Python导入

```python
from app.contract_audit_skills.core.skill_rag import (
    get_rag_manager, import_from_jsonl
)

rag_mgr = get_rag_manager()
result = import_from_jsonl("skill_law_compliance", "laws.jsonl", rag_mgr)
print(f"导入完成: {result}")
```

### 6.5 导入现有法规文件

利用项目中 `data/laws/` 目录下的法规文件：

```python
from app.contract_audit_skills.core.skill_rag import import_from_text_files, get_rag_manager
import glob

rag_mgr = get_rag_manager()
law_files = glob.glob("data/laws/*.docx")  # 使用python-docx可读取
# 先用python-docx转为纯文本，再导入...
```

## 7. 工具函数说明

### 已实现的13个本地工具

| 工具名称 | 功能 | 绑定Skill |
|---------|------|----------|
| ocr_text_extract | PDF/Word/图片文本提取 | Skill 0 |
| regex_amount_extract | 正则金额提取 | Skill 0 |
| text_chunk_splitter | 智能文本分块 | Skill 0 |
| business_entity_verify | 工商主体核验（格式校验） | Skill 1 |
| legal_keyword_matcher | 法条关键词匹配 | Skill 1 |
| amount_case_compare | 金额大小写比对 | Skill 2 |
| payment_schedule_calc | 付款节点金额核验 | Skill 2 |
| tax_rate_calculator | 增值税/所得税计算 | Skill 3 |
| stamp_duty_estimator | 印花税测算 | Skill 3 |
| statute_limitation_calc | 诉讼时效计算 | Skill 4 |
| penalty_cap_estimator | 违约金上限测算 | Skill 4 |
| risk_ledger_writer | 风险台账写入 | Skill 5 |
| report_exporter | 审计报告导出 | Skill 5 |

### 工具调用方式

端侧大模型（非标准function calling）使用**文本标记**方式调用工具：

```
模型输出中包含: FUNC_CALL: tax_rate_calculator {"amount": 5800000, "tax_type": "vat_general"}
系统解析后执行本地函数，将结果返回: FUNC_RESULT: {"tax_amount": 667256.64, ...}
```

## 8. 幻觉抑制机制

### 多层防护

1. **Prompt硬约束**：所有6个Skill模板内置不可违背硬性规则
2. **自检指令**：每个Skill输出前必须执行自检（原文证据、法条来源）
3. **交叉校验**：同一风险需2个独立Skill确认才标记为正式高风险
4. **自动降级**：单一Skill发现的风险自动标记为pending（人工复核）
5. **低温推理**：temperature默认0.1，最大限度减少自由发挥

### 端侧大模型专属约束（已写入所有Skill模板）

```
1. 你当前运行在本地llama.cpp，模型为端侧大模型量化版，上下文长度有限
2. 所有风险判断必须附带合同原文精确片段作为证据
3. 输出严格使用指定JSON格式，禁止额外解释
4. 若合同信息不足，统一输出risk_level: pending
5. 引用法规仅使用当前Skill专属RAG检索到的内容
```

## 9. 输出报告格式

### JSON报告

```json
{
  "report_id": "RPT-20260721-ABC123",
  "contract_info": {
    "contract_type": "purchase",
    "total_amount": 5800000.00,
    "parties": { ... }
  },
  "risk_summary": {
    "final_high_risks": 1,
    "final_medium_risks": 2,
    "final_low_risks": 3,
    "pending_review_risks": 1
  },
  "overall_risk_level": "medium",
  "executive_summary": "审计摘要...",
  "risk_items": [
    {
      "final_risk_id": "FINAL-001",
      "risk_category": "违约金",
      "risk_level": "high",
      "issue": "违约金每日千分之五过高",
      "original_clause_quote": "合同原文...",
      "cross_validated": true,
      "detailed_suggestion": "修改建议...",
      "urgency": "urgent"
    }
  ],
  "cross_validation_notes": [ ... ],
  "audit_metadata": { ... }
}
```

### Markdown报告

包含风险统计表格、风险详情、原文引用、修改建议等，适合人工审阅。

## 10. 扩展开发指南

### 新增一个Skill

1. 在 `skill_config.yaml` 添加Skill配置
2. 创建 `templates/skill_xxx.j2` Prompt模板
3. 如需RAG：创建 `rag_store/xxx/` 目录并导入知识
4. 如需工具：在 `tools/registry.py` 注册工具函数
5. 在 `skill_config.yaml` 的 `contract_type_routing` 配置路由

### Skill Prompt模板规范

所有模板遵循四段式结构：
1. 不可违背硬性规则（风控约束）
2. 本次输入结构化数据
3. 本次专属RAG检索返回法条/模板上下文
4. 强制JSON输出格式（固定字段）

### 工具注册规范

```python
register_tool(
    name="my_tool",
    description="工具用途描述",
    func=my_tool_function,
    param_schema={"param1": {"type": "string", "required": True}},
    return_schema={"success": "bool", "result": "any"},
    bound_skills=["skill_law_compliance"],
)
```
