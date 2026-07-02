# Edge LLM CPU Local Known Issues

## 1. Current Status

This file tracks known issues and residual risks for the CPU-only local edge LLM rollout.

The current implementation has completed:

- Routing
- JSON guard
- Local-only configuration path
- Memory pipeline fallback
- Regression automation entrypoint
- Real local main model smoke validation (`qwen3.6:27b` + `llama3.2:3b`)
- Enterprise offline deployment branch (`feature/enterprise-local-ollama-only`)
- Cloud dependency audit (zero runtime external calls confirmed)
- Hardware fitness benchmark for i5-12500/64GB

The current implementation has not yet completed:

- Real local model smoke validation on this machine
- Long-document timeout pressure testing
- Full API-level end-to-end acceptance with live local models

## 2. Open Issues

### KI-001 Real Local Main Model Verified

- Severity: High
- Status: **Mitigated**
- Description:
  The repository now supports a local main model route and has been validated against a real running Ollama instance on `http://127.0.0.1:11434/v1` using `qwen3.6:27b` and `llama3.2:3b`.
- Impact:
  `TC-001` is now satisfied.
- Verified Actions:
  - `ollama list` confirmed both models installed.
  - `python .\bin\start-local-llm-servers.py` confirmed Ollama API reachable.
  - `python .\bin\benchmark-local-llm.py` confirmed live inference with latency metrics recorded.

### KI-002 Long Document Timeout Behavior Not Yet Pressure Tested

- Severity: High
- Status: Open
- Description:
  Memory mode has fallback logic, but there is still no live long-document pressure test using a real CPU-only model.
- Impact:
  The system may still show latency spikes or degraded output quality on large contracts.
- Suggested Action:
  Prepare a long contract dataset and run controlled latency tests for:
  - `memory` mode
  - `tax_match`
  - `tax_risk`

### KI-003 Existing UTC Deprecation Warnings Still Exist In Older Modules

- Severity: Medium
- Status: Open
- Description:
  Several existing modules still use `datetime.utcnow()` and emit deprecation warnings in Python 3.13.
- Impact:
  Does not currently break functionality, but it pollutes regression logs.
- Suggested Action:
  Replace remaining `datetime.utcnow()` usage with timezone-aware UTC timestamps in a focused cleanup pass.

### KI-004 Ollama Model Availability Depends On Local Installation

- Severity: Medium
- Status: Open
- Description:
  The local runtime now assumes these Ollama model names are installed:
  - `qwen3.6:27b`
  - `llama3.2:3b`
- Impact:
  If the local machine has not pulled the exact model tags, startup and smoke validation will fail.
- Suggested Action:
  Run `python .\bin\download-local-llm-models.py` or override model names in the local config.

### KI-005 Enterprise Local-Only Guardrails Not Yet API-Level Verified

- Severity: Medium
- Status: Open
- Description:
  The enterprise branch disables cloud fallback by configuration, but a full API-level acceptance pass has not yet verified that all deployed configs keep `llm_config` and `local_llm` pointed only to local Ollama endpoints.
- Impact:
  A misconfigured environment could still accidentally target a non-local provider.
- Suggested Action:
  Run admin/API acceptance checks with the enterprise config and confirm every effective model target resolves to `http://127.0.0.1:11434/v1`.

### KI-006 27B Main Model Latency Too High for i5-12500-class CPUs

- Severity: **High**
- Status: Open
- Description:
  On i5-12500 (6P/12T, 64GB), `qwen3.6:27b` achieves only ~2.2 tok/s in pure CPU inference. A 200-token contract audit response takes ~100 seconds. At this throughput, a full multi-clause contract audit will take several minutes.
- Impact:
  User-facing responsiveness is severely degraded. Not suitable for concurrent use.
- Suggested Action:
  Test lighter main models already installed locally (`qwen2.5-coder:14b` at 9.0 GB or `qwen3.5:9b` at 6.6 GB). Expected throughput improvement: 2-3x with 14B, 4-5x with 9B.

### KI-007 27B Model Loading Causes ~26 GB Disk + Memory Pressure

- Severity: Medium
- Status: Open
- Description:
  `qwen3.6:27b` occupies 17 GB on disk plus ~9 GB in-memory workspace, totaling ~26 GB peak allocation. With `llama3.2:3b` (2 GB) and auxiliary models (embeddings, reranker, translation), total system memory usage approaches 35-40 GB.
- Impact:
  Leaves ~25 GB headroom on 64 GB systems — adequate for single-task operation but tight for multitasking or other enterprise workloads.
- Suggested Action:
  Monitor Ollama memory usage with `ollama ps` during sustained operations. Consider offloading non-critical auxiliary models during heavy audit sessions.

## 3. Closed Or Mitigated Issues

### KI-M1 Invalid JSON Breaks Structured Flows

- Status: Mitigated
- Notes:
  `JSON Guard` now covers tax and memory callback paths, reducing local structured-output failures.

### KI-M2 High-Risk Tax Match Cannot Be Escalated

- Status: Mitigated
- Notes:
  The enterprise branch keeps high-risk review local by disabling forced cloud escalation in the default config.

### KI-M3 Memory Callback Failure Can Abort Useful Output

- Status: Mitigated
- Notes:
  Clause audit and flush callbacks now support fallback logic, and flush still keeps a safe degraded text fallback.

## 4. Exit Criteria For Closing Phase 4

Phase 4 can be considered fully closed when all items below are satisfied:

- `TC-001` passes with real local services
- `TC-007B` passes with real long documents
- `TC-008` is executed with a full regression result log
- Enterprise local-only config is API-level verified end-to-end
