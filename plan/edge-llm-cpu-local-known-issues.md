# Edge LLM CPU Local Known Issues

## 1. Current Status

This file tracks known issues and residual risks for the CPU-only local edge LLM rollout.

The current implementation has completed:

- Routing
- JSON guard
- Local fallback
- Cloud review for high-risk tax cases
- Memory pipeline fallback
- Regression automation entrypoint

The current implementation has not yet completed:

- Real local model smoke validation on this machine
- Long-document timeout pressure testing
- Full API-level end-to-end acceptance with live local models

## 2. Open Issues

### KI-001 Real Local Main Model Not Yet Verified

- Severity: High
- Status: Open
- Description:
  The repository now supports a local main model route, but the current session has not validated a real running Ollama main model service on `http://127.0.0.1:11434/v1`.
- Impact:
  `TC-001` remains open.
- Suggested Action:
  Start the real local service and run:

```bash
python .\bin\start-local-llm-servers.py
python .\bin\download-local-llm-models.py
python -m pytest tests/test_llm_router.py tests/test_llm_local_mode.py tests/test_json_guard.py tests/test_local_llm_fallback.py tests/test_tax_contract_parser.py tests/test_tax_matcher.py tests/test_tax_risk.py tests/test_memory_pipeline_fallback.py tests/test_contract_audit_memory_mode.py tests/test_contract_audit.py
```

### KI-002 Long Document Timeout Behavior Not Yet Pressure Tested

- Severity: High
- Status: Open
- Description:
  Memory mode has fallback logic, but there is still no live long-document pressure test using a real CPU-only model.
- Impact:
  The system may still show latency spikes or overuse cloud fallback on large contracts.
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

### KI-005 Cloud Fallback Cost Control Not Yet Measured

- Severity: Medium
- Status: Open
- Description:
  The runtime can now promote high-risk or invalid local results to cloud fallback, but the actual cloud fallback ratio is not yet measured with production-like workloads.
- Impact:
  Real operating cost remains uncertain.
- Suggested Action:
  Add fallback counters to dashboards or export trace summaries for analysis.

## 3. Closed Or Mitigated Issues

### KI-M1 Invalid JSON Breaks Structured Flows

- Status: Mitigated
- Notes:
  `JSON Guard` plus fallback logic now covers tax and memory callback paths.

### KI-M2 High-Risk Tax Match Cannot Be Escalated

- Status: Mitigated
- Notes:
  `tax_matcher` and `tax_risk` now support high-risk cloud review behavior.

### KI-M3 Memory Callback Failure Can Abort Useful Output

- Status: Mitigated
- Notes:
  Clause audit and flush callbacks now support fallback logic, and flush still keeps a safe degraded text fallback.

## 4. Exit Criteria For Closing Phase 4

Phase 4 can be considered fully closed when all items below are satisfied:

- `TC-001` passes with real local services
- `TC-007B` passes with real long documents
- `TC-008` is executed with a full regression result log
- Known fallback ratio is measured and reviewed
