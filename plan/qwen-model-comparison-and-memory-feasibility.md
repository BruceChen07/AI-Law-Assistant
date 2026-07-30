# QWEN3.6 35B A3B vs QWEN3 14B 模型对比测试方案与 Agent 记忆机制可行性分析报告

- 分支：`feature/testing-sandbox`（Ollama provider，GPU 自动加速）
- 硬件基线：NVIDIA RTX 3060 12GB 显存 / 64GB 内存 / Windows
- 撰写日期：2026-07-29

---

## 1. 背景与现状

当前测试分支已从 llama.cpp（纯 CPU，`--n-gpu-layers 0`）切换到 Ollama（自动 GPU 卸载）。
运行配置（`app/config.json`）：

| 角色 | 模型 | 温度 | max_tokens | timeout |
|---|---|---|---|---|
| main（合同审计/税务风险） | qwen3.6:35b-a3b | 0.2 | 2048 | 600s |
| small（税务匹配/实体抽取/记忆压缩） | qwen3:4b | 0.1 | 1024 | 120s |

记忆机制现状：**双重开关关闭**（`memory_runtime_config.memory_module_enabled=false` +
运维总闸 `memory_temporary_disable.enabled=true`，原因 `edge_llm_context_limit`）。
禁用依据是 2026-07-03 在 gemma4:12b（llama.cpp CPU）上的实测：52 条款拆成多轮推理，
单轮输入 1331–1798 tokens、耗时 3.4–20.0s 持续叠加，端侧超时风险放大。
**本次切换 GPU 加速后，该禁用前提已发生实质变化，正是重新评估的契机。**

## 2. 候选模型档案（本机 Ollama 已安装）

| 属性 | qwen3.6:35b-a3b | qwen3:14b |
|---|---|---|
| 架构 | MoE（总参 36B / 激活 ~3B） | Dense 14.8B |
| 量化 | Q4_K_M | Q4_K_M |
| 磁盘/加载体积 | **23.9 GB** | **9.3 GB** |
| 最大上下文 | 262,144 | 40,960 |
| 能力标签 | vision / tools / thinking | tools / thinking |
| 12GB 显存下的部署形态 | **CPU+GPU 混合**（约一半层进显存，其余走内存） | **接近全量进显存**（9.3GB + KV cache，需控制 num_ctx） |

关键推断（需实测验证）：
- **35B A3B**：MoE 激活参数仅 ~3B，即使混合推理，解码速度也可能不慢；质量上限更高（法律推理、长条款理解）。但 23.9GB 无法全进显存，**prompt 预填充（prefill）阶段受内存带宽制约**，长输入首 token 延迟可能显著。
- **14B**：可几乎全量 GPU 推理，**吞吐和首 token 延迟预计占优**；但 dense 14.8B 的法律领域推理质量与 35B 的差距需要量化。
- 两者同时驻留不可能（显存不足），**模型切换会触发重新加载（23.9GB 约需数十秒）**，评测方案必须按"分批串行"设计，避免交替调用造成的抖动污染数据。

---

## 3. 模型对比测试方案设计

### 3.1 总体设计：三层评测金字塔

```
        L3 端到端业务质量（人工金标 + 交叉裁判）
      L2 任务级功能正确性（JSON 合规率 / 引用有效率 / 风险召回）
    L1 基础推理性能（延迟 / 吞吐 / 资源占用）
```

变量控制原则：**每轮只改 `local_llm.main_model.model` 一个变量**，small 固定为
qwen3:4b，temperature/max_tokens/提示词/测试数据完全一致，消除混杂因素。

### 3.2 测试矩阵

| 轮次 | main 模型 | 备注 |
|---|---|---|
| A | qwen3.6:35b-a3b | 当前配置（基线） |
| B | qwen3:14b | 对照组 |
| C（可选） | qwen3:14b 同时兼任 small | 验证"单模型全角色"部署形态（省去 4b 加载） |

每轮切换命令（零代码，改配置即可）：

```powershell
python bin/apply-local-llm-config.py --provider ollama --small-provider ollama `
  --main-model qwen3:14b --small-model qwen3:4b
```

切换后先做一次预热调用（触发模型加载），再正式计时。

### 3.3 L1 基础性能层

**工具**：改造 `bin/benchmark-ollama-vs-llamacpp.py`（566 行）——它已实现
avg/p95 延迟、tokens/sec、峰值内存（tasklist）、GPU 利用率/显存（nvidia-smi）采集，
只需把"Ollama vs llama.cpp 双端点"改为"同一 Ollama 端点、两个模型串行压测"
（新增 `--ollama-model-b` 参数，复用其余全部逻辑）。

**测试用例**（每类 5 轮 + 1 轮预热，取 avg/p95）：

| 场景 | 输入规模 | max_tokens | 模拟的真实负载 |
|---|---|---|---|
| 短提示 | ~200 tokens | 256 | 实体抽取、税务匹配单条 |
| 中提示 | ~2,000 tokens | 1,536 | multipass 单块条款审计（chunk_output_tokens=1536） |
| 长提示 | ~8,000 tokens | 2,048 | classic 单轮全合同审计 |
| 超长提示 | ~14,000 tokens | 2,048 | 触及 16K 预算上限的大合同 |

**指标**：首 token 延迟（prefill）、端到端延迟 avg/p95、decode tokens/sec、
`load_duration_ms`（冷启动）、显存/内存峰值、GPU 利用率。

**预期分化点**：长提示场景下 35B（混合推理）的 prefill 会明显劣于 14B（全 GPU）；
decode 速度 35B-A3B 可能反而不差。这是决定选型的关键数据。

### 3.4 L2 任务级功能正确性层

**数据集**（全部使用仓库现有资产，无需外采）：

- `data/contracts/`：7 份真实合同（员工餐补平台服务合同、返点补充协议 doc/docx、
  薪资税务代理服务合同、软件服务协议 PDF 2.1MB 等），覆盖短/中/长文与 OCR 场景
- `data/laws/`：16 份税法法规（增值税法及实施条例、企业/个人所得税法、印花税法、
  发票管理办法等），作为 RAG 证据库保持不变

**执行方式**：对每份合同分别在轮次 A/B 下调用完整审计 API
（合同审计 + 税务审计管线），每份跑 2 遍（检验稳定性）。

**指标与采集方式**：

| 指标 | 定义 | 采集来源 |
|---|---|---|
| JSON 一次成功率 | 首次输出即通过 `json_guard` 严格解析的比例 | `llm_trace.db` span + `fallback_reason=invalid_result` 计数 |
| JSON 修复率 | 需 `json_repair` 容错解析才通过的比例 | json_guard 解析元数据 |
| small→main 回退率 | `fallback_used=true` 的调用占比 | `local_llm_runtime.call_with_fallback` meta / trace |
| 引用有效率 | 报告引用命中 citation_catalog 白名单的比例 | `python bin/validate_memory_report.py`（`validate_report_citations`） |
| 风险条目产出数 | 每份合同正式风险/人工复核风险数量 | 审计报告 JSON |
| 单份合同总耗时 | 上传→报告完成 | API 计时 + trace 聚合 |
| 单份合同总 token | prompt+completion 累计 | `llm_trace.db` 聚合 |
| 每 profile 延迟分布 | contract_audit_main / tax_risk_main 等分组 p50/p95 | `llm_trace.db` |

**trace 聚合 SQL 示例**（`data/llm_trace.db` 已自动落库每次调用的
`total_latency_ms`、`prompt/completion/total_tokens`、`load_duration_ms`、
`prompt_eval_ms`、`eval_duration_ms`）：

```sql
SELECT model, task_profile,
       COUNT(*) AS calls,
       AVG(total_latency_ms) AS avg_ms,
       AVG(completion_tokens * 1000.0 / NULLIF(eval_duration_ms,0)) AS decode_tps,
       SUM(total_tokens) AS total_tokens
FROM llm_spans
WHERE created_at BETWEEN :round_start AND :round_end
GROUP BY model, task_profile;
```

**回归护栏**：每轮切换后先跑既有单测确保管线无回归：
`pytest tests/test_llm_router.py tests/test_llm_local_mode.py tests/test_json_guard.py tests/test_result_aggregator.py tests/test_audit_token_policy.py -q`
（历史基准：53 用例 38.13s 全过，见 `plan/edge-llm-cpu-local-regression-report.md`）。

### 3.5 L3 端到端业务质量层

这是唯一需要人工投入的一层，也是区分两个模型真实价值的核心：

1. **构建金标集（一次性投入）**：由法务/业务人员对 7 份合同标注"应发现的风险清单"
   （风险点、涉及条款、严重级别、依据法条），存为
   `tests/golden/contract_risks_golden.json`。建议每份合同 5–15 个金标风险点。
2. **量化指标**：
   - 风险召回率 = 命中金标风险数 / 金标风险总数（按条款位置 + 风险类型匹配）
   - 风险精确率 = 命中金标风险数 / 模型产出风险总数（度量幻觉/过度报警）
   - 严重级别一致率、法条引用准确率（引用的法条确实支持该风险）
3. **交叉裁判（辅助手段）**：对 A/B 两轮产出的差异风险条目，用第三方模型
   （本机已装的 `deepseek-r1:14b` 或云端模型）做盲评裁判，输出偏好比例，
   降低纯人工评审工作量。裁判提示词固定、双向盲评（交换 A/B 顺序各评一次）消除位置偏差。
4. **税务专项**：税务匹配主要由 small（qwen3:4b）承担，但 `non_compliant` 低置信
   条目会 force_main 复审——统计两轮中 main 复审改判率的差异，直接反映大模型
   在关键裁决点上的价值。

### 3.6 判定标准（建议）

| 维度 | 权重 | 14B 获选条件 | 35B 获选条件 |
|---|---|---|---|
| 风险召回率 | 35% | 差距 ≤ 5pp | 领先 > 5pp |
| JSON 一次成功率 | 15% | ≥ 95% | ≥ 95% |
| 单份合同总耗时 | 25% | 快 ≥ 30% | 慢 < 30% |
| 引用有效率 | 15% | 差距 ≤ 3pp | 领先 > 3pp |
| 资源余量（显存/内存） | 10% | 全 GPU 且余量可开记忆 | — |

> 直觉预期：14B 胜在速度与资源余量，35B 胜在复杂条款推理。若 35B 召回优势 < 5pp，
> 14B 的速度与"给记忆机制留出资源"的优势将更具综合价值。**以实测为准。**

---

## 4. Agent 记忆机制可行性分析

### 4.1 现有记忆架构（代码已完备，处于休眠状态）

`app/memory_system/`（8 文件）已实现完整的记忆栈，**无需新开发**：

| 组件 | 职责 |
|---|---|
| `manager.py` | 生命周期管理：ShortMemoryBuffer（短期缓冲，token_limit 默认 4000）、分轮推进、经验写回 |
| `indexer.py` | 向量索引：BGE-small-zh-v1.5 embedding + Markdown 分块（400 token/块，重叠 80） |
| `search.py` | HybridSearcher：向量 + SQLite/BM25 混合检索 |
| `rerank.py` | 多信号重排（语义相似度/法规包匹配/反馈质量/新鲜度）+ 上下文预算裁剪 |
| `experience_repo.py` | 审计经验沉淀（`save_audit_episode`） |
| `validator.py` | 报告引用白名单校验 |

运行时管线：`execute_memory_audit()`（`memory_pipeline/audit_loop.py`）
→ 每轮条款审计走 `contract_clause_audit` profile（main 角色，max_tokens 600，关 thinking）
→ 短期记忆超阈值（1400/1600 tokens）触发 `memory_flush`（small 角色，max_tokens 220）压缩
→ 审计结束经验写回 `data/memory/`（memory.db 352KB + 按日 Markdown + 向量索引）。

预算护栏已内置：`memory_max_llm_calls_per_audit=12`、
`memory_max_prompt_chars_per_clause=2400`、`memory_retrieval_top_k=3`。

### 4.2 两个模型对记忆机制的支持度评估

| 评估维度 | qwen3.6:35b-a3b | qwen3:14b | 结论 |
|---|---|---|---|
| **上下文长度** | 262K（远超需求） | 40K（约为记忆轮上限 1800 tokens 的 22 倍） | 两者均充裕；瓶颈在 Ollama 的 `num_ctx` 运行时设置（默认仅 4096，**必须显式配置**，见 4.4） |
| **多轮推理耗时** | 混合推理，单轮 1.3–1.8K tokens prefill 预计 2–6s | 全 GPU，预计 0.5–2s/轮 | 对比禁用时 gemma4 CPU 的 3.4–20s，**两者均已消除禁用主因**；14B 余量更大 |
| **12 轮累计耗时** | 预计 25–70s/合同（叠加在 classic 之外） | 预计 8–25s/合同 | 14B 显著占优 |
| **JSON 结构化输出稳定性** | 大参数量+thinking 能力，预计更稳 | 良好（Qwen3 系列 tools 能力完备） | 需 L2 实测 JSON 一次成功率验证 |
| **记忆压缩质量**（flush 由 small 承担） | 不受影响（small=4b） | 若采用"14B 全角色"（矩阵轮次 C），压缩质量↑ | 轮次 C 值得测试 |
| **资源共存性**（记忆检索 embedding + LLM 同机） | 23.9GB 已占满显存+大量内存，BGE 向量化被挤压 | 9.3GB 进显存后，**留 ~2GB 显存 + 大量内存给 embedding/检索** | **14B 与记忆机制的资源亲和性明显更好** |

### 4.3 可行性结论

**可行，且当前时点比禁用时点（2026-07-03）的条件已根本改善**：

1. 禁用主因是 CPU 端侧单轮 3.4–20s 的叠加超时——GPU 加速后单轮预计降至秒级；
2. 代码零开发量：恢复仅需翻转两个配置开关；
3. 预算护栏（12 次调用上限、token guard、prompt 字符上限）原样生效，风险可控；
4. **推荐组合：qwen3:14b（main）+ qwen3:4b（small）+ 记忆启用**——
   14B 全 GPU 的低延迟正好抵消记忆机制引入的多轮开销，总耗时可能与
   "35B + 无记忆"相当，而质量通过记忆检索补强；
5. 35B + 记忆也可行，但需接受单份合同总耗时增加 30–70s，更适合"离线批量审计"场景。

### 4.4 启用步骤（实测阶段执行）

```jsonc
// app/config.json 两处修改
"memory_runtime_config": { "memory_module_enabled": true, ... },
"memory_temporary_disable": { "enabled": false, ... }
```

同时**必须**为 Ollama 显式设置上下文窗口（否则默认 num_ctx=4096 会截断长合同）：

```jsonc
"local_llm": {
  "main_model":  { ..., "num_ctx": 16384, "keep_alive": "30m" },
  "small_model": { ..., "num_ctx": 8192,  "keep_alive": "30m" }
}
```

`num_ctx`/`keep_alive` 已由 `llm.py:_build_ollama_chat_body` 透传到 Ollama options，
无需改代码。`keep_alive=30m` 防止审计间隙模型被卸载（35B 重载需数十秒）。

> ⚠️ 注意：main 与 small 是两个不同模型，Ollama 在 12GB 显存下无法同时驻留
> 35B（混合）+ 4b。实测发现频繁 main/small 交替导致换入换出时，可考虑轮次 C
> （14B 全角色）或把 small 也换成极小模型常驻内存。

---

## 5. 技术实现路径与工作量

| 阶段 | 工作项 | 预估投入 |
|---|---|---|
| P1 | 改造 benchmark 脚本支持双 Ollama 模型对比（新增 `--ollama-model-b`） | 0.5 天 |
| P1 | 编写 `llm_trace.db` 聚合分析脚本（按轮次/模型/profile 输出 CSV） | 0.5 天 |
| P2 | L1 性能压测（轮次 A/B/C × 4 场景 × 6 轮） | 0.5 天（机器时间为主） |
| P2 | L2 全量审计跑批（7 合同 × 2 轮次 × 2 遍） | 1 天（机器时间为主） |
| P3 | 金标集标注（7 合同风险清单） | 1–2 天（业务人员） |
| P3 | L3 质量评分 + 交叉裁判 | 1 天 |
| P4 | 记忆启用 + 记忆模式下重跑 L2（选定模型） | 0.5 天 |
| P4 | 汇总报告与选型决策 | 0.5 天 |

**总计约 5–6.5 个工作日**（其中机器跑批时间可夜间执行）。

### 可能遇到的问题与对策

| 问题 | 表现 | 对策 |
|---|---|---|
| 35B 混合推理 prefill 慢 | 长合同首 token 延迟 >60s，触发前端超时感知 | timeout 已是 600s 无碍；报告中如实记录，作为选型依据 |
| 模型切换加载抖动 | 轮次交界处延迟异常 | 每轮切换后固定 1 次预热调用，预热数据不计入统计 |
| num_ctx 未设导致截断 | 长合同审计输出质量骤降、引用错乱 | 按 4.4 显式配置；用 trace 中 prompt_eval tokens 对账验证未截断 |
| 显存溢出到共享内存 | tokens/sec 断崖下跌 | nvidia-smi 采样监控；35B 轮次预留显存（关闭其他占用显存的程序，当前已有 10.7GB 被占用，**测试前需清理**） |
| JSON 输出漂移 | 14B 在复杂 schema 下修复率升高 | json_guard + fallback_on_invalid_json 已兜底；把修复率纳入 L2 指标即可量化 |
| 金标主观性 | 召回率结论被质疑 | 双人独立标注 + 分歧仲裁；保留标注过程记录 |

---

## 6. 性能预期与资源消耗评估（待实测校准）

| 项目 | 35B A3B（混合） | 14B（全 GPU） |
|---|---|---|
| 显存占用 | ~11.5GB（打满）+ ~13GB 内存 | ~10GB（含 KV cache @16K ctx） |
| 内存占用 | 高（模型一半 + KV 部分在内存） | 低（<2GB 辅助） |
| decode 速度预期 | 15–35 tok/s（A3B 激活小） | 25–45 tok/s |
| 长提示 prefill 预期 | 慢（内存带宽瓶颈） | 快（GPU prefill） |
| 单份合同 classic 审计 | 预计 60–180s | 预计 30–90s |
| 加记忆模式增量 | +25–70s | +8–25s |
| 冷启动加载 | 30–90s（23.9GB） | 10–25s（9.3GB） |

> 以上为基于架构与硬件的工程估算，L1 层实测数据出来后以实测为准并回填本表。

---

## 7. 实施建议与风险提示

### 建议

1. **先跑 L1/L2 再决定是否投入金标标注**——若某模型在 JSON 合规率或耗时上出现
   一票否决级差距（如 JSON 一次成功率 <85%），可提前收敛结论，节省 L3 投入。
2. **把轮次 C（14B 全角色）纳入正式矩阵**——单模型常驻消除 main/small 换入换出，
   在 12GB 显存下可能是综合最优部署形态。
3. **记忆机制建议在选型收敛后、用获选模型单独开一轮 P4 验证**，避免双变量
   （模型 × 记忆）同时变化导致归因困难。
4. 所有评测均在 `feature/testing-sandbox` 分支进行，config.json 的每轮快照
   随评测报告一起归档（`plan/local-llm-llamacpp/reports/`），保证可复现。
5. 测试期间保持 `llm_trace_full_enabled=true`（已开启），评测结束后评估 trace
   数据量再决定是否回收。

### 风险提示

- **显存挤兑（高）**：当前 GPU 已有 10.7GB 被占用，测试前必须释放，否则 35B 会
  大量溢出到共享内存，性能数据失真。
- **单一样本偏差（中）**：7 份合同集中于服务/税务类，缺少工程、租赁类合同，
  结论外推到全合同类型时需谨慎；有条件可补充 2–3 份其他类型脱敏合同。
- **记忆写回污染（中）**：P4 记忆验证会向 `data/memory/` 写入经验数据，
  建议测试前备份 `data/memory/`，或为测试配置独立 `memory_dir`。
- **Ollama 版本敏感（低）**：MoE 模型的 GPU 卸载策略随 Ollama 版本变化较大，
  报告中需记录 `ollama --version` 以保证可复现。
- **thinking 模式变量（低）**：两模型均支持 thinking，当前管线统一
  `enable_thinking=false`。若后续想测 thinking 增益，应作为独立轮次，
  不与本方案混测。
