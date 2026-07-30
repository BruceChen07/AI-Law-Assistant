# 部署形态探测：num_ctx 对显存驻留的影响（RTX 3060 12GB）

采集时间：2026-07-29　Ollama 0.32.1　CUDA　RTX 3060 12GB（可用约 11 GiB）

用 `ollama ps` 的 PROCESSOR 列直接读取模型的 CPU/GPU 层分割，作为 L1 decode 速度差异的根因证据。

| 模型 | num_ctx | 常驻大小 | PROCESSOR (CPU/GPU) | 说明 |
|---|---|---|---|---|
| qwen3.6:35b-a3b | 16384 | 24 GB | **59% / 41%** | 24GB 远超显存，多数层走内存；MoE 激活仅 ~3B 故 decode 仍可达 ~28 tok/s |
| qwen3:14b | 16384 | 12 GB | **13% / 87%** | 9.3GB 权重 + 16K KV cache 超出 11GiB 可用显存，13% 层溢出到 CPU，decode 跌至 ~16.5 tok/s |
| qwen3:14b | 8192 | 10 GB | **100% GPU** | 8K 上下文下权重+KV 完整进显存，全 GPU 推理，decode 显著回升 |

## 关键结论

1. **num_ctx 是 14b 在 12GB 卡上的决定性变量**：8K 全 GPU、16K 溢出 13%。
2. **文档 4.4 建议的 main num_ctx=16384 会让 14b 部分溢出**，实测 16K 下 14b decode 反而低于 35b-a3b。
3. 35b-a3b 在 16K 下 59% 在 CPU（预期内），但因 MoE 激活参数小，decode 不至于崩塌。
4. 若采用 14b 且需要 16K 上下文预算，需接受 13% CPU 溢出的 decode 损失；若业务上下文可控制在 8K 内，14b 可全 GPU 高速运行。
