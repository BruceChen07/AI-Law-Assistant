# Edge LLM CPU Local Deployment Runbook

## 1. Purpose

This runbook describes how to prepare, start, validate, and operate the CPU-only local edge LLM deployment for this repository.

Scope:

- Main local model service
- Small local model service
- Cloud fallback connectivity check
- Regression execution

This document is the phase 4 delivery runbook. It does not claim that the target machine already has the required GGUF files or local model servers installed.

## 2. Target Topology

- Main model endpoint: `http://127.0.0.1:8011/v1`
- Small model endpoint: `http://127.0.0.1:8012/v1`
- Cloud fallback endpoint: configured by `llm_config.api_base`
- App backend: `http://127.0.0.1:8000`
- App frontend: `http://127.0.0.1:5173`

Recommended model mapping:

- Main model: `qwen3.6-27b-q4`
- Small model: `llama-3.2-3b-q4`
- Cloud fallback: `gpt-4o-mini` or compatible OpenAI-style provider

## 3. Prerequisites

### 3.1 Hardware

- CPU: at least 16 physical cores recommended
- RAM: at least 64GB for local main model validation
- Storage: NVMe SSD strongly recommended

### 3.2 Software

- Windows PowerShell 5+
- Python environment already used by this repository
- Local OpenAI-compatible server, such as `llama.cpp server`
- Model weights prepared on disk

### 3.3 Repository Preparation

Run:

```powershell
.\bin\init.ps1
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
      "provider": "openai_compatible",
      "api_base": "http://127.0.0.1:8011/v1",
      "model": "qwen3.6-27b-q4"
    },
    "small_model": {
      "provider": "openai_compatible",
      "api_base": "http://127.0.0.1:8012/v1",
      "model": "llama-3.2-3b-q4"
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

Example placeholder flow for local model services:

```powershell
# Main model example
llama-server.exe `
  -m D:\models\qwen3.6-27b-q4.gguf `
  --host 127.0.0.1 `
  --port 8011 `
  -c 8192 `
  -ngl 0
```

```powershell
# Small model example
llama-server.exe `
  -m D:\models\llama-3.2-3b-q4.gguf `
  --host 127.0.0.1 `
  --port 8012 `
  -c 4096 `
  -ngl 0
```

Notes:

- The exact executable and flags depend on the local serving framework you choose.
- This repository only requires the server to expose an OpenAI-compatible `/v1/chat/completions` interface.

## 6. App Startup

Start backend and frontend:

```powershell
.\bin\start-services.ps1
```

Stop them:

```powershell
.\bin\stop-services.ps1
```

## 7. Validation Steps

### 7.1 Regression Only

Run:

```powershell
.\bin\run-edge-llm-regression.ps1
```

Expected output:

- Markdown report generated at `plan/edge-llm-cpu-local-regression-report.md`
- Pytest exit code `0`

### 7.2 Regression With Local Smoke

Run:

```powershell
.\bin\run-edge-llm-regression.ps1 `
  -IncludeLocalSmoke `
  -LocalMainApiBase "http://127.0.0.1:8011/v1" `
  -LocalSmallApiBase "http://127.0.0.1:8012/v1"
```

Optional cloud validation:

```powershell
.\bin\run-edge-llm-regression.ps1 `
  -IncludeLocalSmoke `
  -CloudApiBase "https://api.openai.com/v1"
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

```powershell
.\bin\run-edge-llm-regression.ps1
```

## 11. Deliverables Produced In Phase 4

- Regression script: `bin/run-edge-llm-regression.ps1`
- Regression report: `plan/edge-llm-cpu-local-regression-report.md`
- Known issues list: `plan/edge-llm-cpu-local-known-issues.md`
- This runbook: `plan/edge-llm-cpu-local-deployment-runbook.md`
