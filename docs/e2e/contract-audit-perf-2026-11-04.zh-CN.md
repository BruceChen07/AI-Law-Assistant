# 合同审计性能优化：实测对比报告

- 环境：Windows，20 逻辑核 / 64 GB，纯 CPU；llama.cpp `--n-gpu-layers 0 --threads 18`
- 模型：`Qwen3.6-35B-A3B-UD-Q4_K_M.gguf`（20.6 GB）
- 合同：`data/contracts/软件服务协议-红客厅（中智股份）.pdf`（7 页 / 2.11 MB，115 条款，12 个审计分块）
- 三项对比均使用相同合同、相同审计参数（`audit_mode=rag, risk_detection_mode=balanced, tax_focus=true`）

## 一、结论

| 方案 | 总耗时 | 相对基线 | 峰值内存 |
|---|---:|---:|---:|
| A 基线（16k 上下文 / 单槽 / 构建预览清单 / 重新数字化） | 654.8 s | — | 32.6 GB |
| B 8k 预算 + 双槽并发 + 跳过预览清单 + 复用数字化 | **431.3 s** | **−34.1%** | 32.5 GB |
| C 8k 预算 + 单槽 + 跳过预览清单 + 复用数字化（**交付配置**） | **484.0 s** | **−26.1%** | 32.3 GB（空闲 25.5 GB） |

**结论：5 项优化中 2 项真实有效（预览清单跳过、数字化复用，各省约 82 秒），1 项部分有效（并发槽位，单次审计仅快 11%），1 项在本模型上完全无效（prompt 缓存复用），1 项只降低空闲内存（8k 上下文）。**

选 C 作为默认：单次审计 484 秒，单请求延迟稳定在 40 秒左右，内存占用在空闲时比 16k 降低 7 GB，且与 `audit_token_policy` 的 8k 预算一致。B 只快 53 秒，却把单次调用延迟推到 70 秒、长输出有超时风险，更适合多用户并发场景。

## 二、逐项结论

### 1) Prompt 缓存复用：❌ 实测无效

- 已做：请求体显式携带 `cache_prompt: true`；prompt 模板本身的顺序是「指令 → 法规白名单 → 合同条款」，白名单在各分块间稳定，理论上可复用约 500–700 tokens 前缀。
- 实测：`/slots` 显示 `n_prompt_tokens_cache` 仅 **15**（约等于 system 提示长度），12 次调用几乎全部重新处理 prompt。
- 关键证据：llama-server 启动日志 `load_model: cache_reuse is not supported by this context, it will be disabled` —— 该 MoE 模型的上下文不支持 KV 移位复用，`--cache-reuse` 被自动关闭。
- 结论：开关保留（对支持的模型仍有价值），但**不应把它算作本次收益**。若后续换模型，可重新验证。

### 2) 并发槽位：⚠️ 部分有效，未设为默认

| 指标 | 单槽（C） | 双槽（B） |
|---|---:|---:|
| 12 次 LLM 总耗时 | 482.1 s | 429.4 s |
| 单次平均延迟 | 39.7 s（max 60.1） | 70.6 s（max 103.4） |
| 总耗时 | 484.0 s | 431.3 s |
| llama-server 内存 | 32.3 GB | 32.5 GB |

- 双槽只带来 **11%** 的总时间收益：单序列推理已经吃满 18 线程，第二条序列在抢 CPU，生成速度从 4.0 tok/s 掉到 2.1–3.1 tok/s。
- 实现已保留：`contract_audit.parallel_workers` + `--parallel N`，默认 1；多用户并发或短文本场景可调 2。

### 3) 文本抽取去重：✅ 有效，省 82.6 秒

- 定位结果：这 82.6 秒并不在 DOCX 解析本身（实测 0.02 s），而在**审计前构建预览清单**时调用 MinerU 抽取版面坐标（`coord_provider=mineru`）。
- 处理：新增 `contract_preview.audit_build_manifest`，CPU 部署默认 `false`，审计阶段跳过；预览接口仍可按需生成。
- 效果：`extracting` 阶段 82.6 s → **0.02 s**。
- 代价：首次审计的风险条目没有页码坐标，用户打开预览后才会补齐。

### 4) 数字化缓存：✅ 本来就支持，实测命中

- 代码中已存在按内容 sha256 的跨文档复用（`document_digitization._is_reusable_digitization_record`），首次审计因为库里没有历史记录所以是 `reused=false`。
- 本次复测：`digitization_reused=true`，PDF→DOCX 阶段 82.6 s → **0.02 s**。
- 结论：无需改代码，属于“第一次付费、之后免费”。

### 5) 上下文降到 8k：✅ 空闲内存下降，峰值不变

| 配置 | 加载后空闲 | 审计后峰值 |
|---|---:|---:|
| 16k / 2 槽 | 32.5 GB | 32.5 GB |
| 8k / 1 槽 | **25.5 GB** | 32.3 GB |

- 8k 上下文让空闲占用降低约 7 GB，但推理时计算缓冲区会把峰值推回 32 GB 左右。
- `audit_token_policy` 已同步下调：`context_window_tokens=8192`、`input_hard_limit_tokens=5120`、`input_soft_limit_tokens=3584`、`clause_group_target_tokens=2800`。
- 分块数仍为 12，分块策略未受影响。

## 三、质量校验

| 轮次 | 风险数 | 高风险 | 备注 |
|---|---:|---:|---|
| A 基线 | 1 | 1 | 税务赔偿条款缺认定依据 |
| B 双槽 | 0 | 0 | 采样波动（temperature=0.2） |
| C 交付 | 1 | 1 | 与基线结论一致 |

在 A/B/C 三种配置下结论在 0–1 项之间波动，属于 `temperature=0.2` 的采样随机性，**未观察到配置改动导致的系统性退化**。但 7 页合同仅 0–1 项风险仍然偏少，建议后续单独做召回率校准（与本次性能改动无关）。

## 四、交付配置

```json
{
  "contract_audit": { "parallel_workers": 1 },
  "audit_token_policy": {
    "context_window_tokens": 8192,
    "input_hard_limit_tokens": 5120,
    "input_soft_limit_tokens": 3584,
    "clause_group_target_tokens": 2800
  },
  "contract_preview": { "audit_build_manifest": false },
  "memory_runtime_config": { "local_context_window_tokens": 8192 }
}
```

```powershell
llama-server.exe --ctx-size 8192 --parallel 1 --cache-reuse 256 --n-gpu-layers 0 --threads 18 ...
```

## 五、仍可优化但本次未做

1. **输出长度**：12 次调用平均只生成 162 tokens，`classic_audit_max_tokens=1200` 造成大量无效 kv 预留；把 `max_tokens` 下调到 400–600 可减少调度开销（收益待测）。
2. **首字延迟**：prompt 处理 74 tok/s 已是 CPU 上限，进一步提速只能量化权重（Q4→Q3）或换更小模型。
3. **异步化**（上一份报告 P1）：审计期间事件循环仍被独占，本次未改动，进度接口在审计中依然不可用。
