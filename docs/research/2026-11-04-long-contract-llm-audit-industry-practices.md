# 长合同条款级 LLM 审计：业界方案、案例与经验教训调研

- 调研日期：2026-11-04
- 调研目的：为"571 条条款的 docx 合同在本地 Qwen3.6-35B-A3B + llama.cpp 上完成条款级财税审计"这一场景，收集可复用的业界方案、已验证的工程参数与失败教训
- 调研方式：公开网络检索（官方文档、论文、厂商技术博客、第三方测评），共采集 23 个信息源
- 可信度分级：**A** = 一手论文/官方文档；**B** = 厂商官方博客（含产品宣传成分）；**C** = 第三方测评/媒体转述
- 说明：本文所有外部结论均带来源编号；本项目自身的数字来自本地实测（见第 5 节），两者严格区分

---

## 0. 执行摘要

1. **"固定条数 × 固定轮数"作为容量上限，是业界已明确的错误做法**。合同审计应按 token 预算 + 条款边界切分，而不是按"每组 8 条、最多 20 轮"这类静态配额 [S2][S8][S21]。
2. **条款级切分（clause-aware segmentation）是合同审阅与普通 RAG 的分水岭**。固定字符切分会把一条 indemnification 条款切成三段，导致风险判断失真 [S8]；业内基准（CUAD 的 41 类条款）本身就是按条款粒度定义的 [S1]。
3. **覆盖率 100% 只证明"看过"，不证明"看对"**。ContractEval 等基准显示，开源模型在条款存在时仍回答"无相关条款"的比例可超过 30% [S2][S23]；把 `coverage=1.0` 当成质量达标是典型误判。
4. **长上下文不是免费的**。Chroma 对 18 个前沿模型的实测显示，输入越长性能越不稳定，且所有模型都退化 [S10]；"Lost in the Middle" 证明相关信息在中部时性能显著下降 [S11]。因此"把 571 条全文一次塞进去"不是解法。
5. **结构化输出应从"事后解析+重试"升级为"解码期约束"**。OpenAI 用 JSON Schema 严格模式做到 100% 模式符合 [S14]；llama.cpp 原生支持 `json_schema` 语法采样 [S15]。这是当前"输出 1536 token 就截断、解析失败就重试"问题的最低成本解法。
6. **重复前缀必须命中 KV 缓存，否则纯浪费**。llama.cpp 默认开启 prompt caching [S15]，vLLM 的自动前缀缓存可做到"几乎零开销且不改变输出" [S17]。本项目 25 个 chunk 的 `prefix_hash` 完全相同，却每轮重复 5,286 token 的条款索引。
7. **法律 AI 的准确率宣传不能直接迁移**。斯坦福预注册研究发现 Lexis+ AI 与 Westlaw AI 的幻觉率分别约 17% 与 33–34% [S19]；LawGeex 的 94% 只适用于 NDA 这一窄场景 [S22]。任何"94%/97% 准确率"都必须绑定数据集与条款类型。
8. **思考模式在本任务上未必更好**。ContractEval 发现 thinking 模式提升"输出有效性"，但降低"正确性" [S2]；本项目已关闭 thinking，方向正确。
9. **同步长请求 + 90 分钟前端超时，在 CPU 推理下必然踩线**。业内做法是异步任务 + 进度持久化 + 断点续跑 [S8][S9]。
10. **评测必须自建业务黄金集**。CUAD/ContractNLI 只能验证"条款识别能力"，不能验证"中国财税风险判断"；RAGAS 的 faithfulness/context recall 可作为线上持续监控指标 [S20]。

---

## 1. 场景与业界问题映射

| 本项目的问题表述 | 对应的业界术语 | 主流解法 |
|---|---|---|
| 571 条条款超出 160 条计划容量 | 静态配额 vs 预算驱动规划 | token 预算切分 + 自适应分组 [S8][S21] |
| 每一非空行切成一条条款 | 固定/启发式切分 | 结构化解析 + 条款边界识别 [S7][S8] |
| 每个 chunk 重复 5,286 token 索引 | 重复前缀预填 | prefix / KV cache 复用 [S15][S17] |
| 输出 1536 token 被截断、解析失败重试 | 结构化输出可靠性 | 约束解码 + 二次校验 [S14][S15][S16] |
| 覆盖 100% 但不确定审得对不对 | 覆盖性 vs 有效性 | 覆盖清单 + 黄金集召回评测 [S2][S20] |
| 25 个 chunk 串行跑 100+ 分钟 | 长任务编排 | 异步化、并发槽位、分阶段进度 [S8][S9] |
| 报告引用法条可能错配 | 引用归因/幻觉 | 白名单 + 精确匹配 + 引用先行 [S13][S19] |

---

## 2. 方案全景

### 2.1 文档解析与条款切分

**业界共识：先解析结构，再按条款切分，最后才谈 token 长度。**

- 腾讯云文档解析实践强调：先把 PDF/Word 解析为保留标题层级、表格、段落关系的结构化 Markdown，再驱动大模型做"条款审阅 + 规范审阅"两类任务 [S7]。这对应本项目 `split_contract_clauses` 的"每个非空行 = 一条条款"——属于**只有切分、没有解析**。
- 合同审阅 RAG 的工程文章明确指出：固定字符切分是"代价最大的偷懒默认值"，500 字符窗口会把一条赔偿条款切成三段；应当走"解析元素 → 按章节层级识别条款边界 → 每条条款输出一个带类型的对象" [S8]。
- 业内基准的粒度也是条款级：CUAD 收录 510 份商业合同、13,000+ 标注、41 类条款 [S1]；ContractEval 直接复用 CUAD 测试集评估 41 类条款级法律风险 [S2]。
- 条款合并/重叠存在争议：常见建议是 10–20% overlap [S21]，但也有 2026 年的预印本与 Chroma 的测试认为重叠收益很小甚至为负（第三方转述 [C]）。对本项目的启示是：**不要靠 overlap 解决漏审，而要靠覆盖清单**。

**对本项目的映射**：571 条中平均每条仅约 69 token（启发式估算），说明其中大量是表格碎片和换行残句。业界做法对应"把无编号短行合并回所属条款"，并把"条款边界"作为切分主键，而不是行号。

### 2.2 长文档审计的编排模式

业界可归纳为三类，且常组合使用：

| 模式 | 做法 | 优点 | 代价 | 典型来源 |
|---|---|---|---|---|
| Map-Reduce | 每块独立抽取，再一次 reduce 汇总去重 | 可并行、覆盖完整、可审计 | 调用次数多、跨块冲突需二次裁决 | [S8][S9][S21] |
| Refine / 迭代 | 逐块处理并不断更新全局结论 | 跨块一致性较好 | 严格串行、错误会累积 | 通用实践 |
| Hierarchical | 条款 → 章节 → 全文三级摘要/结论 | 兼顾局部与全局 | 需要可靠的层级结构 | [S8][S9] |

合同场景的推荐组合是：**按条款/章节 map → 按风险主题 reduce → 全局一致性复核**。MIT CTL 的本地 LLM 合同抽取项目也是"BM25 + 稠密检索召回，再用 cross-encoder 重排提精度"的两段式结构 [S9]，说明"召回求全、重排求准"是行业通用分层。

**对本项目的映射**：现有 v2 的 chunk → 合并流程属于 Map-Reduce，方向正确；缺的是 reduce 阶段的"跨块冲突裁决"和"缺失条款检测"。

### 2.3 上下文预算与"上下文腐化"

- **Lost in the Middle**（TACL）：相关信息位于长上下文中部时，模型表现显著下降，呈 U 型曲线，即使模型宣称支持长上下文 [S11]。
- **Context Rot**（Chroma，2025-07-14）：评测 18 个前沿模型（含 GPT-4.1、Claude 4、Gemini 2.5、Qwen3），结论是**所有模型都随输入变长而退化**，且退化不均匀；NIAH（大海捞针）分数高不代表真实任务可靠 [S10]。
- Anthropic 的长上下文提示建议：**长文档放前面、问题/指令放后面**（测试中可提升约 30%），用 XML 标签组织结构，并要求"先逐字引用原文，再作答" [S13]。
- Anthropic Contextual Retrieval：给每个 chunk 前置 50–100 token 的上下文说明后再索引，检索失败率降低 49%，叠加 rerank 降低 67% [S12]。注意这是**检索阶段**的优化，不是"把全文索引塞进生成 prompt"。

**对本项目的映射**：当前 v2 每轮 prompt = 5,286 token 全局条款索引 + 约 1,600 token 本轮条款。索引既拉长了输入（context rot 风险），又抬高了预填成本，而且它不是本轮判断所必需的信息。更接近业界做法的是：**本轮条款在前、任务与证据在后、只给必要的章节级上下文**。

### 2.4 结构化输出与约束解码

- OpenAI Structured Outputs：`response_format` 使用 `json_schema` + `strict: true` 时，在官方评测中达到 100% 模式符合（以 gpt-4o-2024-08-06 为例）[S14]。
- llama.cpp：服务端支持 grammar / `json_schema` 参数做"语法采样"，使模型在 token 级别无法输出违反 schema 的内容 [S15]；GBNF 语法文档见 [S16]。工程实践普遍建议"约束解码 + Pydantic/JSON 二次校验"双闸门 [S16]。
- 注意一个坑：llama.cpp 的 schema 转换器会**静默跳过不支持的关键字**，schema 能加载但不一定按预期约束，需要用测试校验生成的语法（第三方总结 [C]）。

**对本项目的映射**：当前 [llm.py](file:///d:/Workspace/AI-Law-Assistant/app/core/llm.py#L280-L328) 只传 `temperature` 和 `max_tokens`，没有任何 `response_format`。这是"解析失败 → 重试 → 整单失败"链路的根源。加上 `json_schema` 后，`classic_parse_failed` 与截断导致的无效输出会大幅下降。

### 2.5 本地/私有化推理性能

- llama.cpp：
  - prompt caching **默认开启**（`--cache-prompt`），`--cache-reuse N` 默认 0（关闭），需要 KV shifting 才能复用非前缀部分 [S15]。
  - 前缀复用依赖**精确字符串前缀匹配**——任何动态变化的前缀（时间戳、随机 header）都会导致 cache miss（第三方实践总结 [C]）。
  - KV cache 可量化：`q8_0` 通常被视为"接近无损"的内存/质量折中（第三方测试给出逐位一致的样例，样本较小 [C]）。
  - MoE 模型可用 `--n-cpu-moe` 把部分专家留在 CPU，其余放 GPU；纯 CPU 场景无意义 [C]。
- vLLM：自动前缀缓存按 block 哈希复用 KV，"几乎无额外开销且不改变模型输出" [S17]；这也是"同一长文档多次提问"场景的标准优化。
- ContractEval 的实测结论：量化能显著提速，但会带来性能下降 [S2]——即"省时间"和"保准确"需要显式取舍。

**对本项目的映射**：25 个 chunk 的 `prefix_hash` 完全一致（实测 `unique_prefix_hashes=1`），理论上可获得接近"只预填本轮条款"的效果；若实测未命中，说明请求侧或服务侧破坏前缀，需要按 [S15] 检查 `cache_prompt` 与动态内容。

### 2.6 评测与回归

- 公开基准：CUAD（条款识别）[S1]、ContractEval（条款级风险识别，19 个模型、41 类风险，含 F1、Jaccard、"懒惰率"三项指标）[S2]。
- RAG 质量指标：RAGAS 的 faithfulness（答案是否都能由上下文支持）、context recall（参考答案要点是否被召回）适合做**线上持续监控**，其中 faithfulness 不需要参考答案即可跑 [S20]。
- 人工复核仍是法律场景的默认配置：LexCheck 2024 基准称常见商业合同条款识别 94–97%，但仍建议高风险合同保留人工终审（第三方 [C]）。
- 斯坦福预注册研究：法律 RAG 工具幻觉率仍达 17%–34%，说明"有引用"不等于"引用正确" [S19]。

**对本项目的映射**：应建立"业务黄金集"（例如 20–50 份已人工标注的合同，覆盖 41 类税务/发票风险），把"条款覆盖率、风险召回率、引用正确率、误报率、单份合同耗时/成本"作为发布门槛；RAGAS 的 faithfulness 可作为每次 prompt 变更后的回归指标。

---

## 3. 案例库

| 主体 | 公开做法（仅限可核实内容） | 可借鉴点 | 来源 | 可信度 |
|---|---|---|---|---|
| OpenAI | Structured Outputs：`json_schema` + `strict`，官方评测 100% 模式符合 | 把"格式正确"从概率问题变成约束问题 | [S14] | A |
| Anthropic | Contextual Retrieval：chunk 前置上下文，检索失败率 -49%，加 rerank -67% | 上下文要"按块注入"，不要"全量重复" | [S12] | A |
| Anthropic | 长上下文提示：长文档在前、问题在后、引用先行 | prompt 结构本身影响召回 | [S13] | A |
| Chroma | Context Rot：18 个模型全部随输入变长退化 | 不能把"塞更多上下文"当质量优化 | [S10] | A |
| vLLM | 自动前缀缓存：block 哈希复用 KV，不改变输出 | 同文档多轮请求的标准优化 | [S17] | A |
| llama.cpp | 语法采样（GBNF / json_schema）、prompt caching 默认开启 | 本地部署也能做到约束输出与缓存复用 | [S15][S16] | A |
| Qwen | Qwen3.6-35B-A3B：35B 总参 / 3B 激活，40 层，256 专家（8 路由 + 1 共享），原生 262,144 上下文；非思考模式推荐 `temperature=0.7, top_p=0.80, top_k=20, min_p=0, presence_penalty=1.5, repetition_penalty=1.0` | 官方采样建议与任务确定性需求需做 A/B；上下文上限远高于当前 16k | [S18] | A |
| Harvey | 合同智能三件事：抽取、分类、分析（官方博客） | 产品分层：先结构化，再判断 | [S3] | B |
| Luminance | 以法律文档监督学习为核心，擅长大批量尽调中的非标准条款识别；2023 年起叠加生成层 LUMI | 大批量场景重"横向一致性"，不重单份深度 | [S4] | C |
| Kira (Litera) | 按"条款类型"训练模型（控制权变更、赔偿、责任上限、IP 归属），支持批量抽取 | 条款类型化 = 规则与模型的结合点 | [S4] | C |
| 幂律智能 MeCheck | 法律知识图谱 + 300+ 预置审查点 + 自定义审查规则 + "越用越准"的闭环调优 | 审查点/规则库是产品护城河；闭环反馈可落地 | [S5][S6] | B |
| 得理科技 / 通义法睿 | 支持自定义审查规则、逐条审查、审查立场选择、一键修订 | "逐条审查 + 规则可配置"是国内主流交互范式 | [S6] | C |
| 腾讯云 TextIn + Dify | 先解析为保留层级/表格的结构化 Markdown，再分支执行"条款审阅 + 规范审阅" | 解析质量决定上限 | [S7] | B |
| LawGeex（历史基准） | NDA 审阅 94% 准确率 vs 律师 85%（F1，窄场景） | 基准结论必须绑定场景，不可外推 | [S22] | C |
| Stanford RegLab | Lexis+ AI 约 17%、Westlaw AI 约 33–34% 幻觉率（预注册研究） | "有引用"≠"引用正确"，必须做引用校验 | [S19] | A |
| CUAD / ContractEval | 41 类条款/风险，19 个模型横向评测；开源模型"无相关条款"误答偏多；thinking 提升输出有效性但降低正确性；量化提速但掉点 | 评测维度应包含"懒惰率"与"漏报"，不只看 F1 | [S1][S2][S23] | A/C |

---

## 4. 典型经验教训（反模式清单）

| # | 反模式 | 后果 | 证据 | 正确做法 |
|---|---|---|---|---|
| L1 | 用**固定条数/固定轮数**当容量上限 | 与真实 token 脱节，长合同直接失败或低效 | [S2][S8]；本项目 160 条 vs 真实需 25 chunks | 按 token 预算反推条数，容量由预算决定 |
| L2 | 用**字符启发式估算 token** 做预算 | 中文场景偏差可达数倍，计划失真 | 本项目实测偏差约 4 倍 | 用目标模型 tokenizer 精确计数（v2 已做） |
| L3 | 按**行/固定长度**切条款 | 条款被截断，语义与定位都失真 | [S8][S7] | 结构化解析 + 条款边界识别 |
| L4 | 每个 chunk 重复**全量索引/全文** | 预填成本线性膨胀 + context rot | [S10][S11]；本项目 5,286 token × 25 | 前缀缓存 + 章节级必要上下文 |
| L5 | 输出预算与输入规模**不匹配** | 截断 → 重试 → 整单失败 | 本项目 23 条/轮仅 1536 token | 按"条款数 × 风险密度"估算输出，或拆分更细 |
| L6 | 只开 **JSON mode**，不做 schema 约束 | 语法合法但字段缺失/枚举非法 | [S14] | `json_schema` 约束解码 + 二次校验 |
| L7 | 只校验格式，不校验**语义一致性** | 法条标题/条号与证据不匹配 | [S19]；本项目 strict validation 是对的方向 | citation 白名单 + 字段精确匹配 |
| L8 | 把**覆盖率 100%** 当质量达标 | 看过但没看对，漏报被掩盖 | [S2][S23] | 覆盖率 + 黄金集召回率 + 懒惰率三件套 |
| L9 | **静默降级**：部分结果当成功返回 | 合规风险，事后无法追溯 | 本项目 classic 路径历史行为 | 显式 `coverage_incomplete` 失败或标记 |
| L10 | **同步长请求 + 短超时** | 用户侧超时，成本已发生 | 本项目 90 分钟 vs 预计 100+ 分钟 | 异步任务 + 进度持久化 + 断点续跑 [S8][S9] |
| L11 | 依赖**厂商宣称准确率** | 场景迁移失败 | [S19][S22] | 自建业务黄金集 + 发布门槛 |
| L12 | 默认开**思考模式** | 延迟翻倍，且正确性未必提升 | [S2]；CPU 实测 8.7 tok/s | 抽取类任务关闭 thinking，按基准决定 |

---

## 5. 与本项目现状的差值（本地实测，非同源外部数据）

| 指标 | 本项目实测 | 业界参考做法 | 差值含义 |
|---|---|---|---|
| 571 条真实合同 v2 规划 | 58 units → **25 chunks**，`coverage_planned=1.0` | 按 token 预算 + 条款边界切分 [S8] | 可审，但 chunk 数偏多 |
| 单 chunk 输入 | **6,991 token**，其中 5,286 是全局条款索引 | 上下文按块注入 [S12][S13] | 索引占 76%，属浪费 |
| 输入合计 | **166,451 token** | 前缀缓存可复用 [S15][S17] | 未确认命中前等于全量重算 |
| 单 chunk 输出预算 | **1,536 token**，平均 22.8 条/轮 | 输出按风险密度估算 | 截断风险高 |
| CPU 吞吐 | 预填 **58.5 tok/s**，生成 **8.7 tok/s** | 量化/KV 量化/线程调优 [S2][S15] | 25 chunk 预计 100–120 分钟 |
| 前端超时 | **90 分钟** | 异步化 [S8][S9] | 必然踩线 |
| 模型上下文 | 服务端 **16,384**，模型原生 **262,144** [S18] | 依 context rot 选"够用且不退化"的窗口 [S10] | 有扩窗空间，但不应盲目扩 |

**关于 KV 内存的推导**（仅估算）：该模型 40 层中只有 10 层是 Gated Attention（16 Q / 2 KV heads，head_dim 256），其余为线性注意力 [S18]。按 10 层 × 2 KV heads × 256 × 2（K/V）× 2 字节估算，约 **20 KB/token**：16k≈320 MB、32k≈640 MB、64k≈1.25 GB、128k≈2.5 GB。因此"扩到 32k–64k"在内存上可行，但**是否值得扩取决于 context rot 实测**，不能只按内存判断。

---

## 6. 针对本项目的方案映射与优先级

| 优先级 | 动作 | 业界依据 | 预期收益 | 风险 |
|---|---|---|---|---|
| P0 | 给审计请求加 `json_schema` 约束（llama.cpp `response_format`） | [S14][S15][S16] | 解析失败/截断显著下降，重试成本下降 | schema 关键字可能被静默忽略，需回归 |
| P0 | 验证 prefix cache 是否命中；不命中则修正请求前缀 | [S15][S17] | 预填可降至接近"仅本轮条款" | 需看 `cache_n`/`prompt_n` 实测 |
| P0 | `input_target_tokens` 7000→10000–11000，`output_tokens` 1536→2048，`max_tokens`→2560 | [S8][S21] + 本地实测 | chunks 25→16–17，截断减少 | 单请求预填变长，需盯 context rot |
| P1 | 压缩"全局条款索引"为章节级/本轮缺失清单 | [S12][S13] | 预填总量可从 166k 降到 40–60k | 可能削弱"未约定"判断，需黄金集验证 |
| P1 | 条款切分治理：合并无编号短行 | [S1][S7][S8] | 条款数下降，语义更完整 | 影响定位粒度，需回归 |
| P1 | 审计异步化 + 进度持久化 + 断点续跑 | [S8][S9] | 消除 90 分钟超时 | 改造量中等 |
| P1 | 把 classic 的税务 checklist 移植进 v2 prompt | [S5][S6]（规则库价值） | 税务专项召回回升 | prompt 变长，需 A/B |
| P2 | 建立 20–50 份业务黄金集，指标含召回/误报/引用正确/耗时 | [S1][S2][S20] | 让每次调参可判定 | 标注成本 |
| P2 | 采样参数按官方建议做网格：`temperature 0.2/0.7`、`top_p 0.8/0.95`、`presence 0/1.5` | [S18] | 找到任务最优点 | presence_penalty 对重复字段名可能有害，需实测 |
| P2 | llama-server：`--threads 6`、`--threads-batch 6`、`--ubatch-size 1024`，必要时 `-ctk/-ctv q8_0` | [S15] + 本地硬件 | 预填提速，内存换窗口 | 收益需实测，量化可能掉点 [S2] |

**不建议做的事**（有明确反证）：

1. 把 `max_rounds` 直接提到 72 硬跑完 571 条——调用次数与 token 利用率都最差（本项目早期分析已量化）。
2. 把全文/全量索引塞进每个 chunk 来"保证上下文"——context rot 与预填成本双输 [S10][S11]。
3. 用扩大上下文替代检索/切分设计——模型能力上限不等于任务可靠性 [S10]。
4. 只以 F1 或"覆盖率"作为验收标准——必须同时看漏报率和懒惰率 [S2][S23]。

---

## 7. 落地路线与验收指标

### 阶段划分

| 阶段 | 内容 | 完成标志 |
|---|---|---|
| P0（止血，配置+小改） | JSON schema、prefix cache 验证、`input_target/output` 调整、超时放大或异步开关 | 571 条可在超时内跑完，`coverage_audited=1.0`，`parse_failed=0` |
| P1（质量） | 索引压缩、切分治理、checklist 移植、异步化 | 黄金集召回率不低于 classic 基线，误报率不升 |
| P2（长期） | 黄金集常态化回归、参数网格、KV/线程调优、成本模型 | 每次 prompt/参数变更都有可对比报告 |

### 建议验收指标（每个都要有基线值）

| 指标 | 定义 | 目标（建议） |
|---|---|---|
| 条款覆盖率 | `coverage_audited` | = 1.0 |
| 解析失败率 | `classic_parse_failed` chunks / total | < 1% |
| 截断率 | `truncated_chunks` / total | < 2% |
| 引用拒绝率 | `rejected_risk_count` / 生成风险数 | 监控，异常升高说明 schema/字段约束过严 |
| 黄金集召回率 | 人工标注风险被检出比例 | ≥ classic 基线 |
| 误报率 | 人工判定不成立的风险占比 | 不高于基线 |
| P50/P95 单份合同耗时 | 端到端 | 满足业务 SLA（当前 CPU 需异步化） |
| 单位条款成本 | 总 token / 条款数 | 逐版本下降 |

---

## 8. 参考来源

| 编号 | 标题 | 类型 | 链接 | 访问日期 |
|---|---|---|---|---|
| S1 | CUAD Dataset — The Atticus Project | 官方数据集 | https://www.atticusprojectai.org/cuad/ | 2026-11-04 |
| S2 | ContractEval: Benchmarking LLMs for Clause-Level Legal Risk Identification | 论文（arXiv 2508.03080） | https://arxiv.org/pdf/2508.03080v1 | 2026-11-04 |
| S3 | What is Contract Intelligence and How Does it Work? — Harvey | 厂商博客 | https://www.harvey.ai/blog/contract-intelligence | 2026-11-04 |
| S4 | Contract Review AI Tools Compared: Luminance, Kira, Spellbook | 第三方测评 | https://legalaiinsights.com/tool-evaluations/contract-review-ai-tools-comparison-luminance-kira-spellbook | 2026-11-04 |
| S5 | MeCheck 幂律智能合同审查系统 | 厂商官网 | https://mecheck.net.cn/index | 2026-11-04 |
| S6 | LEGAL AI · 合同与法务（国内合同审查工具综述） | 开发者社区 | https://cloud.tencent.com.cn/developer/article/2772446 | 2026-11-04 |
| S7 | 合同审核的上半场是解析：腾讯云多模态文档解析实践 | 厂商技术博客 | https://cloud.tencent.cn/developer/article/2760240 | 2026-11-04 |
| S8 | How to Build (and Evaluate) a Contract Review RAG Agent in 2026 | 工程博客 | https://futureagi.com/blog/contract-review-rag-build-evaluate-2026/ | 2026-11-04 |
| S9 | From Unstructured PDF Files to Contract Intelligence: A Local LLM RAG Pipeline（MIT CTL） | 课程/项目报告 | https://ctl.mit.edu/sites/default/files/documents/from-unstructured-pdf-files-to-contract-intelligence-capstone.pdf | 2026-11-04 |
| S10 | Context Rot: How Increasing Input Tokens Impacts LLM Performance — Chroma | 技术报告 | https://www.trychroma.com/research/context-rot | 2026-11-04 |
| S11 | Lost in the Middle: How Language Models Use Long Contexts（TACL） | 论文 | https://www.mitpressjournals.org/tacl/article/119630/Lost-in-the-Middle-How-Language-Models-Use-Long | 2026-11-04 |
| S12 | Introducing Contextual Retrieval — Anthropic | 官方博客 | https://www.anthropic.com/news/contextual-retrieval | 2026-11-04 |
| S13 | Prompting best practices — Claude Platform Docs | 官方文档 | https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices | 2026-11-04 |
| S14 | Introducing Structured Outputs in the API — OpenAI | 官方博客 | https://openai.com/index/introducing-structured-outputs-in-the-api/ | 2026-11-04 |
| S15 | llama.cpp server README（cache-prompt / cache-reuse / json_schema） | 官方仓库文档 | https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md | 2026-11-04 |
| S16 | llama.cpp grammars README（GBNF） | 官方仓库文档 | https://raw.githubusercontent.com/ggml-org/llama.cpp/master/grammars/README.md | 2026-11-04 |
| S17 | Automatic Prefix Caching — vLLM Docs | 官方文档 | https://docs.vllm.ai/en/stable/design/prefix_caching/ | 2026-11-04 |
| S18 | Qwen3.6-35B-A3B 模型卡 | 官方模型卡 | https://www.modelscope.ai/models/Qwen/Qwen3.6-35B-A3B | 2026-11-04 |
| S19 | Hallucination-Free? Assessing the Reliability of Leading AI Legal Research Tools（Stanford RegLab） | 论文 | https://www.law.stanford.edu/wp-content/uploads/2024/05/Legal_RAG_Hallucinations.pdf | 2026-11-04 |
| S20 | RAGAS Metrics（faithfulness / context recall 等） | 官方文档 | https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/nvidia_metrics/ | 2026-11-04 |
| S21 | Best Chunking Strategies for RAG (and LLMs) in 2026 | 工程博客 | https://www.firecrawl.dev/blog/best-chunking-strategies-rag | 2026-11-04 |
| S22 | What AI Contract Analysis Benchmarks Reveal About Accuracy Risks（含 LawGeex NDA 基准转述） | 第三方测评 | https://legalaiinsights.com/tool-evaluations/ai-contract-analysis-accuracy-risks | 2026-11-04 |
| S23 | AI Contract Clause Extraction Benchmarks in 2026 | 第三方测评 | https://legalaiinsights.com/tool-evaluations/ai-contract-clause-extraction-benchmarks | 2026-11-04 |

---

## 9. 调研局限

1. **厂商能力描述不可验证**。Harvey、Luminance、Kira、MeCheck 的公开材料以产品价值为主，缺少可复现的技术细节；本文仅将其作为"产品形态与交互范式"参考，不作为性能证据。
2. **部分数据来自第三方转述**（标 C）。例如 ContractEval 中"部分 Qwen3 模型误答率 >30%"、KV 量化"逐位一致"等，均非一手实验复现。
3. **公开基准与业务场景存在分布差异**。CUAD/ContractEval 是英文商业合同、41 类通用条款；本项目是中文合同、税务与发票风险，不能直接套用其分数。
4. **本地实测只覆盖规划与吞吐**。166k 预填、25 chunks、58.5/8.7 tok/s 均为本机实测；真实审计的端到端耗时还受检索、重试率、风险密度影响，需在 P0 落地后用真实模型跑一次全链路回归。
5. **未做多用户并发评估**。纯 CPU、`n_slots=1` 的条件下，并发会直接排队，本文未量化多用户场景。
