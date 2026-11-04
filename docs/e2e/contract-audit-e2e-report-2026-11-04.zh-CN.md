# 合同审计端到端测试报告（纯 CPU / llama.cpp）

- 测试时间：2026-11-04 01:16:45 ~ 01:27:39（本地时区）
- 测试对象：7 页 PDF 合同 `data/contracts/软件服务协议-红客厅（中智股份）.pdf`（2.11 MB）
- 运行环境：Windows，20 逻辑核 / 64 GB 内存，**无可用 GPU**（torch 为 CPU 版，`torch.cuda.is_available()=False`）
- 推理栈：`llama.cpp`（`third_party/llama.cpp/bin-win-cpu-x64/llama-server.exe`，`--n-gpu-layers 0 --threads 18`）
- 模型：`Qwen3.6-35B-A3B-UD-Q4_K_M.gguf`（20.6 GB，MoE 激活参数约 3B）
- OCR：MinerU 本地模型（`model_source=local`，pipeline 后端）
- 审计参数：`language=zh, audit_mode=rag, risk_detection_mode=balanced, tax_focus=true`
- 模型加载耗时：15.8 s；服务启动到 `/health` 就绪：11.8 s

## 一、结论

**可以顺利审计完成**：接口返回 200，产出结构化结果（12 个片段、1 项高风险、引用《中华人民共和国发票管理办法》第二十二条），审计记录与阶段日志均落库。

但存在 **4 个问题 + 2 个观察项**，其中「审计期间整个服务不可用」是最需要处理的。

**端到端总耗时 654.8 秒（约 10 分 55 秒）**，对 7 页合同来说在纯 CPU 上属于可用但偏慢。

## 二、耗时分解

| 阶段 | 起止 | 耗时 | 占比 |
|---|---|---:|---:|
| 接收/落库 | 01:16:45.159 → 01:16:45.190 | 0.03 s | ~0% |
| PDF → DOCX 数字化（MinerU） | 01:16:51.078 → 01:18:13.643 | **82.6 s** | 12.6% |
| DOCX 文本抽取（文档含 6 张内嵌图片，抽取耗时异常偏高） | 01:18:13.643 → 01:19:36.209 | **82.6 s** | 12.6% |
| 证据检索（embedding + rerank） | 01:19:36.209 → 01:19:37.592 | 1.4 s | 0.2% |
| 条款审计（12 次 LLM 调用） | 01:19:37.672 → 01:27:39.667 | **482.0 s** | 73.6% |
| 结果组装 + 落库 | 01:27:39.667 → 01:27:39.672 | 0.005 s | ~0% |
| **合计** | 请求 01:16:41 发起 → 01:27:36 返回 | **654.8 s** | 100% |

### LLM 调用明细

| 指标 | 数值 |
|---|---:|
| 调用次数 | 12 |
| 平均延迟 | 40.1 s |
| 最快 / 最慢 | 27.8 s / 61.4 s |
| 输入 tokens（prompt） | 17,788 |
| 输出 tokens（completion） | 1,948 |
| 有效生成速度 | ≈ 4.0 tok/s（1948 / 482 s） |
| 端到端 token 吞吐 | ≈ 41 tok/s（含 prompt 处理） |

基线对照：单独用 14 tokens 的短问题测试，生成速度 7.36 tok/s；审计场景每次要处理约 1,450 tokens 的 prompt，prompt 处理占了大头，所以单次 40 秒。

### 容量换算（推算，非实测）

按 12 个片段 / 482 秒线性外推：

- 20 页左右合同 ≈ 30 个片段 ≈ **20 分钟**
- 50 页合同 ≈ 75 个片段 ≈ **50 分钟**

## 三、结果摘要

- `summary`：分块经典审计完成，共在 12 个片段中识别 1 项风险
- `risk_summary`：high=1 / medium=0 / low=0
- 风险：合同缺少税务赔偿的触发条件与金额认定依据（应以税务机关最终生效认定或法院生效裁判为准）
- 检索：8 个查询全部成功，12 条证据，覆盖度 2.0，未降级
- 数字化：7 页、114 段、6 表、6 图，文本匹配率 0.8696，`digitization_ocr_engine=mineru`
- 执行路径：`classic_multipass`（记忆模块处于临时关闭状态）
- 完整响应：`logs/e2e/audit_response.json`

## 四、问题清单

### P1（高）审计期间整个服务不可用

**现象**：审计进行中，`GET /health`、`GET /contracts/audit/{id}/progress` 全部超时（5 秒内返回 `000`，无响应）；审计结束后立即恢复（200，13 ms）。

**证据**：01:17 与 01:21 两次探测均 `000 time=5.01s`；同时 llama.cpp `/health` 正常 200，说明不是模型侧问题。

**根因**：`app/api/routers/contracts/audit.py` 的 `async def audit_contract_file(...)` 内部直接同步调用 `run_contract_pipeline_bundle(...)`，没有丢进线程池。单进程 uvicorn 的事件循环被占满 654 秒。

**影响**：
- 前端拿不到进度，进度条全程卡死，用户只能干等
- 其他所有接口（登录、列表、健康检查）同时不可用
- 若前面有 Nginx/网关，默认 60 s 超时会把请求掐断，用户看到失败但后台仍在跑
- 健康检查失败会让监控告警、可能触发误重启

**建议**：把审计主体改为 `await run_in_threadpool(...)`，或改成「提交任务 → 立即返回 audit_id → 后台执行」的异步模型，进度接口才有意义。

### P2（中）审计日志的 `document_id` 指向不存在的文档

**现象**：本次审计的 16 条阶段日志里 `document_id=8d24acaa-9599-4293-b6da-05821d4ad4ca`，但 `documents` 表里没有这条记录；真实文档 id 是 `89785903-bb4a-4586-b0e8-010f5cafc17b`。

**根因**：`audit.py` 先用新生成的 `doc_id` 注册日志上下文，随后 `insert_document()` 按 `original_filename` 命中同名旧记录并**复用旧 id**（`app/services/crud.py:234`），导致日志上下文里的 id 与最终文档 id 分叉。

**佐证**：同一文件名的旧记录被 `UPDATE`（`created_at` 被改成本次时间、`file_path` 指向新上传文件），说明是「同名覆盖」而非新建。

**影响**：
- 按文档筛选审计日志会查不到该合同，审计追溯断链
- 同一文件名重复上传会覆盖旧文档行，历史审计与文档的对应关系被改写

**建议**：`insert_document` 返回实际 id 后重新注册日志上下文；或去掉「同名复用」，改为按内容 sha256 去重并保留历史行。

### P3（中）唯一高风险项没有引用映射

**现象**：风险项 `citation_status="unmapped"`、`citation_id=""`，虽然 `basis` 写了《中华人民共和国发票管理办法 第二十二条》，但无法定位到具体证据片段。

**影响**：前端「查看引用」不可点，用户无法核验结论依据；对法律类产品属于硬伤。

**建议**：检查 `classic_multipass` 路径下 citation 的分配逻辑（检索到的 12 条 `citation_ids` 未被消费）。

### P4（低）`meta.llm_model` 记录为 `"auto"`

**现象**：结果元数据里 `llm_model: "auto"`，实际模型是 `Qwen3.6-35B-A3B-UD-Q4_K_M`。

**影响**：审计结果无法回溯到具体模型版本，换模型后历史结果不可比。

**建议**：写入时替换为 `/v1/models` 探测到的真实模型名。

### O1（观察）只识别出 1 项风险

7 页、115 个条款、12 个片段的软件服务协议只报 1 项税费赔偿风险。`classic_max_tokens=1200`，12 次调用平均只输出 162 tokens，存在漏检可能。建议人工抽检若干份合同校准阈值与 prompt。

### O2（观察）只接受 `.docx` 和 `.pdf`

`/contracts/audit` 仅允许这两种扩展名（`ext not in [".docx", ".pdf"]` → 400）。`data/contracts/` 目录里还有 `.doc` 文件，直接上传会失败。

## 五、性能优化建议（按收益排序）

1. **Prompt 缓存复用**（收益最大）：12 次调用每次重发约 1,450 tokens 且前缀高度相似。llama.cpp 开 `--cache-reuse` / 让 prompt 前缀稳定，可把单次 40 s 压到 15 s 量级。
2. **并发槽位**：`--parallel 2`（20 核、18 线程时仍有富余），理论 1.7~1.9 倍。
3. **文本抽取去重**：DOCX 抽取花了 82.6 s（114 段、6 表、6 图），远超正常 docx 解析耗时，疑似对 6 张内嵌图片逐张跑 OCR；若审计不需要图片内容，可跳过或加缓存。
4. **数字化缓存**：本次 `digitization_reused=false`；同一内容再次审计可复用 DOCX，省 82 s。
5. **降低上下文**：`context_window_tokens=16384` 使 llama-server 常驻 32.6 GB（模型 20.6 GB + KV cache）。改 8k 可显著降内存，但需同步调 `audit_token_budget.policy`。
6. **异步化**（同时解决 P1）：把长任务移出事件循环后，还可以顺手做多合同排队。

## 六、资源占用

| 进程 | RSS | 说明 |
|---|---:|---|
| llama-server（35B-A3B, 16k ctx） | 32.6 GB | 20.6 GB 权重 + KV cache |
| 后端（uvicorn + 嵌入模型 + MinerU 子进程） | 约 2~3 GB | 峰值出现在 OCR 阶段 |

**注意**：32 GB 内存的机器跑 35B-A3B + 16k 上下文会 OOM；建议 ≥48 GB，或降到 8k 上下文 / 换 14B 模型。

## 七、复现步骤

```powershell
# 1) 启动 llama.cpp（纯 CPU）
third_party\llama.cpp\bin-win-cpu-x64\llama-server.exe `
  --host 127.0.0.1 --port 18080 `
  --model models\llm\Qwen3.6-35B-A3B-UD-Q4_K_M.gguf --alias Qwen3.6-35B-A3B-UD-Q4_K_M `
  --ctx-size 16384 --threads 18 --threads-http 2 --parallel 1 `
  --n-gpu-layers 0 --jinja --cont-batching --metrics

# 2) 启动后端
$env:APP_PORT='8020'; $env:APP_PORT_AUTO_SWITCH='0'
app\venv\Scripts\python.exe -m app.main

# 3) 登录（默认管理员）
curl --noproxy "*" -X POST http://127.0.0.1:8020/api/auth/login `
  -H "Content-Type: application/json" --data-binary "@logs/e2e/login-body.json"

# 4) 提交审计（注意：合同接口没有 /api 前缀）
curl --noproxy "*" -X POST http://127.0.0.1:8020/contracts/audit `
  -H "Authorization: Bearer <token>" `
  -F "file=@data/contracts/软件服务协议-红客厅（中智股份）.pdf;type=application/pdf" `
  -F "language=zh" -F "audit_mode=rag" -F "risk_detection_mode=balanced" -F "tax_focus=true" `
  -F "audit_id=<自定义 uuid>" --form-string "title=软件服务协议-红客厅（中智股份）"

# 5) 查询进度（审计结束前会超时，见 P1）
curl --noproxy "*" http://127.0.0.1:8020/contracts/audit/<audit_id>/progress `
  -H "Authorization: Bearer <token>"
```

## 八、测试产物

| 文件 | 内容 |
|---|---|
| `logs/e2e/audit_response.json` | 审计完整响应（20 KB） |
| `logs/e2e/llamacpp.err.log` | llama.cpp 启动/推理日志 |
| `logs/e2e/backend.err.log` | 后端标准错误 |
| `logs/2026-11-04/main.log` | 阶段进度、每次 LLM 调用 token 与延迟 |
| `data/files/contract_digitization/89785903-.../v1/*_digitized.docx` | PDF 数字化产物 |
