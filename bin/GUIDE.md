# bin/ Scripts Guide

This document classifies every script under `bin/`, explains recommended startup flows for different deployment scenarios, and flags redundant or deprecated scripts.

---

## Quick Answer: Fresh Deployment

| Scenario | Command(s) |
|---|---|
| **Minimal Dev Setup** (no local LLM) | `python bin/init.py` → `python bin/create_admin.py` → `python bin/start-services.py` |
| **Enterprise Offline — One-Click** | `python bin/deploy_desktop.py` (handles EVERYTHING) |
| **Enterprise Offline — Step-by-Step** | `python bin/init.py` → `python bin/start-local-llamacpp-stack.py` → `python bin/apply-local-llm-config.py --provider llama_cpp --small-provider llama_cpp` → `python bin/start-services.py` |

---

## Script Classification

### 1. Core Service Startup & Initialization

| Script | Purpose | Standalone? |
|---|---|---|
| **`init.py`** | Initialize repo: copy `config.example.json` → `config.json`, create required directories, run `app.main --init` to bootstrap DB. Use this for fresh checkout. | ✅ Yes |
| `init.ps1` | PowerShell variant of `init.py`. Less feature-rich (no `embedding_profiles` handling). | ⚠️ Redundant — use `init.py` |
| **`start-services.py`** | Start backend (`python -m app.main`) + frontend (`npm run dev`) with PID tracking, auto venv/npm detection, log output. | ✅ Yes |
| `start-services.ps1` | PowerShell variant of `start-services.py`. Opens separate PS windows per process, less robust PID management. | ⚠️ Redundant — use `start-services.py` |
| **`stop-services.ps1`** | Stop backend + frontend via PID file (`.runtime/services.pids.json`), with optional port-kill fallback. | ✅ Yes |

> **Recommendation**: `init.py` and `start-services.py` are the canonical versions. The `.ps1` variants are legacy wrappers and can be ignored.

### 2. Local LLM Runtime Startup (llama.cpp — Current Path)

| Script | Purpose | Standalone? |
|---|---|---|
| **`start-local-llamacpp-server.py`** | Launch a single llama.cpp server for edge-only inference. Auto-detects `llama-server.exe`, validates model path, waits for readiness. | ✅ Yes |
| **`start-local-llamacpp-stack.py`** | Launch dual llama.cpp runtimes (main model on port 18080 + small model on port 18081). Calls `start-local-llamacpp-server.py` twice. This is the **recommended path** for enterprise offline deployments. | ✅ Yes |

### 3. Local LLM Runtime Startup (Ollama — Legacy Path)

| Script | Purpose | Standalone? |
|---|---|---|
| `start-local-llm-servers.py` | Start Ollama service, ensure main + small models are pulled. | ⚠️ Deprecated — project policy is now "pure llama.cpp, no Ollama dependency" |
| `download-local-llm-models.py` | Validate Ollama models are installed (verify-only in offline mode). | ⚠️ Deprecated — only works with Ollama; GGUF files are now staged directly from the artifact repository |

### 4. Local LLM Configuration

| Script | Purpose | Standalone? |
|---|---|---|
| **`apply-local-llm-config.py`** | Enable/disable `local_llm` in `config.json`. Sets provider (`llama_cpp` or `ollama`), model names, API base URLs, network policy (`offline_strict`). Run **after** starting llama.cpp runtimes, **before** starting the app backend. | ✅ Yes |

### 5. Deployment & Asset Management

| Script | Purpose | Standalone? |
|---|---|---|
| **`deploy_desktop.py`** | **One-shot desktop deployment entrypoint.** Orchestrates: env detection → venv creation → pip/npm install → `init.py` → download assets → verify assets → apply local config → start llama.cpp stack → start backend → start frontend → monitor. This is the "deploy everything" script. | ✅ Yes |
| `deploy_desktop_lib.py` | Shared library for `deploy_desktop.py`, `download_deployment_assets.py`, etc. Not meant to be run directly. | ❌ Library |
| `detect_deployment_env.py` | Detect OS, CPU, GPU, installed tooling; recommend llama.cpp build profile (CUDA/ROCm/Metal/CPU). | ✅ Yes |
| `download_deployment_assets.py` | Download GGUF models + llama.cpp runtime bundles from the deployment manifest, with retry, resume, SHA256 verification. | ✅ Yes |

### 6. Verification & Validation

| Script | Purpose | Standalone? |
|---|---|---|
| **`validate-local-llamacpp-runtime.py`** | Comprehensive runtime cutover validation: config correctness checks, endpoint probes (`/v1/models`), backend `/health`, chat smoke test. | ✅ Yes |
| `validate_offline_compliance.py` | Scan codebase for forbidden patterns (public cloud endpoints, model hubs, cloud fallback logic). CI safety net. | ✅ Yes |
| `verify_deployment_assets.py` | Verify GGUF models + llama.cpp bundles against manifest SHA256 hashes. | ✅ Yes |
| `verify_llamacpp_bundle.py` | Verify llama.cpp binary: file exists, SHA256 matches, `--help` runs, optional git source checkout verification. | ✅ Yes |
| **`ensure_local_models.py`** | Validate all model assets (embedding, reranker, translation) exist locally according to config. | ✅ Yes |
| `verify_ocr_env.py` | Detect OCR dependencies and optionally benchmark engines. | ✅ Yes |

### 7. User Management

| Script | Purpose | Standalone? |
|---|---|---|
| **`create_admin.py`** | Create admin user (or promote existing user to admin). Default: `admin`/`admin123`. | ✅ Yes |
| **`create_user.py`** | Create a regular user (`--role user`). | ✅ Yes |
| **`list_users.py`** | List all users or filter by username. | ✅ Yes |
| **`user_modify.py`** | Modify user fields: role, password, email, activate/deactivate. | ✅ Yes |
| `debug_jwt.py` | Debug JWT authentication flow (login, decode token, test admin API). | ✅ Yes |

### 8. Model Preparation (Offline/Enterprise)

| Script | Purpose | Standalone? |
|---|---|---|
| `download_embedding_model.py` | Copy embedding ONNX model + tokenizer files from a pre-staged local source directory to `models/embedding/`. Updates `config.json` accordingly. | ✅ Yes |
| `convert_model.py` | Convert a HuggingFace embedding model to ONNX format locally (requires `transformers`, `torch`). | ✅ Yes |

### 9. Maintenance & Utilities

| Script | Purpose | Standalone? |
|---|---|---|
| `cleanup_regulations.py` | Delete regulation data from DB and vector stores by job ID or all at once. | ✅ Yes |
| `rebuild_fts.py` | Rebuild full-text search (FTS) index from articles. | ✅ Yes |
| `memory_demo.py` | Demo script for the memory system (indexing, hybrid search, contract audit with fake LLM). | ✅ Yes |
| `validate_memory_report.py` | Validate memory report citations against a catalog. | ✅ Yes |

### 10. Test & Benchmark

| Script | Purpose | Standalone? |
|---|---|---|
| `test_qwen_ai.py` | Simple chat completion test against a Qwen model endpoint. | ✅ Yes |
| `generate_test_report.py` | Run `pytest` and emit a JSON report. | ✅ Yes |
| `run-edge-llm-regression.ps1` | Run edge LLM regression test suite (10 test files) + optional smoke checks, output Markdown report. | ✅ Yes |
| `benchmark-ollama-vs-llamacpp.py` | Benchmark Ollama vs llama.cpp (latency, throughput, memory, GPU) and emit comparison JSON. | ✅ Yes |

### 11. OCR Installation

| Script | Purpose | Platform |
|---|---|---|
| `install_ocr_windows.bat` | Install system-level OCR dependencies: Tesseract OCR + Poppler (pdftoppm), plus Python `mineru` package. | Windows |
| `install_ocr_deps_windows.bat` | Wrapper that calls `install_ocr_windows.bat` + installs Python OCR packages (pytesseract, pdf2image, pillow, pypdf, mineru). | Windows |
| `install_ocr_deps.sh` | Install OCR system + Python dependencies. | Linux |
| `install_ocr_macos.sh` | Install OCR system + Python dependencies. | macOS |

> **Note**: `install_ocr_deps_windows.bat` calls `install_ocr_windows.bat` internally, then adds pip package installs. For a full Windows OCR setup, run `install_ocr_deps_windows.bat` directly.

---

## Redundancy & Deprecation Summary

| Redundant Script | Replacement | Reason |
|---|---|---|
| `init.ps1` | **`init.py`** | Same function; `.py` version handles `embedding_profiles` correctly, `.ps1` does not |
| `start-services.ps1` | **`start-services.py`** | Same function; `.py` version has better auto-detection, PID tracking, and log management |
| `start-local-llm-servers.py` | **`start-local-llamacpp-stack.py`** | Ollama is the legacy runtime; project policy now mandates "pure llama.cpp, no Ollama dependency" |
| `download-local-llm-models.py` | **`download_deployment_assets.py`** | Only validates Ollama models; GGUF models are staged from the artifact repository via the manifest |

---

## Recommended Startup Flows

### Flow A: Minimal Development Setup

For quick local development **without** local LLM (relies on cloud API configured in `config.json`).

```powershell
# 1. Initialize repository (creates config.json, DB, directories)
python bin/init.py

# 2. Create admin user
python bin/create_admin.py

# 3. Start backend + frontend
python bin/start-services.py
```

Then open `http://localhost:5173` in your browser and log in with `admin` / `admin123`.

### Flow B: Enterprise Offline — One-Click Desktop Deploy

Does **everything** automatically. Requires: Python 3.10+, Git 2.40+, CMake 3.20+, Node 22+, npm.

```powershell
python bin/deploy_desktop.py
```

This single command:
1. Detects your environment (OS, CPU, GPU)
2. Creates Python virtual environment (`app/.venv`)
3. Installs backend pip dependencies
4. Installs frontend npm dependencies
5. Runs repository initialization (`init.py`)
6. Downloads GGUF models + llama.cpp runtime (from manifest)
7. Verifies all assets (SHA256)
8. Verifies llama.cpp binary
9. Applies local LLM configuration (`apply-local-llm-config.py`)
10. Starts dual llama.cpp runtimes (main on :18080, small on :18081)
11. Starts backend (:8000) and frontend (:5173)
12. Monitors service health for 5 minutes with auto-restart

### Flow C: Enterprise Offline — Step-by-Step

When you need fine-grained control over each step:

```powershell
# 1. Initialize
python bin/init.py

# 2. Start llama.cpp dual runtimes (wait for models to load)
python bin/start-local-llamacpp-stack.py

# 3. Apply local LLM config (enables offline_strict mode)
python bin/apply-local-llm-config.py --provider llama_cpp --small-provider llama_cpp

# 4. Start backend + frontend
python bin/start-services.py

# 5. Verify everything is healthy
python bin/validate-local-llamacpp-runtime.py
```

### Flow D: Deployment Validation (Post-Deployment)

After deployment, run these to confirm correctness:

```powershell
# Validate llama.cpp runtime readiness (config + endpoints + chat smoke)
python bin/validate-local-llamacpp-runtime.py

# Verify offline compliance (no cloud API references leaked into code)
python bin/validate_offline_compliance.py

# Verify all model assets (embedding/reranker/translation) exist locally
python bin/ensure_local_models.py

# Verify deployment assets against manifest SHA256
python bin/verify_deployment_assets.py
```

---

## Architecture Decision: Why llama.cpp over Ollama

The project has **migrated from Ollama to pure llama.cpp** for enterprise offline deployments:

- **`start-local-llm-servers.py`** (Ollama) and **`download-local-llm-models.py`** are **legacy/deprecated**.
- **`start-local-llamacpp-stack.py`** is the current recommended runtime launcher.
- **`apply-local-llm-config.py`** defaults to `--provider llama_cpp`.
- The deployment manifest (`deploy/desktop-deployment.manifest.json`) provides GGUF models directly — no Ollama registry needed.

---

## Internals Reference

- DB schema and helpers: `app/core/database.py`
- Password hashing and auth: `app/core/auth.py`
- Admin permission check: `app/api/dependencies.py` (requires `role == 'admin'`)
- Configuration resolution: `app/core/config.py`
- Deployment manifest: `deploy/desktop-deployment.manifest.json`
- llama.cpp runtime: `third_party/llama.cpp/bin-win-cpu-x64/`
