# 合同速读摘要（独立特性线）

给税务审核师用的「一页纸」合同摘要：先看这段，就知道这份合同在讲什么、核心商业安排是什么、税务上先核对什么。

该特性**不参与审计流程**，独立成一个模块和接口，可单独发布/回滚。

## 组成

| 层 | 位置 | 说明 |
|---|---|---|
| 服务 | `app/services/contract_summary.py` | 生成摘要：缓存、长文 map-reduce、JSON 校验与降级 |
| 接口 | `GET /contracts/{document_id}/summary` | 返回 `summary` + `meta`，与审计接口互不影响 |
| 前端 | `web/App.jsx`「概览」区块 | 结果就绪后异步拉取，失败静默降级，不阻塞审计展示 |
| 配置 | `contract_summary_enabled` | 总开关，默认 `true` |

## 输出结构

```json
{
  "document_id": "...",
  "summary": {
    "title": "合同名称",
    "about": "合同在说什么（150-220 字）",
    "core": "核心商业安排（220-320 字）",
    "tax": "税务审核提示（200-300 字）",
    "source_file": "原始文件名"
  },
  "meta": {
    "contract_summary_status": "ok | fallback | error | disabled",
    "contract_summary_cache_hit": false,
    "contract_summary_map_reduce": false,
    "contract_summary_map_chunks": 0,
    "contract_summary_chars": 920
  }
}
```

## 行为

- **上传即生成**：文件上传拿到 `document_id` 后前端立刻拉取摘要，不等审计结束，用户先看到「合同讲什么」；
- **文本抽取复用**：`extract_text_with_config` 按文件 sha256 缓存抽取结果，扫描件只跑一次 MinerU OCR，审计与摘要共用；同一文件并发抽取时用哈希锁串行化，后来者直接读缓存；
- **缓存**：以 `sha256(正文)+语言` 为键存到 `data/memory/contract_summary/`，重复打开不再调用模型；
- **长合同**：正文超过 8,000 字符时先按 6,000 字符分块抽取事实（map），再用事实生成摘要（reduce），最多 24 块；
- **降级**：模型没返回可用 JSON 或内容过短时，回退为正文首段（`status=fallback`），保证界面永远有内容；
- **不阻塞**：摘要由前端异步请求，审计结果先展示。

## 成本

CPU（Qwen3.6-35B-A3B，llama.cpp）实测：1,300 字补充协议约 **94 秒**；正文 2.3 万字的长合同会触发 map-reduce，耗时随分块数线性增长。

## 配置

```json
{ "contract_summary_enabled": true }
```

关掉后接口返回 `status=disabled`，前端不显示摘要区块。

## 与审计的关系

- 审计接口 `/contracts/audit` 的入参未改动，只是改为「入队 + 摘要优先」执行；
- 摘要失败不影响审计结果；
- 摘要优先实现：队列 worker 在跑审计前先生成摘要，接口在队列持有该文档时返回 `status=pending`，前端每 3 秒轮询直到就绪（最多约 3 分钟）；实测提交后 265 ms 返回 pending，85 秒摘要就绪，此时审计仍在执行；
- 所在分支：`feature/audit-summary`（由 `feature/contract-summary` 与 `feature/audit-queue` 合并）。
