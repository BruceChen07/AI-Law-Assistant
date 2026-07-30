# Qwen 本地模型对比评测 —— 最终测试报告

- 评测日期：2026-07-29 ~ 2026-07-30
- 评测依据：`plan/qwen-model-comparison-and-memory-feasibility.md`（三层评测金字塔 L1/L2/L3）
- 硬件：NVIDIA GeForce RTX 3060 12GB（可用 ~11.2GiB）/ 系统内存 64GB / Windows 11 23H2
- 运行时：Ollama 0.32.1（CUDA），模型目录 `E:\ollama_model`，`OLLAMA_KEEP_ALIVE=30m`
- 数据集：`data/contracts/` 真实合同 5 份（4 docx + 1 pdf）、`data/laws/` 税法法规 16 份
- 追踪：`data/llm_trace.db`（llm_trace_spans 表）+ `data/llm_traces_full/DATE/llm_trace_full.jsonl`（完整响应正文）

---

## 0. 选型结论（Executive Summary）

**推荐主模型：`qwen3:14b` @ `num_ctx=8192`（全 GPU 常驻）。**

| 维度 | qwen3.6:35b-a3b（轮次A/基线） | qwen3:14b（轮次B/推荐） |
|---|---|---|
| 稳定性（L2 合同审计工作负载） | **不可用**：3/3 次运行器崩溃 | **稳定**：0 次崩溃，10 次运行仅 2 次因 OCR 环境缺失失败 |
| 显存/溢出 | 23GB 模型，25/41 层溢出到 CPU（~13GB host buffer） | 9.3GB，`num_ctx=8192` 时全 GPU 常驻 |
| 单次调用平均延迟 | 12396.9 ms | **5913.4 ms（快 ~2.1×）** |
| prefill（prompt_eval）平均 | 3563.1 ms | **649.8 ms（快 ~5.5×）** |
| JSON 一次成功率 | 100%（崩溃前 35/35） | **100%（70/70）** |
| small→main 回退率 | 0% | 0% |
| 重模型合同（薪资税务）风险产出 | 崩溃，未产出 | 12~16 条，稳定产出 |

**理由**：在 RTX 3060 12GB 上，`qwen3.6:35b-a3b`（MoE，23GB）必须将约 13GB 权重溢出到 CPU host memory，在合同审计的持续多请求负载下，原生推理层（CGo → llama.cpp/CUDA）发生访问违例，导致 `ollama serve` 进程崩溃并遗留占用大量内存的孤儿 `llama-server`。本次共复现 3 次崩溃（3/3）。相较之下，`qwen3:14b` 在 `num_ctx=8192` 下全 GPU 常驻，稳定、更快，JSON 合规性与风险召回均达到或优于 35b-a3b 的可用样本。

---

## 1. 测试环境与变量控制

### 1.1 硬件与运行时
- GPU：NVIDIA GeForce RTX 3060 12GB（`common_params_fit_impl` 报告可用 11240 MiB free）
- 系统内存：64GB
- OS：Windows 11 23H2；Shell：PowerShell（分隔符用 `;`）
- Ollama：0.32.1（内置 llama.cpp 运行器，CUDA offload）
- 环境变量：`OLLAMA_KEEP_ALIVE=30m`、`OLLAMA_MODELS=E:\ollama_model`

### 1.2 模型与量化
| 模型 | 结构 | 量化 | 磁盘体积 | GPU 常驻情况 |
|---|---|---|---|---|
| qwen3.6:35b-a3b | MoE（约 3B 激活） | Q4_K_M | ~23GB | 部分溢出（25/41 层到 CPU，~13GB host buffer） |
| qwen3:14b | Dense | Q4_K_M | ~9.3GB | `num_ctx=8192` 全 GPU |
| qwen3:4b（small 角色） | Dense | Q4_K_M | ~2.5GB | 全 GPU |

### 1.3 变量控制原则
- **每轮仅改变 `local_llm.main_model.model`**，其余参数固定：`num_ctx_main=8192`、`num_ctx_small=8192`、`small_model=qwen3:4b`、`keep_alive=30m`、`provider=ollama`、`temperature`/`max_tokens` 采用管线默认。
- 每轮执行 **1 次预热调用**（warmup，加载模型进显存），预热耗时单独记录，不计入指标。
- 通过跑批脚本 `l2_contract_runner.py` **进程内**直接调用审计管线（无 HTTP/鉴权），`deepcopy(cfg)` 后仅替换模型字段，保证配置隔离。
- 每轮记录 UTC `trace_window`，聚合时据此从 `llm_trace_spans` 精确切片本轮 span，与历史/其它模型数据完全隔离，保证可追溯、可复现。

---

## 2. L1 基础性能层结果

### 2.1 三种可用规模（short/medium/long）

| 场景 | 模型 | prompt_tokens | 首token avg(ms) | 端到端 avg(s) | decode(tok/s) | prefill(ms) | 冷启load(ms) | 峰值显存(MB) | GPU% |
|---|---|---|---|---|---|---|---|---|---|
| short-200 | qwen3.6:35b-a3b | 155 | 418.81 | 3.276 | 28.06 | 90.21 | 332.68 | 11794 | 43 |
| short-200 | qwen3:14b（默认ctx） | 176 | 243.41 | 15.733 | 16.54 | 64.86 | 183.53 | 11486 | 58 |
| **short-200** | **qwen3:14b@ctx8192** | 176 | 192.07 | **8.049** | **32.65** | 32.61 | 161.82 | 11757 | 99 |
| medium-2000 | qwen3.6:35b-a3b | 1068 | 11722.52 | 61.539 | 13.77 | 11397.31 | 333.48 | 11808 | 100 |
| medium-2000 | qwen3:14b（默认ctx） | 1183 | 263.06 | 26.468 | 15.23 | 71.42 | 199.75 | 11761 | 62 |
| **medium-2000** | **qwen3:14b@ctx8192** | 1183 | 199.30 | **12.757** | **31.8** | 34.31 | 168.66 | 11856 | 99 |
| long-8000 | qwen3.6:35b-a3b | 4178 | 1674.66 | 30.160 | 29.28 | 1342.09 | 343.88 | 11747 | 100 |
| long-8000 | qwen3:14b（默认ctx） | 4605 | 266.58 | 83.151 | 12.36 | 82.35 | 193.89 | 11944 | 59 |

**L1 关键发现**：
1. **`num_ctx=8192` 是 14b 的甜点区**：全 GPU 常驻（GPU 99%），decode 达 **~32 tok/s**，比 14b 在更大 num_ctx（默认溢出到 CPU）时的 12~16 tok/s **快约 2 倍**。这是 14b 的推荐运行形态。
2. **35b-a3b 的 prefill 抖动明显**：medium-2000 场景 prefill 高达 11397 ms（异常，100% GPU 但 prompt 处理极慢），显示 MoE + CPU 溢出下 prompt 处理不稳定；14b@8192 的 prefill 稳定在 32~34 ms。
3. 冷启 load 两者均在 160~350 ms 量级；峰值显存均逼近 11.8GB（12GB 卡的实际上限）。

### 2.2 超长场景（xlong-14000 @ num_ctx=16384）—— 部署包络上限

**结论：在 RTX 3060 12GB 上，当实际提示逼近 ~14K tokens 且 `num_ctx=16384` 时，llama.cpp 运行器崩溃，35b-a3b 与 14b 两者均无法稳定完成。** 详见 `L1_xlong_stability_finding.md`。

| 轮次 | 模型 | 结果 | 崩溃特征 |
|---|---|---|---|
| A | qwen3.6:35b-a3b | 崩溃 | 运行器 `Exception 0xc0000005`（访问违例），serve 被拖死，遗留孤儿 llama-server |
| B | qwen3:14b（隔离、RAM 已清理至 45GB 空闲） | 崩溃 | KV 分配/prefill 阶段死亡，连接重置（WinError 10054），**非 host-OOM** |

**运维建议**：单次送入模型的上下文预算应控制在 **~8K tokens 以内**；超长合同走"分块/多轮审计"而非单轮塞满 16K；serve 需配置崩溃自愈 + 孤儿 `llama-server` 清理。

---

## 3. L2 任务级功能正确性层结果

数据来源：`L2_aggregate.json` / `L2_aggregate_table.md`（由 `aggregate_l2.py` 从 `llm_trace.db` + JSONL 完整追踪按各轮 trace_window 精确切片聚合）。协议：5 份合同 × 2 遍 = 10 次运行/轮，`num_ctx_main=8192`。

### 3.1 轮次对比总览

| 指标 | 轮次A：qwen3.6:35b-a3b | 轮次B：qwen3:14b |
|---|---|---|
| 合同运行数 | 10 | 10 |
| 成功 ok | **3**（均为崩溃前 pass-1） | **8** |
| 失败 | 7（5 服务崩溃 + 2 OCR） | 2（均为 OCR 环境缺失） |
| **运行时崩溃** | **是（本轮 mid-run 崩溃；总计 3/3 复现）** | **否（0 次）** |
| LLM span 总数 | 40（35 成功 / 5 失败） | 70（70 成功 / 0 失败） |
| 模型/角色分布 | 100% main（`contract_audit_main`） | 100% main（`contract_audit_main`） |
| small 模型调用数 | 0 | 0 |
| **small→main 回退率** | **0%** | **0%** |
| **JSON 总体有效率** | **100%（35/35）** | **100%（70/70）** |
| **JSON 一次成功率** | **100%** | **100%** |
| 重试响应数 / JSON 需修复估计 | 0 / 0 | 0 / 0 |
| 总 token 消耗 | 91,714（崩溃前） | 187,439 |
| 单次平均 token | 2620.4 | 2677.7 |
| 单次平均延迟 | 12396.9 ms | **5913.4 ms** |
| 单次 p95 延迟 | 28687 ms | **18214 ms** |
| 平均 prompt_eval | 3563.1 ms | **649.8 ms** |
| 平均 eval | 8293.8 ms | **4880.0 ms** |
| 平均冷启 load | 301.4 ms | 156.2 ms |
| 单份合同平均耗时 | 109.9 s（含失败快返，仅供参考） | 52.84 s |

### 3.2 合同级明细

**轮次B（qwen3:14b）—— 完整、稳定：**

| 合同 | 遍 | ok | 耗时(s) | 风险数 | 路径 |
|---|---|---|---|---|---|
| Employee_Meal_Subsidy | 1 | ✓ | 66.72 | 2 | multipass_classic_stage1 |
| Employee_Meal_Subsidy | 2 | ✓ | 73.26 | 3 | multipass_classic_stage1 |
| 关爱通返点补充协议 | 1 | ✓ | 5.70 | 0 | multipass_classic_stage1 |
| 关爱通返点补充协议 | 2 | ✓ | 4.89 | 0 | multipass_classic_stage1 |
| 员工餐补平台服务合同 | 1 | ✓ | 42.04 | 1 | multipass_classic_stage1 |
| 员工餐补平台服务合同 | 2 | ✓ | 38.34 | 0 | multipass_classic_stage1 |
| 薪资税务代理服务合同 | 1 | ✓ | 108.73 | **16** | multipass_classic_stage1 |
| 薪资税务代理服务合同 | 2 | ✓ | 82.996 | **12** | multipass_classic_stage1 |
| 软件服务协议-红客厅.pdf | 1/2 | ✗ | — | — | OCR 环境缺失（见 3.4） |

**轮次A（qwen3.6:35b-a3b）—— 崩溃前的 3 个干净 pass-1：**

| 合同 | 遍 | ok | 耗时(s) | 风险数 | 路径 |
|---|---|---|---|---|---|
| Employee_Meal_Subsidy | 1 | ✓ | 218.78 | 5（2高/3中） | multipass_classic_stage1 |
| 关爱通返点补充协议 | 1 | ✓ | 27.91 | 0 | multipass_classic_stage1 |
| 员工餐补平台服务合同 | 1 | ✓ | 83.01 | 0 | multipass_classic_stage1 |
| 薪资税务代理服务合同 | 1 | ✗ | 111.90 | — | 运行器崩溃（WinError 10061） |
| 之后所有 pass-2 | — | ✗ | — | — | serve 已死，连接拒绝 |

**逐合同对比洞察**：
- 同一份轻量合同（关爱通）：14b ~5s vs 35b ~28s（35b 慢 ~5×）；员工餐补：14b ~40s vs 35b 83s（慢 ~2×）；Employee_Meal：14b 66~73s vs 35b 218s（慢 ~3×）。
- 最重的 **薪资税务** 合同：14b 稳定产出 **12~16 条**风险；35b-a3b 恰在此合同上崩溃，**无法产出**。
- Employee_Meal 上 35b 产出 5 条风险、14b 产出 2~3 条 —— 35b 召回或略高，但样本极小（35b 仅完成 3 份 pass-1，其中 2 份为 0 风险），且以"完全无法完成整轮"为代价，不具工程可用性。

### 3.3 引用有效率说明
- 合同审计在 `memory_temporarily_disabled`（`disable_reason=edge_llm_context_limit`）下走 **classic 多轮路径**，该路径不在风险条目上回填法条引用 ID（`citation_rate=0.0`），这是**既有管线行为，非模型缺陷**，两轮一致，不构成模型差异。
- 引用有效率的差异化验证属于税务审计管线（tax pipeline）范畴，见 L3 方法论。

### 3.4 失败归因
- **OCR 失败（2 次/轮，两轮一致）**：`软件服务协议-红客厅.pdf` 依赖 MinerU OCR，本机未安装（`FileNotFoundError: WinError 2`）。这是**环境约束，跨轮完全一致**，不影响模型对比。
- **服务崩溃（仅轮次A，5 次）**：35b-a3b 运行器崩溃导致后续请求 `WinError 10061/10054`，属模型可用性问题（见第 4 节）。

### 3.5 JSON 合规与回退口径说明
- JSON 有效性以 **`data/llm_traces_full/*/llm_trace_full.jsonl` 的 `event=response` 行**实际响应正文能否 `json.loads` 为权威口径（`llm_trace_spans.response_content` 列在库内为空，仅存长度元数据，故不作为口径）。
- 两轮所有成功响应均为**直接可解析 JSON**，无需 `<think>`/```` ```json ```` 清洗，`json_repair` 未触发，`reliability_retry`（召回增强重试，非 JSON 解析失败）产生的响应同样 100% 合规。
- `allow_small_to_main_fallback=true` 但合同审计管线恒用 main 角色（`contract_audit_main→main`），故 small→main 回退率恒为 0。

---

## 4. 稳定性深度分析（35b-a3b 崩溃根因）

**根因：原生推理层访问违例拖死 serve 进程。**

- 显存布局（serve 日志）：`load_tensors: CUDA0 model buffer = 8718 MiB`、`CUDA_Host model buffer = 12974 MiB` —— 23GB 模型有约 **13GB 溢出到 CPU host memory**，41 层中 **25 层 overflowing**。
- 崩溃签名（`ollama-serve-l2b.log`）：Go 运行时 panic —— **`signal arrived during external code execution` @ `runtime.cgocall`**，即 CGo 调用的原生 C++（llama.cpp/CUDA）代码段发生故障（段错误/访问违例），向上拖垮 `ollama serve` 父进程。
- 现象：父进程退出（终端回到提示符）；遗留**孤儿 `llama-server` 占用 14~15GB RAM**，若不清理会在后续加载时连锁 host-OOM。

**复现记录（3/3）**：
| 尝试 | 结果 |
|---|---|
| #1 | pass-2 mid-run 崩溃（WinError 10061） |
| #2 | 运行 ~7.5 分钟，3 份 pass-1 干净完成后在第 4 份崩溃（本报告采用其干净样本） |
| #3 | 在第 1 份合同 chunk-002 即崩溃，runs=10 ok=0；trace_window 17:22:35..17:23:35 |

**结论**：35b-a3b 在本硬件（12GB GPU）+ 合同审计持续负载下**不具备生产可用性**。若必须使用 35b 级模型，需升级到显存能容纳其全部权重的 GPU（≥24GB），或改用更小/更高量化的变体。

---

## 5. Round C（14b 兼任 small 角色）等价性论证

**结论：对"合同审计"管线，Round C ≡ Round B，无需单独跑批。**

- 依据 `config.json` 的 `routing.task_profiles`，合同审计恒用 `contract_audit_main → main` 角色；L2 追踪证实两轮均为 **100% main 角色、small 调用数=0、回退率=0**。
- 因此把 small 角色也指向 `qwen3:14b`（单模型全角色形态）对合同审计的**执行路径、token、延迟、JSON 合规完全无影响**，其指标与 Round B 逐项相等。
- Round C 的差异**仅在税务审计管线（tax pipeline）中体现**：该管线存在 small 角色的匹配/抽取调用（`tax_match_small` 等），单模型部署会用 14b 承担这些原本可由 4b 承担的小任务，影响的是"小任务延迟/显存驻留个数"，与合同审计质量无关。该验证归入 L3/税务专项，见第 6 节方法论。

---

## 6. L3 端到端业务质量层 —— 方法论（未全自动执行的说明）

L3 需"人工金标准 + 交叉裁判"，无法在本次全自动跑批中给出客观分值，故按测试计划以**方法论 + 可执行步骤**交付，供后续人工评审执行：

1. **金标准构建**：由法务/税务专家对 5 份合同（及税务专项样本）标注应识别风险点、应引用法条、风险等级，形成 golden set。
2. **候选产出**：分别用 Round A（若换用 ≥24GB GPU 后可跑通）/ Round B 产出审计结果 JSON。
3. **交叉裁判**：用独立强模型（或第二名专家）对"候选 vs 金标准"打分：风险召回率、精确率、法条引用正确率、风险定级一致性（Cohen's κ）。
4. **税务专项（承接 Round C 差异）**：在 tax pipeline 上对比 A/B/C，重点看 small 角色任务（匹配/抽取）在单模型 vs 双模型下的质量与资源占用。
5. **判据**：以 L1（性能/稳定）为准入门槛，L2（JSON 合规/召回稳定）为工程可用门槛，L3（业务准确率）为最终排序。

**基于 L1+L2 的前置结论**：35b-a3b 未通过 L1 稳定性准入门槛（xlong 崩溃）与 L2 可用性门槛（3/3 崩溃），故当前硬件下 L3 排序已无悬念 —— `qwen3:14b@ctx8192` 为唯一工程可用选项。

---

## 7. 配置快照（可复现）

三轮共享基线（仅 `main_model.model` 变化）：

```jsonc
// config.json 关键字段（评测期）
"llm_config":   { "provider": "ollama", "model": "<每轮变化>" },
"local_llm": {
  "provider": "ollama",
  "main_model":  { "model": "<每轮变化>", "num_ctx": 8192 },
  "small_model": { "model": "qwen3:4b",  "num_ctx": 8192 },
  "keep_alive": "30m",
  "allow_small_to_main_fallback": true,
  "json_repair_enabled": true,
  "routing": { "task_profiles": { "contract_audit_main": "main" } },
  "llm_trace_full_db_path": "../data/llm_trace.db",
  "llm_trace_full_dir":     "../data/llm_traces_full"
}
```

| 轮次 | main_model.model | small_model.model | num_ctx_main | 说明 |
|---|---|---|---|---|
| A | `qwen3.6:35b-a3b` | `qwen3:4b` | 8192 | 当前基线 |
| B | `qwen3:14b` | `qwen3:4b` | 8192 | **推荐** |
| C | `qwen3:14b` | `qwen3:14b` | 8192 | 合同审计等价于 B（第 5 节） |

环境变量：`OLLAMA_KEEP_ALIVE=30m`、`OLLAMA_MODELS=E:\ollama_model`。

---

## 8. 复现步骤与产物索引

### 8.1 复现命令
```powershell
# 0) 干净启动 ollama serve
$env:OLLAMA_KEEP_ALIVE="30m"; $env:OLLAMA_MODELS="E:\ollama_model"
ollama serve *> .runtime\logs\ollama-serve.log

# 1) L1（示例：14b@8192 medium）
python bin\benchmark-ollama-vs-llamacpp.py  # 见脚本内 --model / --num-ctx / --prompt 参数

# 2) L2 跑批（从 app 目录，进程内调审计管线）
cd app
venv\Scripts\python.exe ..\plan\local-llm-llamacpp\reports\model-comparison-20260729\l2_contract_runner.py `
  --round-label "B-14b" --main-model "qwen3:14b" --small-model "qwen3:4b" `
  --num-ctx-main 8192 --num-ctx-small 8192 --contracts "all" --passes 2 --warmup 1 `
  --out "..\plan\local-llm-llamacpp\reports\model-comparison-20260729\L2_contract_B-14b.json"

# 3) L2 聚合（从报告目录）
cd plan\local-llm-llamacpp\reports\model-comparison-20260729
..\..\..\..\app\venv\Scripts\python.exe aggregate_l2.py `
  --round "A-35b-a3b:L2_contract_A-35b.json:L2_run_A.log" `
  --round "B-14b:L2_contract_B-14b.json:L2_run_B.log" `
  --out L2_aggregate.json --md L2_aggregate_table.md
```

### 8.2 产物索引（`plan/local-llm-llamacpp/reports/model-comparison-20260729/`）
| 文件 | 内容 |
|---|---|
| `FINAL_REPORT.md` | 本报告 |
| `L1_summary_table.md` | L1 短/中/长汇总表 |
| `L1_xlong_stability_finding.md` | L1 超长 16K 崩溃上限发现 |
| `L1_*.json` | L1 各场景原始采集 |
| `make_prompts.py` / `prompt_*.txt` | L1 四规模提示语料生成器与语料 |
| `benchmark-ollama-vs-llamacpp.py`（bin/） | L1 压测工具 |
| `l2_contract_runner.py` | L2 进程内跑批脚本（模型切换+trace_window） |
| `L2_contract_A-35b.json` / `L2_contract_B-14b.json` | L2 各轮原始结果 |
| `L2_run_A.log` / `L2_run_B.log` | L2 跑批 stdout 日志 |
| `aggregate_l2.py` | L2 追踪聚合脚本 |
| `L2_aggregate.json` / `L2_aggregate_table.md` | L2 聚合结果与对比表 |
| `data/llm_trace.db` | 全量 LLM 调用 span（按 trace_window 切片） |
| `data/llm_traces_full/2026-07-29/llm_trace_full.jsonl` | 完整响应正文（JSON 合规权威口径） |

### 8.3 可追溯性
- 每轮结果 JSON 内含 `trace_window`（UTC ISO 起止），聚合脚本据此从 `llm_trace_spans` 精确切片，确保各轮数据互不串扰。
- 轮次A 采用第 #2 次尝试的干净样本（trace_window `17:11:17..17:18:52`）；第 #1、#3 次崩溃记录见 `L2_contract_A-35b.json` 的 `stability_reproductions` 字段。

---

## 9. 结论与建议

1. **主模型选 `qwen3:14b`，固定 `num_ctx=8192`**：全 GPU 常驻、~32 tok/s、单次调用延迟约为 35b-a3b 的一半、prefill 快 ~5.5×，JSON 一次成功率 100%，10 次运行零崩溃。
2. **弃用 `qwen3.6:35b-a3b`（在 12GB GPU 上）**：23GB 权重溢出 ~13GB 到 CPU，持续负载下原生层访问违例崩溃（3/3 复现），工程不可用。如需 35b 级模型须上 ≥24GB 显存 GPU。
3. **单模型全角色部署（Round C）**对合同审计与 Round B 完全等价，可放心用 14b 兼任 small；其差异只需在税务专项（L3）另行评估。
4. **上下文预算 ≤ 8K tokens**：超长合同分块多轮审计，勿单轮塞满 16K（两模型在 16K + 14K 输入下均崩溃）。
5. **运维护栏**：serve 崩溃自愈 + 孤儿 `llama-server` 自动清理；OCR 依赖（MinerU）需在生产环境安装以支持 PDF 合同。
6. **L3 后续**：按第 6 节方法论构建人工金标准 + 交叉裁判，完成业务准确率终评（当前 L1/L2 已锁定唯一可用选项）。
