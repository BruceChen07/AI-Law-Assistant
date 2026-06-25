# Edge LLM CPU Local Deployment Runbook

## 1. Purpose

This runbook describes how to prepare, start, validate, and operate the CPU-only local edge LLM deployment for this repository.

Scope:

- Main local model service
- Small local model service
- Cloud fallback connectivity check
- Regression execution

This document is the phase 4 delivery runbook. It assumes the repository uses Ollama as the only local LLM runtime.

## 2. Target Topology

- Main model endpoint: `http://127.0.0.1:11434/v1`
- Small model endpoint: `http://127.0.0.1:11434/v1`
- Cloud fallback endpoint: configured by `llm_config.api_base`
- App backend: `http://127.0.0.1:8000`
- App frontend: `http://127.0.0.1:5173`

Recommended model mapping:

- Main model: `qwen3.6:27b`
- Small model: `llama3.2:3b`
- Cloud fallback: `gpt-4o-mini` or compatible OpenAI-style provider

## 3. Prerequisites

### 3.1 Hardware

- CPU: at least 16 physical cores recommended
- RAM: at least 64GB for local main model validation
- Storage: NVMe SSD strongly recommended

### 3.2 Software

- Python environment already used by this repository
- Ollama installed locally
- Ollama API reachable on `http://127.0.0.1:11434`

### 3.3 Repository Preparation

Run:

```bash
python .\bin\init.py
```

If needed, copy:

- `app/config.example.json` -> `app/config.json`

Then update:

- `llm_config`
- `local_llm.enabled`
- `local_llm.main_model`
- `local_llm.small_model`

## 4. Recommended Config

Key settings in `app/config.json`:

```json
{
  "llm_config": {
    "provider": "openai_compatible",
    "api_base": "https://api.openai.com/v1",
    "model": "gpt-4o-mini"
  },
  "local_llm": {
    "enabled": true,
    "routing_enabled": true,
    "cloud_fallback_enabled": true,
    "main_model": {
      "provider": "ollama",
      "api_base": "http://127.0.0.1:11434/v1",
      "model": "qwen3.6:27b"
    },
    "small_model": {
      "provider": "ollama",
      "api_base": "http://127.0.0.1:11434/v1",
      "model": "llama3.2:3b"
    }
  }
}
```

Important execution controls:

- `local_llm.execution.fallback_on_error`
- `local_llm.execution.fallback_on_invalid_json`
- `local_llm.execution.tax_match_max_workers`
- `local_llm.execution.tax_risk_max_workers`
- `local_llm.execution.memory_clause_force_cloud_for_priority`
- `local_llm.execution.memory_flush_force_cloud`

## 5. Local Model Startup

Start Ollama and ensure required models are installed:

```bash
python .\bin\start-local-llm-servers.py
python .\bin\download-local-llm-models.py
```

Optional manual verification:

```bash
ollama list
```

Notes:

- Both local roles use the same Ollama API endpoint and are distinguished by model name.
- The application uses Ollama official runtime management and Ollama-compatible inference configuration.

## 6. App Startup

Start backend and frontend:

```bash
python .\bin\start-services.py
```

Stop them:

```bash
Use the PID information in `.runtime/services.pids.json` and terminate the processes manually if needed.
```

## 7. Validation Steps

### 7.1 Regression Only

Run:

```bash
python -m pytest tests/test_llm_router.py tests/test_llm_local_mode.py tests/test_json_guard.py tests/test_local_llm_fallback.py tests/test_tax_contract_parser.py tests/test_tax_matcher.py tests/test_tax_risk.py tests/test_memory_pipeline_fallback.py tests/test_contract_audit_memory_mode.py tests/test_contract_audit.py
```

Expected output:

- Markdown report generated at `plan/edge-llm-cpu-local-regression-report.md`
- Pytest exit code `0`

### 7.2 Regression With Local Smoke

Run:

```bash
python .\bin\start-local-llm-servers.py
python .\bin\download-local-llm-models.py
python -m pytest tests/test_llm_router.py tests/test_llm_local_mode.py tests/test_json_guard.py tests/test_local_llm_fallback.py tests/test_tax_contract_parser.py tests/test_tax_matcher.py tests/test_tax_risk.py tests/test_memory_pipeline_fallback.py tests/test_contract_audit_memory_mode.py tests/test_contract_audit.py
```

Optional cloud validation:

```bash
python .\bin\apply-local-llm-config.py
```

## 8. Acceptance Checklist

- `TC-001`: local main model endpoint reachable
- `TC-002`: route selection tests pass
- `TC-004`: JSON guard tests pass
- `TC-005`: fallback and cloud review tests pass
- `TC-007`: memory fallback tests pass
- `TC-008`: full regression passes

## 9. Operations Guidance

- Default to local small model for extraction and tax match tasks
- Keep cloud fallback enabled until real local smoke and long-document pressure tests pass
- Keep `tax_match_max_workers` and `tax_risk_max_workers` conservative on CPU-only machines
- Turn on `memory_clause_force_cloud_for_priority` only if high-priority clauses show regression during validation

## 10. Rollback

If local edge mode is unstable:

- Set `local_llm.enabled` to `false`
- Keep `llm_config` pointing to the cloud endpoint
- Re-run:

```bash
python -m pytest tests/test_llm_router.py tests/test_llm_local_mode.py tests/test_json_guard.py tests/test_local_llm_fallback.py tests/test_tax_contract_parser.py tests/test_tax_matcher.py tests/test_tax_risk.py tests/test_memory_pipeline_fallback.py tests/test_contract_audit_memory_mode.py tests/test_contract_audit.py
```

## 11. Deliverables Produced In Phase 4-5

- Python startup helpers: `bin/init.py`、`bin/start-services.py`
- Ollama helpers: `bin/start-local-llm-servers.py`、`bin/download-local-llm-models.py`、`bin/apply-local-llm-config.py`
- Regression script: `bin/run-edge-llm-regression.ps1`
- Regression report: `plan/edge-llm-cpu-local-regression-report.md`
- Known issues list: `plan/edge-llm-cpu-local-known-issues.md`
- This runbook: `plan/edge-llm-cpu-local-deployment-runbook.md`
